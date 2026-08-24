from __future__ import annotations

from typing import Final, TypedDict

from scripts.release_green_check import failure_reason, release_suite_ids

RELEASE_WORKFLOW: Final = ".github/workflows/release.yml"
REQUIRED_CHECK: Final = "Offline test suite"


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


def check_run(name: str, suite_id: int, conclusion: str | None) -> CheckRun:
    return {
        "name": name,
        "status": "completed" if conclusion is not None else "in_progress",
        "conclusion": conclusion,
        "check_suite": {"id": suite_id},
    }


def workflow_run(path: str, suite_id: int) -> WorkflowRun:
    return {"path": path, "check_suite_id": suite_id}


def test_self_pending_release_job_does_not_block_the_release() -> None:
    # Given
    checks = [
        check_run(REQUIRED_CHECK, 10, "success"),
        check_run("tag", 20, None),
    ]
    workflow_runs = [workflow_run(RELEASE_WORKFLOW, 20)]

    # When
    reason = failure_reason(checks, release_suite_ids(workflow_runs), REQUIRED_CHECK)

    # Then
    assert reason is None


def test_prior_failed_release_attempt_does_not_lock_out_a_retry() -> None:
    # Given
    checks = [
        check_run(REQUIRED_CHECK, 10, "success"),
        check_run("tag", 19, "failure"),
        check_run("tag", 20, None),
    ]
    workflow_runs = [
        workflow_run(RELEASE_WORKFLOW, 19),
        workflow_run(RELEASE_WORKFLOW, 20),
    ]

    # When
    reason = failure_reason(checks, release_suite_ids(workflow_runs), REQUIRED_CHECK)

    # Then
    assert reason is None


def test_red_non_release_ci_blocks_the_release() -> None:
    # Given
    checks = [
        check_run(REQUIRED_CHECK, 10, "success"),
        check_run("Lint", 11, "failure"),
    ]

    # When
    reason = failure_reason(checks, set(), REQUIRED_CHECK)

    # Then
    assert reason == "::error::red checks on this commit: Lint"


def test_pending_non_release_ci_blocks_the_release() -> None:
    # Given
    checks = [
        check_run(REQUIRED_CHECK, 10, "success"),
        check_run("Integration tests", 11, None),
    ]

    # When
    reason = failure_reason(checks, set(), REQUIRED_CHECK)

    # Then
    assert reason == "::error::checks still running: Integration tests — wait for them"


def test_missing_required_check_fails_closed() -> None:
    # Given
    checks = [check_run("Lint", 11, "success")]

    # When
    reason = failure_reason(checks, set(), REQUIRED_CHECK)

    # Then
    assert reason == (
        "::error::required check 'Offline test suite' did not succeed on this commit"
    )


def test_same_name_non_release_check_is_not_excluded() -> None:
    # Given
    checks = [
        check_run(REQUIRED_CHECK, 10, "success"),
        check_run("tag", 20, None),
        check_run("tag", 21, None),
    ]
    workflow_runs = [
        workflow_run(RELEASE_WORKFLOW, 20),
        workflow_run(".github/workflows/ci.yml", 21),
    ]

    # When
    reason = failure_reason(checks, release_suite_ids(workflow_runs), REQUIRED_CHECK)

    # Then
    assert reason == "::error::checks still running: tag — wait for them"


def test_unmapped_suite_remains_blocking() -> None:
    # Given
    checks = [
        check_run(REQUIRED_CHECK, 10, "success"),
        check_run("tag", 99, None),
    ]
    workflow_runs = [workflow_run(RELEASE_WORKFLOW, 20)]

    # When
    reason = failure_reason(checks, release_suite_ids(workflow_runs), REQUIRED_CHECK)

    # Then
    assert reason == "::error::checks still running: tag — wait for them"


def test_empty_workflow_mapping_fails_closed_on_pending_release_named_check() -> None:
    # Given
    checks = [
        check_run(REQUIRED_CHECK, 10, "success"),
        check_run("tag", 20, None),
    ]
    workflow_runs: list[WorkflowRun] = []

    # When
    reason = failure_reason(checks, release_suite_ids(workflow_runs), REQUIRED_CHECK)

    # Then
    assert reason == "::error::checks still running: tag — wait for them"


def test_red_checks_take_precedence_over_pending_checks() -> None:
    # Given
    checks = [
        check_run(REQUIRED_CHECK, 10, "success"),
        check_run("Lint", 11, "failure"),
        check_run("Integration tests", 12, None),
    ]

    # When
    reason = failure_reason(checks, set(), REQUIRED_CHECK)

    # Then
    assert reason == "::error::red checks on this commit: Lint"
