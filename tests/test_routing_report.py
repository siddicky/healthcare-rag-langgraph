from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.routing_dataset_models import Action, SafetyCategory
from evals.routing_evaluators import RoutingRecord
from evals.routing_provenance import (
    ArmEnvironment,
    ArtifactHashes,
    ExperimentRows,
    RoutingProvenance,
)
from evals.routing_report import (
    RoutingReportPayload,
    RoutingReportRequest,
    RoutingReportRow,
    StageUsage,
    build_routing_report_pair,
    write_routing_report,
)
from evals.routing_report_io import ReportError


def _record(*, classifier_latency: float, whole_latency: float) -> RoutingRecord:
    return RoutingRecord(
        expected_action=Action.DIRECT,
        effective_action=Action.DIRECT,
        model_action=Action.DIRECT,
        expected_safety_category=SafetyCategory.OUT_OF_SCOPE,
        observed_safety_category=SafetyCategory.OUT_OF_SCOPE,
        boundary_hit=False,
        classifier_latency_s=classifier_latency,
        classifier_cost_usd=0.01,
        classifier_llm_calls=1,
        classifier_embedding_calls=0,
        whole_latency_s=whole_latency,
        whole_cost_usd=0.04,
        whole_llm_calls=3,
        whole_embedding_calls=0,
        pipeline_error=False,
        classifier_fallback=False,
        evaluator_error=False,
    )


def _provenance() -> RoutingProvenance:
    digest = "a" * 64
    return RoutingProvenance(
        git_sha="1" * 40,
        git_dirty=False,
        arm_env=ArmEnvironment(
            HC_RAG_QUERY_RESPONSE_ARM="tool",
            HC_RAG_SAFETY_CLASSIFIER="llm",
        ),
        rows=ExperimentRows(
            local_row_count=2,
            local_row_ids=("core-1", "holdout-1"),
            langsmith_row_count=2,
            langsmith_row_ids=("core-1", "holdout-1"),
        ),
        experiment_name="routing-fixture",
        experiment_url="https://smith.langchain.com/o/example/projects/p/fixture",
        hashes=ArtifactHashes(
            code=digest,
            dataset=digest,
            multiturn=digest,
            prototypes=digest,
            thresholds=digest,
            evaluators=digest,
            prompts=digest,
            uv_lock=digest,
        ),
        semantic_router_version="0.1.16",
        encoder_model="text-embedding-3-small",
        judge_model="gpt-5.4-mini",
        repetitions=2,
        concurrency=1,
    )


def test_routing_report_when_rows_span_groups_round_trips_complete_payload(
    tmp_path: Path,
) -> None:
    # Given
    rows = (
        RoutingReportRow.model_validate(
            {
                "row_id": "core-1",
                "lane": "query_response",
                "category": "benign_social",
                "split": "core",
                "routing": _record(classifier_latency=0.1, whole_latency=0.8),
            }
        ),
        RoutingReportRow.model_validate(
            {
                "row_id": "holdout-1",
                "lane": "query_response",
                "category": "benign_social",
                "split": "holdout",
                "routing": _record(classifier_latency=0.3, whole_latency=1.2),
            }
        ),
    )
    request = RoutingReportRequest(
        provenance=_provenance(),
        rows=rows,
        stage_usage={"safety_gate": StageUsage(calls=2, tokens=120, cost_usd=0.02)},
        output_dir=tmp_path,
    )

    # When
    json_path, markdown_path = write_routing_report(request)
    payload = RoutingReportPayload.model_validate_json(json_path.read_text())
    markdown = markdown_path.read_text()

    # Then
    assert payload.provenance.git_sha == "1" * 40
    assert payload.provenance.git_dirty is False
    assert set(ArtifactHashes.model_fields) == {
        "code",
        "dataset",
        "multiturn",
        "prototypes",
        "thresholds",
        "evaluators",
        "prompts",
        "uv_lock",
    }
    overall = payload.aggregate.overall
    assert overall.classifier_only.latency_p50_s == 0.2
    assert overall.classifier_only.cost_total_usd == 0.02
    assert overall.whole_query.latency_p50_s == 1.0
    assert overall.whole_query.cost_total_usd == 0.08
    assert payload.stage_usage["safety_gate"] == StageUsage(
        calls=2, tokens=120, cost_usd=0.02
    )
    assert set(payload.aggregate.by_split) == {"core", "holdout"}
    assert set(payload.aggregate.by_lane) == {"query_response"}
    assert "## By lane" in markdown
    assert "## By category" in markdown
    assert "## By split" in markdown
    assert "Classifier-only" in markdown
    assert "Whole-query" in markdown
    assert "SYNTHETIC-PHI-CANARY" not in json_path.read_text()


