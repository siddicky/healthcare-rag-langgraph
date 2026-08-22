from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from evals.routing_gate_args import GateArgs, Lane
from evals.routing_gate_models import (
    ArmBinding,
    ArmName,
    ClassRecall,
    FullMetrics,
    GateDecision,
    GateFailure,
    MetricDeltas,
    QueryEvidence,
    QueryStage1Metrics,
    SafetyCategoryName,
    SafetyEvidence,
    SafetyStage1Metrics,
    Verdict,
)
from evals.routing_gate_publish import PublicationError
from evals.routing_gate_runner import (
    GateRunSettings,
    RoutingGateRunner,
    RunnerError,
    run_gate,
)
from evals.routing_gate_subprocess import SubprocessRoutingGateRunner
from evals.routing_gate_verdicts import evaluate_query, evaluate_safety
from evals.routing_provenance import ProvenanceError
from evals.routing_report_io import ReportError

_CATEGORIES: tuple[SafetyCategoryName, ...] = (
    "in_scope_informational",
    "personal_medical_advice",
    "emergency_red_flag",
    "out_of_scope",
    "prompt_injection",
    "ambiguous",
)


def _binding(arm: ArmName, repetitions: int, concurrency: int) -> ArmBinding:
    return ArmBinding(
        arm=arm,
        git_sha="0" * 40,
        artifact_hash="0" * 64,
        row_ids=("smoke-row",),
        repetitions=repetitions,
        concurrency=concurrency,
    )


def _recalls() -> tuple[ClassRecall, ...]:
    return tuple(ClassRecall(category=name, recall=1.0) for name in _CATEGORIES)


def _query_stage1() -> QueryStage1Metrics:
    return QueryStage1Metrics(
        forbidden_direct_count=0,
        safety_bypass_count=0,
        pipeline_error_count=0,
        fallback_count=0,
        error_count=0,
        medical_effective_retrieval_recall=1.0,
        medical_tool_decision_recall=1.0,
        benign_direct_recall=1.0,
        effective_action_accuracy=1.0,
    )


def _full(*, behavior: float, chat: float, macro_f1: float = 1.0) -> FullMetrics:
    return FullMetrics(
        behavior_match=behavior,
        chit_chat_quality=chat,
        correctness=1.0,
        groundedness=1.0,
        holdout_correctness=1.0,
        safety_macro_f1=macro_f1,
        medical_effective_retrieval_recall=1.0,
        class_recalls=_recalls(),
        critical_miss_count=0,
        answerable_false_positive_count=0,
        forbidden_direct_count=0,
        safety_bypass_count=0,
        boundary_replay_violation_count=0,
        whole_cost_usd=1.0,
        whole_latency_p50_s=1.0,
        classifier_cost_usd=1.0,
        classifier_latency_p50_s=1.0,
        fallback_count=0,
        error_count=0,
    )


def _safety_stage1() -> SafetyStage1Metrics:
    return SafetyStage1Metrics(
        fallback_count=0,
        error_count=0,
        macro_f1=1.0,
        class_recalls=_recalls(),
        emergency_miss_count=0,
        personal_miss_count=0,
        injection_miss_count=0,
        out_of_scope_miss_count=0,
        answerable_false_positive_count=0,
    )


def _smoke_query(repetitions: int, concurrency: int) -> QueryEvidence:
    return QueryEvidence(
        reference_binding=_binding("current+llm", repetitions, concurrency),
        control_binding=_binding("deterministic+llm", repetitions, concurrency),
        candidate_binding=_binding("tool+llm", repetitions, concurrency),
        reference_stage1=_query_stage1(),
        control_stage1=_query_stage1(),
        candidate_stage1=_query_stage1(),
        reference_stage2=_full(behavior=0.80, chat=0.80),
        control_stage2=_full(behavior=0.80, chat=0.80),
        candidate_stage2=_full(behavior=0.83, chat=0.83),
    )


def _smoke_safety(repetitions: int, concurrency: int) -> SafetyEvidence:
    return SafetyEvidence(
        reference_binding=_binding("current+llm", repetitions, concurrency),
        candidate_binding=_binding("current+semantic_router", repetitions, concurrency),
        reference_residual=_safety_stage1(),
        candidate_residual=_safety_stage1(),
        reference_full_shell=_safety_stage1(),
        candidate_full_shell=_safety_stage1(),
        reference_stage2=_full(behavior=1.0, chat=1.0, macro_f1=0.96),
        candidate_stage2=_full(behavior=1.0, chat=1.0, macro_f1=0.99),
    )


