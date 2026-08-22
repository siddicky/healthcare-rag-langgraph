from __future__ import annotations

from evals.routing_gate_models import (
    ArmBinding,
    ArmName,
    ClassRecall,
    FullMetrics,
    QueryEvidence,
    QueryStage1Metrics,
    SafetyEvidence,
    SafetyStage1Metrics,
)

CLASSES = (
    "in_scope_informational",
    "personal_medical_advice",
    "emergency_red_flag",
    "out_of_scope",
    "prompt_injection",
    "ambiguous",
)


def binding(
    arm: ArmName, *, digest: str = "a" * 64, rows: tuple[str, ...] = ("r1",)
) -> ArmBinding:
    return ArmBinding(
        arm=arm,
        git_sha="a" * 40,
        artifact_hash=digest,
        row_ids=rows,
        repetitions=2,
        concurrency=1,
    )


def query_stage1(**changes: float) -> QueryStage1Metrics:
    values: dict[str, float | int] = {
        "forbidden_direct_count": 0,
        "safety_bypass_count": 0,
        "pipeline_error_count": 0,
        "fallback_count": 0,
        "error_count": 0,
        "medical_effective_retrieval_recall": 1.0,
        "medical_tool_decision_recall": 0.95,
        "benign_direct_recall": 0.90,
        "effective_action_accuracy": 0.98,
    }
    values.update(changes)
    return QueryStage1Metrics.model_validate(values)


def full(**changes: float) -> FullMetrics:
    values: dict[str, float | int | tuple[ClassRecall, ...]] = {
        "behavior_match": 0.90,
        "chit_chat_quality": 0.90,
        "correctness": 0.90,
        "groundedness": 0.90,
        "holdout_correctness": 0.90,
        "safety_macro_f1": 0.90,
        "medical_effective_retrieval_recall": 1.0,
        "class_recalls": tuple(
            ClassRecall(category=name, recall=0.90) for name in CLASSES
        ),
        "critical_miss_count": 0,
        "answerable_false_positive_count": 0,
        "forbidden_direct_count": 0,
        "safety_bypass_count": 0,
        "boundary_replay_violation_count": 0,
        "whole_cost_usd": 1.0,
        "whole_latency_p50_s": 1.0,
        "classifier_cost_usd": 1.0,
        "classifier_latency_p50_s": 1.0,
        "fallback_count": 0,
        "error_count": 0,
    }
    values.update(changes)
    return FullMetrics.model_validate(values)


def query(*, candidate_stage1: QueryStage1Metrics | None = None) -> QueryEvidence:
    return QueryEvidence(
        reference_binding=binding("current+llm"),
        control_binding=binding("deterministic+llm"),
        candidate_binding=binding("tool+llm"),
        reference_stage1=query_stage1(effective_action_accuracy=0.95),
        control_stage1=query_stage1(effective_action_accuracy=0.97),
        candidate_stage1=candidate_stage1
        or query_stage1(effective_action_accuracy=0.97),
        reference_stage2=full(behavior_match=0.80),
        control_stage2=full(chit_chat_quality=0.80),
        candidate_stage2=full(behavior_match=0.83, chit_chat_quality=0.83),
    )


def safety_metrics(**changes: float | tuple[ClassRecall, ...]) -> SafetyStage1Metrics:
    values: dict[str, float | int | tuple[ClassRecall, ...]] = {
        "fallback_count": 0,
        "error_count": 0,
        "macro_f1": 0.90,
        "class_recalls": tuple(
            ClassRecall(category=name, recall=0.90) for name in CLASSES
        ),
        "emergency_miss_count": 0,
        "personal_miss_count": 0,
        "injection_miss_count": 0,
        "out_of_scope_miss_count": 0,
        "answerable_false_positive_count": 0,
    }
    values.update(changes)
    return SafetyStage1Metrics.model_validate(values)


def safety() -> SafetyEvidence:
    return SafetyEvidence(
        reference_binding=binding("current+llm"),
        candidate_binding=binding("current+semantic_router"),
        reference_residual=safety_metrics(),
        candidate_residual=safety_metrics(),
        reference_full_shell=safety_metrics(),
        candidate_full_shell=safety_metrics(),
        reference_stage2=full(safety_macro_f1=0.80),
        candidate_stage2=full(
            safety_macro_f1=0.83,
            classifier_cost_usd=0.80,
            classifier_latency_p50_s=0.80,
        ),
    )
