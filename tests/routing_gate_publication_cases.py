from __future__ import annotations

from pathlib import Path

import pytest

import evals.routing_report_io as report_io
from evals.routing_gate_publish import PublicationError, publish_gate
from evals.routing_report_io import ReportError, recover_report_files
from tests.routing_gate_cases import publication_request


def assert_duplicate_arm_publication_identity_rejects_before_writes(
    tmp_path: Path,
) -> None:
    request = publication_request(tmp_path)
    reports = tuple(
        report.model_copy(
            update={
                "provenance": report.provenance.model_copy(
                    update={
                        "experiment_name": "duplicate",
                        "experiment_url": "https://smith.langchain.com/duplicate",
                    }
                )
            }
        )
        for report in request.arm_reports
    )
    with pytest.raises(PublicationError, match="unique"):
        _ = publish_gate(request.model_copy(update={"arm_reports": reports}))
    assert not tuple(tmp_path.iterdir())


def assert_calibration_rows_reject_before_writes(tmp_path: Path) -> None:
    request = publication_request(tmp_path)
    first = request.arm_reports[0]
    calibration = first.rows[0].model_copy(update={"split": "calibration"})
    reports = (
        first.model_copy(update={"rows": (calibration,)}),
        *request.arm_reports[1:],
    )
    with pytest.raises(PublicationError, match="calibration"):
        _ = publish_gate(request.model_copy(update={"arm_reports": reports}))
    assert not tuple(tmp_path.iterdir())


def assert_batch_publication_failure_preserves_prior_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_call: int,
) -> None:
    request = publication_request(tmp_path)
    _ = publish_gate(request)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    original = report_io.replace_report_file
    calls = 0

    def fail_once(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("injected gate batch failure")
        original(source, target)

    monkeypatch.setattr(report_io, "replace_report_file", fail_once)
    with pytest.raises(ReportError):
        _ = publish_gate(request)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def assert_batch_rollback_failure_retains_recoverable_backups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = publication_request(tmp_path)
    _ = publish_gate(request)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    original = report_io.replace_report_file
    calls = 0

    def fail_publish_and_rollback(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls in {16, 17}:
            raise OSError("injected gate rollback failure")
        original(source, target)

    monkeypatch.setattr(report_io, "replace_report_file", fail_publish_and_rollback)
    with pytest.raises(ReportError, match="rollback") as caught:
        _ = publish_gate(request)
    assert caught.value.recovery_paths
    assert all(path.exists() for path in caught.value.recovery_paths)
    monkeypatch.setattr(report_io, "replace_report_file", original)
    _ = recover_report_files(caught.value.recovery_paths)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
