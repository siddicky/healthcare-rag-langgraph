from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import override

from evals.routing_gate_models import (
    ArmBinding,
    ArmName,
    GateDecision,
    QueryEvidence,
    SafetyEvidence,
)
from evals.routing_gate_verdicts import evaluate_query, evaluate_safety
from evals.routing_provenance import RoutingProvenance, compare_manifests
from evals.routing_report import RoutingReportRequest, build_routing_report_pair
from evals.routing_report_io import ReportPair, publish_report_batch


class PublicationModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class ArmReportLink(PublicationModel):
    arm: ArmName
    experiment_url: str
    json_path: Path
    markdown_path: Path


class GatePublication(PublicationModel):
    lane: Literal["query", "safety"]
    decision: GateDecision
    arm_reports: tuple[ArmReportLink, ...]


class GatePublicationRequest(PublicationModel):
    evidence: QueryEvidence | SafetyEvidence
    arm_reports: tuple[RoutingReportRequest, ...] = Field(min_length=2, max_length=3)
    output_dir: Path
    report_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class PublicationError(Exception):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


def _manifest_arm(manifest: RoutingProvenance) -> ArmName:
    response = manifest.arm_env.HC_RAG_QUERY_RESPONSE_ARM
    classifier = manifest.arm_env.HC_RAG_SAFETY_CLASSIFIER
    arms: dict[tuple[str, str], ArmName] = {
        ("current", "llm"): "current+llm",
        ("deterministic", "llm"): "deterministic+llm",
        ("tool", "llm"): "tool+llm",
        ("current", "semantic_router"): "current+semantic_router",
    }
    arm = arms.get((response, classifier))
    if arm is None:
        msg = "combined or lane-contaminated report arm"
        raise ValueError(msg)
    return arm


def _artifact_digest(manifest: RoutingProvenance) -> str:
    payload = manifest.hashes.model_dump_json().encode()
    return hashlib.sha256(payload).hexdigest()


def binding_from_manifest(manifest: RoutingProvenance) -> ArmBinding:
    return ArmBinding(
        arm=_manifest_arm(manifest),
        git_sha=manifest.git_sha,
        artifact_hash=_artifact_digest(manifest),
        row_ids=tuple(sorted(manifest.rows.local_row_ids)),
        repetitions=manifest.repetitions,
        concurrency=manifest.concurrency,
    )


def _expected_bindings(
    evidence: QueryEvidence | SafetyEvidence,
) -> tuple[ArmBinding, ...]:
    match evidence:
        case QueryEvidence():
            return (
                evidence.reference_binding,
                evidence.control_binding,
                evidence.candidate_binding,
            )
        case SafetyEvidence():
            return evidence.reference_binding, evidence.candidate_binding
        case _ as unreachable:
            assert_never(unreachable)


def _evaluate(evidence: QueryEvidence | SafetyEvidence) -> GateDecision:
    match evidence:
        case QueryEvidence():
            return evaluate_query(evidence)
        case SafetyEvidence():
            return evaluate_safety(evidence)
        case _ as unreachable:
            assert_never(unreachable)


def publish_gate(request: GatePublicationRequest) -> tuple[Path, Path]:
    manifests = tuple(item.provenance for item in request.arm_reports)
    match request.evidence:
        case QueryEvidence():
            lane: Literal["query", "safety"] = "query"
            provenance_lane = "query_response"
        case SafetyEvidence():
            lane = "safety"
            provenance_lane = "safety_classifier"
        case _ as unreachable:
            assert_never(unreachable)
    compare_manifests(manifests, lane=provenance_lane)
    observed = tuple(binding_from_manifest(manifest) for manifest in manifests)
    if observed != _expected_bindings(request.evidence):
        raise PublicationError("gate evidence bindings do not match report manifests")
    names = tuple(manifest.experiment_name for manifest in manifests)
    urls = tuple(manifest.experiment_url for manifest in manifests)
    if len(set(names)) != len(names) or len(set(urls)) != len(urls):
        raise PublicationError("arm experiment names and URLs must be unique")
    if request.report_name in names:
        raise PublicationError(
            "gate report name must differ from every arm report stem"
        )
    if any(report.output_dir != request.output_dir for report in request.arm_reports):
        raise PublicationError("gate and arm reports must share one output directory")
    if any(
        row.split == "calibration"
        for report in request.arm_reports
        for row in report.rows
    ):
        raise PublicationError("stage 1 reports must exclude calibration rows")
    arm_pairs = tuple(
        build_routing_report_pair(report) for report in request.arm_reports
    )
    links = tuple(
        ArmReportLink(
            arm=arm.arm,
            experiment_url=arm_request.provenance.experiment_url,
            json_path=pair.json_path,
            markdown_path=pair.markdown_path,
        )
        for arm, arm_request, pair in zip(
            observed, request.arm_reports, arm_pairs, strict=True
        )
    )
    publication = GatePublication(
        lane=lane,
        decision=_evaluate(request.evidence),
        arm_reports=links,
    )
    json_path = request.output_dir / f"{request.report_name}.json"
    markdown_path = request.output_dir / f"{request.report_name}.md"
    markdown = [
        f"# Routing gate — {request.report_name}",
        "",
        f"Verdict: **{publication.decision.verdict.value}**",
        "",
        *(
            f"- `{link.arm}`: [{link.markdown_path.name}]({link.markdown_path.name})"
            for link in publication.arm_reports
        ),
        "",
    ]
    gate_pair = ReportPair(
        json_path=json_path,
        json_content=json.dumps(
            publication.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        markdown_path=markdown_path,
        markdown_content="\n".join(markdown),
    )
    published = publish_report_batch((*arm_pairs, gate_pair))
    return published[-1]