def test_routing_report_when_stage_metric_is_nonfinite_rejects() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError, match="finite number"):
        _ = StageUsage(calls=1, tokens=1, cost_usd=float("nan"))


def test_routing_report_when_untrusted_telemetry_contains_raw_query_rejects() -> None:
    # Given
    row = {
        "row_id": "core-1",
        "lane": "query_response",
        "category": "benign_social",
        "split": "core",
        "routing": _record(classifier_latency=0.1, whole_latency=0.8),
        "raw_question": "SYNTHETIC-PHI-CANARY",
    }

    # When / Then
    with pytest.raises(ValidationError, match="Extra inputs"):
        _ = RoutingReportRow.model_validate(row)


def test_routing_report_builder_when_called_does_not_publish_targets(
    tmp_path: Path,
) -> None:
    # Given
    output_dir = tmp_path / "nonexistent" / "routing"
    rows = tuple(
        RoutingReportRow(
            row_id=row_id,
            lane="query_response",
            category="benign_social",
            split="core",
            routing=_record(classifier_latency=0.1, whole_latency=0.2),
        )
        for row_id in ("core-1", "holdout-1")
    )
    request = RoutingReportRequest(
        provenance=_provenance(),
        rows=rows,
        stage_usage={"safety_gate": StageUsage(calls=2, tokens=2, cost_usd=0.1)},
        output_dir=output_dir,
    )

    # When
    pair = build_routing_report_pair(request)

    # Then
    assert not output_dir.exists()
    assert not pair.json_path.exists()
    assert not pair.markdown_path.exists()
    payload = RoutingReportPayload.model_validate_json(pair.json_content)
    assert tuple(row.row_id for row in payload.rows) == ("core-1", "holdout-1")
    published = write_routing_report(request)
    assert tuple(path.read_text() for path in published) == (
        pair.json_content,
        pair.markdown_content,
    )


def test_routing_report_builds_when_later_arm_is_invalid_leave_output_absent(
    tmp_path: Path,
) -> None:
    # Given
    output_dir = tmp_path / "nonexistent" / "paired-arms"
    rows = tuple(
        RoutingReportRow(
            row_id=row_id,
            lane="query_response",
            category="benign_social",
            split="core",
            routing=_record(classifier_latency=0.1, whole_latency=0.2),
        )
        for row_id in ("core-1", "holdout-1")
    )
    valid = RoutingReportRequest(
        provenance=_provenance(),
        rows=rows,
        stage_usage={"safety_gate": StageUsage(calls=2, tokens=2, cost_usd=0.1)},
        output_dir=output_dir,
    )
    overflow_rows = tuple(
        row.model_copy(
            update={
                "routing": row.routing.model_copy(
                    update={"classifier_latency_s": 1e308}
                )
            }
        )
        for row in rows
    )
    invalid = valid.model_copy(update={"rows": overflow_rows})

    # When
    _ = build_routing_report_pair(valid)
    with pytest.raises(ReportError, match="aggregate"):
        _ = build_routing_report_pair(invalid)

    # Then
    assert not output_dir.exists()
