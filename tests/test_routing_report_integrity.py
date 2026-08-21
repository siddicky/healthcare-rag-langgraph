from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

import evals.routing_report_io as report_io
from evals.routing_dataset_models import Action, SafetyCategory
from evals.routing_evaluators import RoutingRecord
from evals.routing_provenance import (
    ArmEnvironment,
    ArtifactHashes,
    ExperimentRows,
    RoutingProvenance,
)
from evals.routing_report import (
    RoutingReportRequest,
    RoutingReportRow,
    StageUsage,
    write_routing_report,
)
from evals.routing_report_io import (
    ReportError,
    ReportInterrupted,
    recover_report_files,
)


@dataclass(frozen=True, slots=True)
class RollbackFailure:
    call: int
    kind: Literal["oserror", "interrupt"]


def _record(**updates: float) -> RoutingRecord:
    base = RoutingRecord(
        expected_action=Action.DIRECT,
        effective_action=Action.DIRECT,
        model_action=Action.DIRECT,
        expected_safety_category=SafetyCategory.OUT_OF_SCOPE,
        observed_safety_category=SafetyCategory.OUT_OF_SCOPE,
        boundary_hit=False,
        classifier_latency_s=0.1,
        classifier_cost_usd=0.01,
        classifier_llm_calls=1,
        classifier_embedding_calls=0,
        whole_latency_s=0.2,
        whole_cost_usd=0.04,
        whole_llm_calls=3,
        whole_embedding_calls=0,
        pipeline_error=False,
        classifier_fallback=False,
        evaluator_error=False,
    )
    return base.model_copy(update=updates)


def _request(
    output_dir: Path, records: tuple[RoutingRecord, RoutingRecord] | None = None
) -> RoutingReportRequest:
    digest = "a" * 64
    row_ids = ("core-1", "holdout-1")
    hashes = ArtifactHashes(**{name: digest for name in ArtifactHashes.model_fields})
    provenance = RoutingProvenance(
        git_sha="1" * 40,
        git_dirty=False,
        arm_env=ArmEnvironment(
            HC_RAG_QUERY_RESPONSE_ARM="tool", HC_RAG_SAFETY_CLASSIFIER="llm"
        ),
        rows=ExperimentRows(
            local_row_count=2,
            local_row_ids=row_ids,
            langsmith_row_count=2,
            langsmith_row_ids=row_ids,
        ),
        experiment_name="routing-integrity",
        experiment_url="https://smith.langchain.com/o/example/projects/p/integrity",
        hashes=hashes,
        semantic_router_version="0.1.16",
        encoder_model="text-embedding-3-small",
        judge_model="gpt-5.4-mini",
        repetitions=2,
        concurrency=1,
    )
    values = records or (_record(), _record())
    rows = tuple(
        RoutingReportRow(
            row_id=row_id,
            lane="query_response",
            category="benign_social",
            split="core",
            routing=record,
        )
        for row_id, record in zip(row_ids, values, strict=True)
    )
    return RoutingReportRequest(
        provenance=provenance,
        rows=rows,
        stage_usage={"safety_gate": StageUsage(calls=2, tokens=2, cost_usd=0.1)},
        output_dir=output_dir,
    )


@pytest.mark.parametrize(
    ("field", "phase"),
    [("classifier_cost_usd", "serialization"), ("classifier_latency_s", "aggregate")],
)
def test_routing_report_when_derived_metric_overflows_rejects_typed(
    tmp_path: Path, field: str, phase: str
) -> None:
    # Given
    records = (_record(**{field: 1e308}), _record(**{field: 1e308}))

    # When / Then
    with pytest.raises(ReportError, match=phase):
        _ = write_routing_report(_request(tmp_path, records))
    assert not tuple(tmp_path.iterdir())


def test_routing_report_when_rows_are_permuted_writes_identical_artifacts(
    tmp_path: Path,
) -> None:
    # Given
    first = _request(tmp_path / "first")
    second = first.model_copy(
        update={"rows": tuple(reversed(first.rows)), "output_dir": tmp_path / "second"}
    )

    # When / Then
    assert [path.read_bytes() for path in write_routing_report(first)] == [
        path.read_bytes() for path in write_routing_report(second)
    ]


