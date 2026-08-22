from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol, assert_never

from pydantic import BaseModel, ConfigDict
from typing_extensions import override

from evals.routing_gate_args import Lane
from evals.routing_gate_models import (
    ArmName,
    FullMetrics,
    GateDecision,
    QueryEvidence,
    QueryStage1Metrics,
    SafetyEvidence,
    SafetyStage1Metrics,
    Verdict,
)
from evals.routing_gate_publish import (
    GatePublicationRequest,
    binding_from_manifest,
    publish_gate,
)
from evals.routing_gate_verdicts import evaluate_query, evaluate_safety
from evals.routing_provenance import compare_manifests
from evals.routing_report import RoutingReportRequest


class RunnerModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class GateRunSettings(RunnerModel):
    lane: Lane
    stage: Literal["1", "all"]
    repetitions: int
    concurrency: int
    report_name: str


class ArmRunRequest(RunnerModel):
    lane: Lane
    stage: Literal["1", "2"]
    repetitions: int
    concurrency: int
    report_name: str
    arm: ArmName


class ArmRunResult(RunnerModel):
    report: RoutingReportRequest
    query_stage1: QueryStage1Metrics | None = None
    safety_residual: SafetyStage1Metrics | None = None
    safety_full_shell: SafetyStage1Metrics | None = None
    stage2: FullMetrics | None = None


class RoutingGateRunner(Protocol):
    def run_arm(self, request: ArmRunRequest) -> ArmRunResult: ...


@dataclass(frozen=True, slots=True)
class RunnerError(Exception):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


def _arms(lane: Lane) -> tuple[ArmName, ...]:
    match lane:
        case "query":
            return "current+llm", "deterministic+llm", "tool+llm"
        case "safety":
            return "current+llm", "current+semantic_router"
        case _ as unreachable:
            assert_never(unreachable)


def _query_evidence(
    stage1: tuple[ArmRunResult, ...], stage2: tuple[ArmRunResult, ...] | None = None
) -> QueryEvidence:
    if len(stage1) != 3 or any(result.query_stage1 is None for result in stage1):
        raise RunnerError("query runner omitted stage-1 metrics")
    reference, control, candidate = stage1
    measured = stage2 or stage1
    assert reference.query_stage1 is not None
    assert control.query_stage1 is not None
    assert candidate.query_stage1 is not None
    return QueryEvidence(
        reference_binding=binding_from_manifest(measured[0].report.provenance),
        control_binding=binding_from_manifest(measured[1].report.provenance),
        candidate_binding=binding_from_manifest(measured[2].report.provenance),
        reference_stage1=reference.query_stage1,
        control_stage1=control.query_stage1,
        candidate_stage1=candidate.query_stage1,
        reference_stage2=measured[0].stage2 if stage2 else None,
        control_stage2=measured[1].stage2 if stage2 else None,
        candidate_stage2=measured[2].stage2 if stage2 else None,
    )


def _safety_evidence(
    stage1: tuple[ArmRunResult, ...], stage2: tuple[ArmRunResult, ...] | None = None
) -> SafetyEvidence:
    if len(stage1) != 2 or any(
        result.safety_residual is None or result.safety_full_shell is None
        for result in stage1
    ):
        raise RunnerError("safety runner omitted stage-1 metrics")
    reference, candidate = stage1
    measured = stage2 or stage1
    assert reference.safety_residual is not None
    assert candidate.safety_residual is not None
    assert reference.safety_full_shell is not None
    assert candidate.safety_full_shell is not None
    return SafetyEvidence(
        reference_binding=binding_from_manifest(measured[0].report.provenance),
        candidate_binding=binding_from_manifest(measured[1].report.provenance),
        reference_residual=reference.safety_residual,
        candidate_residual=candidate.safety_residual,
        reference_full_shell=reference.safety_full_shell,
        candidate_full_shell=candidate.safety_full_shell,
        reference_stage2=measured[0].stage2 if stage2 else None,
        candidate_stage2=measured[1].stage2 if stage2 else None,
    )


