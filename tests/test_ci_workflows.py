import json
from pathlib import Path
from typing import Final, TypedDict

import yaml
from pydantic import TypeAdapter

WorkflowConcurrency = TypedDict(
    "WorkflowConcurrency",
    {"group": str, "cancel-in-progress": bool | str},
)


class WorkflowStep(TypedDict, total=False):
    uses: str


class WorkflowJob(TypedDict, total=False):
    steps: list[WorkflowStep]


class Workflow(TypedDict):
    jobs: dict[str, WorkflowJob]


class CiTestsWorkflow(Workflow):
    concurrency: WorkflowConcurrency


WORKFLOW_DIRECTORY: Final = Path(".github/workflows")
TESTS_WORKFLOW: Final = WORKFLOW_DIRECTORY / "tests.yml"
CHECKOUT_V5_SHA: Final = "fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09"
WORKFLOW_ADAPTER: Final = TypeAdapter(Workflow)
TESTS_WORKFLOW_ADAPTER: Final = TypeAdapter(CiTestsWorkflow)


def _load_workflow(path: Path) -> Workflow:
    return WORKFLOW_ADAPTER.validate_json(json.dumps(yaml.safe_load(path.read_text())))


def _load_tests_workflow() -> CiTestsWorkflow:
    return TESTS_WORKFLOW_ADAPTER.validate_json(
        json.dumps(yaml.safe_load(TESTS_WORKFLOW.read_text()))
    )


def test_tests_workflow_only_supersedes_older_runs_for_the_same_pr() -> None:
    workflow = _load_tests_workflow()
    concurrency = workflow["concurrency"]

    assert concurrency["group"] == (
        "tests-${{ github.event_name }}-"
        "${{ github.event.pull_request.number || github.run_id }}"
    )
    assert concurrency["cancel-in-progress"] == (
        "${{ github.event_name == 'pull_request' }}"
    )


def test_every_checkout_step_uses_the_pinned_v5_commit() -> None:
    checkout_uses = [
        checkout_use
        for path in sorted(WORKFLOW_DIRECTORY.glob("*.yml"))
        for job in _load_workflow(path)["jobs"].values()
        for step in job.get("steps", [])
        if (checkout_use := step.get("uses")) is not None
        if checkout_use.startswith("actions/checkout@")
    ]

    assert checkout_uses
    assert set(checkout_uses) == {f"actions/checkout@{CHECKOUT_V5_SHA}"}