@pytest.mark.parametrize("failure_call", [1, 2, 3, 4])
def test_routing_report_when_replace_fails_preserves_prior_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_call: int
) -> None:
    # Given
    request = _request(tmp_path)
    paths = write_routing_report(request)
    before = tuple(path.read_bytes() for path in paths)
    original = report_io.replace_report_file
    calls = 0

    def fail_once(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("injected replace failure")
        original(source, target)

    monkeypatch.setattr(report_io, "replace_report_file", fail_once)

    # When / Then
    with pytest.raises(ReportError, match="publish"):
        _ = write_routing_report(request)
    assert tuple(path.read_bytes() for path in paths) == before
    assert not tuple(tmp_path.glob(".*.tmp"))
    assert not tuple(tmp_path.glob(".*.backup"))


@pytest.mark.parametrize("failure_call", [1, 2])
def test_routing_report_when_temp_write_fails_preserves_prior_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_call: int
) -> None:
    # Given
    request = _request(tmp_path)
    paths = write_routing_report(request)
    before = tuple(path.read_bytes() for path in paths)
    original = report_io.write_report_temp
    calls = 0

    def fail_once(target: Path, content: str) -> Path:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("injected temp failure")
        return original(target, content)

    monkeypatch.setattr(report_io, "write_report_temp", fail_once)

    # When / Then
    with pytest.raises(ReportError, match="temp_write"):
        _ = write_routing_report(request)
    assert tuple(path.read_bytes() for path in paths) == before
    assert not tuple(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("method", ["temp", "replace"])
def test_routing_report_when_cancelled_preserves_prior_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: Literal["temp", "replace"],
) -> None:
    # Given
    request = _request(tmp_path)
    paths = write_routing_report(request)
    before = tuple(path.read_bytes() for path in paths)
    calls = 0

    original_replace = report_io.replace_report_file

    def cancel_replace(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise KeyboardInterrupt
        original_replace(source, target)

    if method == "temp":
        original_temp = report_io.write_report_temp

        def cancel_temp(target: Path, content: str) -> Path:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt
            return original_temp(target, content)

        monkeypatch.setattr(report_io, "write_report_temp", cancel_temp)
    else:
        monkeypatch.setattr(report_io, "replace_report_file", cancel_replace)

    # When / Then
    with pytest.raises(KeyboardInterrupt):
        _ = write_routing_report(request)
    assert tuple(path.read_bytes() for path in paths) == before
    assert not tuple(tmp_path.glob(".*.tmp"))
    assert not tuple(tmp_path.glob(".*.backup"))


@pytest.mark.parametrize(
    "scenario",
    [
        RollbackFailure(5, "oserror"),
        RollbackFailure(6, "oserror"),
        RollbackFailure(5, "interrupt"),
        RollbackFailure(6, "interrupt"),
    ],
)
def test_routing_report_when_rollback_fails_retains_recoverable_backups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: RollbackFailure,
) -> None:
    # Given
    request = _request(tmp_path)
    paths = write_routing_report(request)
    before = tuple(path.read_bytes() for path in paths)
    original = report_io.replace_report_file
    calls = 0

    def fail_publish_and_restore(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("injected publish failure")
        if calls == scenario.call:
            if scenario.kind == "interrupt":
                raise KeyboardInterrupt
            raise OSError("injected rollback failure")
        original(source, target)

    monkeypatch.setattr(report_io, "replace_report_file", fail_publish_and_restore)
    expected_error = ReportInterrupted if scenario.kind == "interrupt" else ReportError

    # When
    with pytest.raises(expected_error) as caught:
        _ = write_routing_report(request)

    # Then
    recovery_paths = caught.value.recovery_paths
    assert recovery_paths
    assert all(path.exists() and path.read_bytes() in before for path in recovery_paths)
    assert all(path.name.endswith(".backup") for path in recovery_paths)
    assert not tuple(tmp_path.glob(".*.tmp"))
    monkeypatch.setattr(report_io, "replace_report_file", original)
    _ = recover_report_files(recovery_paths)
    assert tuple(path.read_bytes() for path in paths) == before
    assert not tuple(tmp_path.glob(".*.backup"))