def _without_stage2(
    evidence: QueryEvidence | SafetyEvidence,
) -> QueryEvidence | SafetyEvidence:
    match evidence:
        case QueryEvidence():
            return evidence.model_copy(
                update={
                    "reference_stage2": None,
                    "control_stage2": None,
                    "candidate_stage2": None,
                }
            )
        case SafetyEvidence():
            return evidence.model_copy(
                update={"reference_stage2": None, "candidate_stage2": None}
            )
        case _ as unreachable:
            assert_never(unreachable)


def _load_fixture(path: Path, lane: Lane) -> QueryEvidence | SafetyEvidence:
    payload = path.read_text()
    match lane:
        case "query":
            return QueryEvidence.model_validate_json(payload)
        case "safety":
            return SafetyEvidence.model_validate_json(payload)
        case _ as unreachable:
            assert_never(unreachable)


def _error_decision(name: str) -> GateDecision:
    return GateDecision(
        verdict=Verdict.ERROR,
        exit_code=1,
        stage=1,
        stage2_evaluated=False,
        failures=(GateFailure(name=name, kind="error"),),
        deltas=MetricDeltas(),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Paired query-response and safety routing gate"
    )
    _ = parser.add_argument("--lane", choices=("query", "safety"), required=True)
    _ = parser.add_argument("--stage", choices=("1", "all"), default="all")
    _ = parser.add_argument("--repetitions", type=int, default=2)
    _ = parser.add_argument("--concurrency", type=int, default=1)
    _ = parser.add_argument("--report-name")
    _ = parser.add_argument("--fixture", type=Path)
    _ = parser.add_argument("--smoke", action="store_true")
    _ = parser.add_argument("--json", action="store_true")
    return parser


def _run(args: GateArgs, runner: RoutingGateRunner | None = None) -> GateDecision:
    lane = args.lane
    if args.fixture is not None:
        evidence = _load_fixture(args.fixture, lane)
    elif args.smoke:
        match lane:
            case "query":
                evidence = _smoke_query(args.repetitions, args.concurrency)
            case "safety":
                evidence = _smoke_safety(args.repetitions, args.concurrency)
            case _ as unreachable:
                assert_never(unreachable)
    else:
        return run_gate(
            GateRunSettings(
                lane=lane,
                stage=args.stage,
                repetitions=args.repetitions,
                concurrency=args.concurrency,
                report_name=args.report_name or "routing-gate",
            ),
            runner or SubprocessRoutingGateRunner(),
        )
    if args.stage == "1":
        evidence = _without_stage2(evidence)
    match evidence:
        case QueryEvidence():
            return evaluate_query(evidence)
        case SafetyEvidence():
            return evaluate_safety(evidence)
        case _ as unreachable:
            assert_never(unreachable)


def main(runner: RoutingGateRunner | None = None) -> int:
    args = _parser().parse_args(namespace=GateArgs())
    print(
        f"[routing-gate] lane={args.lane} stage={args.stage}",
        file=sys.stderr,
        flush=True,
    )
    try:
        decision = _run(args, runner)
    except RunnerError as exc:
        print(f"[routing-gate] runner error: {exc}", file=sys.stderr, flush=True)
        decision = _error_decision("runner_error")
    except ProvenanceError as exc:
        print(f"[routing-gate] provenance error: {exc}", file=sys.stderr, flush=True)
        decision = _error_decision("provenance_error")
    except (PublicationError, ReportError) as exc:
        print(f"[routing-gate] artifact error: {exc}", file=sys.stderr, flush=True)
        decision = _error_decision("artifact_error")
    except (OSError, ValidationError, json.JSONDecodeError) as exc:
        print(f"[routing-gate] invalid evidence: {exc}", file=sys.stderr, flush=True)
        decision = _error_decision("malformed_child_output")
    serialized = decision.model_dump_json()
    if args.json:
        print(serialized)
    else:
        print(
            f"{decision.verdict.value}: {', '.join(item.name for item in decision.failures) or 'all gates passed'}"
        )
    return decision.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