def _run_phase(
    settings: GateRunSettings,
    runner: RoutingGateRunner,
    phase: Literal["1", "2"],
) -> tuple[ArmRunResult, ...]:
    return tuple(
        runner.run_arm(
            ArmRunRequest(
                lane=settings.lane,
                stage=phase,
                repetitions=settings.repetitions,
                concurrency=settings.concurrency,
                arm=arm,
                report_name=(
                    f"{settings.report_name}-{arm.replace('+', '-')}-stage{phase}"
                ),
            )
        )
        for arm in _arms(settings.lane)
    )


def _evidence(
    lane: Lane,
    stage1: tuple[ArmRunResult, ...],
    stage2: tuple[ArmRunResult, ...] | None = None,
) -> QueryEvidence | SafetyEvidence:
    match lane:
        case "query":
            return _query_evidence(stage1, stage2)
        case "safety":
            return _safety_evidence(stage1, stage2)
        case _ as unreachable:
            assert_never(unreachable)


def _decision(evidence: QueryEvidence | SafetyEvidence) -> GateDecision:
    match evidence:
        case QueryEvidence():
            return evaluate_query(evidence)
        case SafetyEvidence():
            return evaluate_safety(evidence)
        case _ as unreachable:
            assert_never(unreachable)


def _validate_phase(
    settings: GateRunSettings,
    results: tuple[ArmRunResult, ...],
    phase: Literal["1", "2"],
) -> None:
    provenance_lane = (
        "query_response" if settings.lane == "query" else "safety_classifier"
    )
    compare_manifests(
        tuple(result.report.provenance for result in results), lane=provenance_lane
    )
    manifests = tuple(result.report.provenance for result in results)
    if any(
        manifest.repetitions != settings.repetitions
        or manifest.concurrency != settings.concurrency
        for manifest in manifests
    ):
        raise RunnerError("runner report settings do not match gate request")
    expected_arms = _arms(settings.lane)
    observed_arms = tuple(binding_from_manifest(manifest).arm for manifest in manifests)
    if observed_arms != expected_arms:
        raise RunnerError("runner report arms do not match gate request")
    expected_names = tuple(
        f"{settings.report_name}-{arm.replace('+', '-')}-stage{phase}"
        for arm in expected_arms
    )
    if tuple(manifest.experiment_name for manifest in manifests) != expected_names:
        raise RunnerError("runner report names do not match gate request")
    if any(
        row.split == "calibration" for result in results for row in result.report.rows
    ):
        raise RunnerError("routing stage reports must exclude calibration rows")


def run_gate(settings: GateRunSettings, runner: RoutingGateRunner) -> GateDecision:
    stage1 = _run_phase(settings, runner, "1")
    _validate_phase(settings, stage1, "1")
    stage1_evidence = _evidence(settings.lane, stage1)
    stage1_decision = _decision(stage1_evidence)
    run_stage2 = (
        settings.stage == "all" and stage1_decision.verdict is Verdict.INCONCLUSIVE
    )
    stage2 = _run_phase(settings, runner, "2") if run_stage2 else None
    if stage2 is not None:
        _validate_phase(settings, stage2, "2")
        if any(result.stage2 is None for result in stage2):
            raise RunnerError("stage-2 runner omitted full metrics")
        before = tuple(
            binding_from_manifest(result.report.provenance) for result in stage1
        )
        after = tuple(
            binding_from_manifest(result.report.provenance) for result in stage2
        )
        if before != after:
            raise RunnerError("stage-1 and stage-2 report bindings must match")
    results = stage2 or stage1
    evidence = _evidence(settings.lane, stage1, stage2)
    output_dirs = {result.report.output_dir for result in results}
    if len(output_dirs) != 1:
        raise RunnerError("routing arm reports must share one output directory")
    request = GatePublicationRequest(
        evidence=evidence,
        arm_reports=tuple(result.report for result in results),
        output_dir=next(iter(output_dirs)),
        report_name=settings.report_name,
    )
    _ = publish_gate(request)
    return _decision(evidence)
