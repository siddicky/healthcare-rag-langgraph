from __future__ import annotations

import json
import os
from typing import Final, TypeAlias, TypedDict

DEFAULT_RELEASE_WORKFLOW_PATH: Final = ".github/workflows/release.yml"
RED_CONCLUSIONS: Final = frozenset(
    {"failure", "timed_out", "cancelled", "action_required"}
)
JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


class ReleasePayloadError(Exception):
    __slots__: tuple[str, str] = ("detail", "source")

    source: str
    detail: str

    def __init__(self, source: str, detail: str) -> None:
        self.source = source
        self.detail = detail
        super().__init__(f"invalid {source}: {detail}")


class CheckSuite(TypedDict):
    id: int


class CheckRun(TypedDict):
    name: str
    status: str
    conclusion: str | None
    check_suite: CheckSuite


class WorkflowRun(TypedDict):
    path: str
    check_suite_id: int


class ChecksPayload(TypedDict):
    check_runs: list[CheckRun]


class WorkflowRunsPayload(TypedDict):
    workflow_runs: list[WorkflowRun]


def _json_object(raw: str, source: str) -> dict[str, JsonValue]:
    objects: list[dict[str, JsonValue]] = []

    def record(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        objects.append(value)
        return value

    if not raw.lstrip().startswith("{"):
        raise ReleasePayloadError(source, "expected a JSON object")
    try:
        json.loads(raw, object_hook=record)
    except json.JSONDecodeError as error:
        raise ReleasePayloadError(source, error.msg) from error
    if not objects:
        raise ReleasePayloadError(source, "expected a JSON object")
    return objects[-1]


def _mapping(value: JsonValue, location: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ReleasePayloadError(location, "expected an object")
    return value


def _list(value: JsonValue, location: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ReleasePayloadError(location, "expected a list")
    return value


def _string(value: JsonValue, location: str) -> str:
    if not isinstance(value, str):
        raise ReleasePayloadError(location, "expected a string")
    return value


def _integer(value: JsonValue, location: str) -> int:
    if type(value) is not int:
        raise ReleasePayloadError(location, "expected an integer")
    return value


def _optional_string(value: JsonValue, location: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ReleasePayloadError(location, "expected a string or null")
    return value


def _check_run(value: JsonValue, index: int) -> CheckRun:
    location = f"CHECKS_JSON.check_runs[{index}]"
    run = _mapping(value, location)
    suite = _mapping(run.get("check_suite"), f"{location}.check_suite")
    return {
        "name": _string(run.get("name"), f"{location}.name"),
        "status": _string(run.get("status"), f"{location}.status"),
        "conclusion": _optional_string(
            run.get("conclusion"), f"{location}.conclusion"
        ),
        "check_suite": {
            "id": _integer(suite.get("id"), f"{location}.check_suite.id")
        },
    }


def _workflow_run(value: JsonValue, index: int) -> WorkflowRun:
    location = f"WORKFLOW_RUNS_JSON.workflow_runs[{index}]"
    run = _mapping(value, location)
    return {
        "path": _string(run.get("path"), f"{location}.path"),
        "check_suite_id": _integer(
            run.get("check_suite_id"), f"{location}.check_suite_id"
        ),
    }


def _checks_payload(raw: str) -> ChecksPayload:
    payload = _json_object(raw, "CHECKS_JSON")
    runs = _list(payload.get("check_runs"), "CHECKS_JSON.check_runs")
    return {"check_runs": [_check_run(run, index) for index, run in enumerate(runs)]}


def _workflow_runs_payload(raw: str) -> WorkflowRunsPayload:
    payload = _json_object(raw, "WORKFLOW_RUNS_JSON")
    runs = _list(
        payload.get("workflow_runs"), "WORKFLOW_RUNS_JSON.workflow_runs"
    )
    return {
        "workflow_runs": [
            _workflow_run(run, index) for index, run in enumerate(runs)
        ]
    }


def release_suite_ids(
    workflow_runs: list[WorkflowRun],
    release_workflow_path: str = DEFAULT_RELEASE_WORKFLOW_PATH,
) -> set[int]:
    return {
        run["check_suite_id"]
        for run in workflow_runs
        if run["path"] == release_workflow_path
    }


def failure_reason(
    check_runs: list[CheckRun],
    ignored_suite_ids: set[int],
    required_check: str,
) -> str | None:
    considered = [
        run
        for run in check_runs
        if run["check_suite"]["id"] not in ignored_suite_ids
    ]
    red = [
        run["name"]
        for run in considered
        if run["status"] == "completed"
        and run["conclusion"] in RED_CONCLUSIONS
    ]
    pending = [run["name"] for run in considered if run["status"] != "completed"]
    required_succeeded = any(
        run["name"] == required_check and run["conclusion"] == "success"
        for run in considered
    )

    if red:
        return f"::error::red checks on this commit: {', '.join(red)}"
    if pending:
        return f"::error::checks still running: {', '.join(pending)} — wait for them"
    if not required_succeeded:
        return (
            f"::error::required check '{required_check}' did not succeed on this commit"
        )
    return None


def _print_runs(check_runs: list[CheckRun], ignored_suite_ids: set[int]) -> None:
    considered = [
        run
        for run in check_runs
        if run["check_suite"]["id"] not in ignored_suite_ids
    ]
    ignored = [
        run for run in check_runs if run["check_suite"]["id"] in ignored_suite_ids
    ]

    print("Considered check runs:")
    for run in considered:
        print(_format_run(run))
    if not considered:
        print("  (none)")

    print("Ignored release-attempt check runs:")
    for run in ignored:
        print(_format_run(run))
    if not ignored:
        print("  (none)")


def _format_run(run: CheckRun) -> str:
    suite_id = run["check_suite"]["id"]
    return f"  {run['name']}: {run['status']}/{run['conclusion']} (check suite {suite_id})"


def main() -> int:
    try:
        checks_payload = _checks_payload(os.environ["CHECKS_JSON"])
        workflow_payload = _workflow_runs_payload(os.environ["WORKFLOW_RUNS_JSON"])
    except ReleasePayloadError as error:
        raise SystemExit(f"::error::{error}") from None
    required_check = os.environ["REQUIRED_CHECK"]
    release_workflow_path = os.environ.get(
        "RELEASE_WORKFLOW_PATH", DEFAULT_RELEASE_WORKFLOW_PATH
    )

    ignored_suite_ids = release_suite_ids(
        workflow_payload["workflow_runs"], release_workflow_path
    )
    check_runs = checks_payload["check_runs"]
    _print_runs(check_runs, ignored_suite_ids)

    reason = failure_reason(check_runs, ignored_suite_ids, required_check)
    if reason is not None:
        raise SystemExit(reason)

    print(f"required check '{required_check}' green, no red checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
