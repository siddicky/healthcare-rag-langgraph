from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Literal, assert_never

import pytest

from evals.routing_dataset_models import Action, SafetyCategory
from evals.routing_evaluators import RoutingRecord
from evals.routing_gate_models import Verdict
from evals.routing_gate_publish import (
    GatePublication,
    GatePublicationRequest,
    binding_from_manifest,
    publish_gate,
)
from evals.routing_gate_verdicts import evaluate_query
from evals.routing_provenance import (
    ArmEnvironment,
    ArtifactHashes,
    ExperimentRows,
    RoutingProvenance,
)
from evals.routing_report import RoutingReportRequest, RoutingReportRow, StageUsage
from tests.routing_gate_fixtures import binding, full, query


def assert_binding_or_lane_contamination_is_error() -> None:
    evidence_rows = (
        query().model_copy(
            update={"candidate_binding": binding("tool+llm", digest="b" * 64)}
        ),
        query().model_copy(
            update={"candidate_binding": binding("tool+llm", rows=("r2",))}
        ),
        query().model_copy(
            update={"candidate_binding": binding("current+semantic_router")}
        ),
    )
    for evidence in evidence_rows:
        assert (
            evaluate_query(evidence).verdict,
            evaluate_query(evidence).exit_code,
        ) == (
            Verdict.ERROR,
            1,
        )


def assert_stage1_only_never_adopts() -> None:
    evidence = query().model_copy(
        update={
            "reference_stage2": None,
            "control_stage2": None,
            "candidate_stage2": None,
        }
    )
    assert (evaluate_query(evidence).verdict, evaluate_query(evidence).exit_code) == (
        Verdict.INCONCLUSIVE,
        3,
    )


def assert_nonfinite_metric_is_rejected_at_parse_boundary() -> None:
    with pytest.raises(ValueError):
        _ = full(correctness=math.nan)


def assert_absent_metric_is_rejected_at_parse_boundary() -> None:
    payload = full().model_dump()
    del payload["correctness"]
    with pytest.raises(ValueError):
        _ = type(full()).model_validate(payload)


def assert_json_smoke_keeps_progress_off_stdout() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.routing_gate",
            "--lane",
            "query",
            "--smoke",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    lines = completed.stdout.splitlines()
    assert completed.returncode == 0
    assert len(lines) == 1
    assert json.loads(lines[-1])["verdict"] == "ADOPT"
    assert "[routing-gate]" in completed.stderr


def assert_malformed_fixture_child_output_is_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    _ = path.write_text("not-json")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.routing_gate",
            "--lane",
            "safety",
            "--fixture",
            str(path),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout.splitlines()[-1])["verdict"] == "ERROR"


def manifest(
    arm: Literal[
        "current+llm",
        "deterministic+llm",
        "tool+llm",
        "current+semantic_router",
    ],
) -> RoutingProvenance:
    digest = "a" * 64
    match arm:
        case "current+llm":
            response, classifier = "current", "llm"
        case "deterministic+llm":
            response, classifier = "deterministic", "llm"
        case "tool+llm":
            response, classifier = "tool", "llm"
        case "current+semantic_router":
            response, classifier = "current", "semantic_router"
        case _ as unreachable:
            assert_never(unreachable)
    return RoutingProvenance.model_validate(
        {
            "git_sha": "1" * 40,
            "git_dirty": False,
            "arm_env": ArmEnvironment(
                HC_RAG_QUERY_RESPONSE_ARM=response,
                HC_RAG_SAFETY_CLASSIFIER=classifier,
            ),
            "rows": ExperimentRows(
                local_row_count=1,
                local_row_ids=("r1",),
                langsmith_row_count=1,
                langsmith_row_ids=("r1",),
            ),
            "experiment_name": arm.replace("+", "-"),
            "experiment_url": f"https://smith.langchain.com/{response}-{classifier}",
            "hashes": ArtifactHashes(
                **dict.fromkeys(ArtifactHashes.model_fields, digest)
            ),
            "semantic_router_version": "0.1.16",
            "encoder_model": "text-embedding-3-small",
            "judge_model": "gpt-5.4-mini",
            "repetitions": 2,
            "concurrency": 1,
        }
    )


def report(manifest: RoutingProvenance, output_dir: Path) -> RoutingReportRequest:
    record = RoutingRecord(
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
        whole_cost_usd=0.02,
        whole_llm_calls=2,
        whole_embedding_calls=0,
        pipeline_error=False,
        classifier_fallback=False,
        evaluator_error=False,
    )
    row = RoutingReportRow(
        row_id="r1",
        lane="query_response",
        category="benign_social",
        split="core",
        routing=record,
    )
    return RoutingReportRequest(
        provenance=manifest,
        rows=(row,),
        stage_usage={"routing": StageUsage(calls=1, tokens=1, cost_usd=0.01)},
        output_dir=output_dir,
    )


def publication_request(
    tmp_path: Path, report_name: str = "query-smoke-gate"
) -> GatePublicationRequest:
    manifests = tuple(
        manifest(arm) for arm in ("current+llm", "deterministic+llm", "tool+llm")
    )
    evidence = query().model_copy(
        update={
            "reference_binding": binding_from_manifest(manifests[0]),
            "control_binding": binding_from_manifest(manifests[1]),
            "candidate_binding": binding_from_manifest(manifests[2]),
        }
    )
    return GatePublicationRequest(
        evidence=evidence,
        arm_reports=tuple(report(manifest, tmp_path) for manifest in manifests),
        output_dir=tmp_path,
        report_name=report_name,
    )


def assert_smoke_gate_publishes_linked_todo5_reports(tmp_path: Path) -> None:
    request = publication_request(tmp_path)
    json_path, markdown_path = publish_gate(request)
    payload = GatePublication.model_validate_json(json_path.read_text())
    assert payload.decision.verdict is Verdict.ADOPT
    assert len(payload.arm_reports) == 3
    assert all(
        link.json_path.exists() and link.markdown_path.exists()
        for link in payload.arm_reports
    )
    markdown = markdown_path.read_text()
    assert all(link.markdown_path.name in markdown for link in payload.arm_reports)
