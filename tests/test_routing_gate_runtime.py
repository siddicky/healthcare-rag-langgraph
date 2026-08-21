from __future__ import annotations

from pathlib import Path

import pytest

from evals.routing_gate_models import ArmName
from tests import routing_gate_cases as cases
from tests import routing_gate_publication_cases as publication_cases
from tests import routing_gate_runner_cases as runner_cases


def test_smoke_gate_publishes_linked_todo5_reports(tmp_path: Path) -> None:
    cases.assert_smoke_gate_publishes_linked_todo5_reports(tmp_path)


@pytest.mark.parametrize(
    ("lane", "expected"),
    [
        ("query", ("current+llm", "deterministic+llm", "tool+llm")),
        ("safety", ("current+llm", "current+semantic_router")),
    ],
)
def test_non_smoke_cli_runs_exact_arms_and_writes_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    lane: str,
    expected: tuple[ArmName, ...],
) -> None:
    runner_cases.assert_non_smoke_cli_runs_exact_arms_and_writes_report(
        tmp_path, monkeypatch, capsys, lane, expected
    )


def test_duplicate_arm_publication_identity_rejects_before_writes(
    tmp_path: Path,
) -> None:
    publication_cases.assert_duplicate_arm_publication_identity_rejects_before_writes(
        tmp_path
    )


def test_non_smoke_stage1_reject_suppresses_stage2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner_cases.assert_non_smoke_stage1_reject_suppresses_stage2(tmp_path, monkeypatch)


@pytest.mark.parametrize("lane", ["query", "safety"])
def test_real_subprocess_protocol_runs_with_fake_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    runner_cases.assert_real_subprocess_protocol_runs_with_fake_adapter(
        tmp_path, monkeypatch, lane
    )


@pytest.mark.parametrize("lane", ["query", "safety"])
@pytest.mark.parametrize("failure", ["missing", "contaminated"])
def test_cross_phase_failure_is_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
    failure: str,
) -> None:
    runner_cases.assert_cross_phase_failure_is_error(
        tmp_path, monkeypatch, lane, failure
    )


@pytest.mark.parametrize("lane", ["query", "safety"])
def test_requested_settings_mismatch_is_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    runner_cases.assert_requested_settings_mismatch_is_error(
        tmp_path, monkeypatch, lane
    )


def test_calibration_rows_reject_before_writes(tmp_path: Path) -> None:
    publication_cases.assert_calibration_rows_reject_before_writes(tmp_path)


@pytest.mark.parametrize("failure_call", [1, 4, 8, 9, 12, 16])
def test_batch_publication_failure_preserves_prior_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_call: int,
) -> None:
    publication_cases.assert_batch_publication_failure_preserves_prior_set(
        tmp_path, monkeypatch, failure_call
    )


def test_batch_rollback_failure_retains_recoverable_backups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication_cases.assert_batch_rollback_failure_retains_recoverable_backups(
        tmp_path, monkeypatch
    )
