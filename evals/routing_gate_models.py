from __future__ import annotations

from enum import StrEnum
from typing import ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

ArmName: TypeAlias = Literal[
    "current+llm",
    "deterministic+llm",
    "tool+llm",
    "current+semantic_router",
]
SafetyCategoryName: TypeAlias = Literal[
    "in_scope_informational",
    "personal_medical_advice",
    "emergency_red_flag",
    "out_of_scope",
    "prompt_injection",
    "ambiguous",
]

SAFETY_CATEGORIES = frozenset(
    {
        "in_scope_informational",
        "personal_medical_advice",
        "emergency_red_flag",
        "out_of_scope",
        "prompt_injection",
        "ambiguous",
    }
)


class GateModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class Verdict(StrEnum):
    ADOPT = "ADOPT"
    ERROR = "ERROR"
    REJECT = "REJECT"
    INCONCLUSIVE = "INCONCLUSIVE"


class ArmBinding(GateModel):
    arm: ArmName
    git_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    row_ids: tuple[str, ...] = Field(min_length=1)
    repetitions: int = Field(ge=1)
    concurrency: int = Field(ge=1)
    calibration_row_count: Literal[0] = 0

    @model_validator(mode="after")
    def require_unique_rows(self) -> ArmBinding:
        if len(set(self.row_ids)) != len(self.row_ids):
            msg = "row IDs must be unique"
            raise ValueError(msg)
        return self


class ClassRecall(GateModel):
    category: SafetyCategoryName
    recall: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class RecallSet(GateModel):
    class_recalls: tuple[ClassRecall, ...]

    @model_validator(mode="after")
    def require_every_safety_class(self) -> RecallSet:
        names = tuple(item.category for item in self.class_recalls)
        if len(names) != len(set(names)) or frozenset(names) != SAFETY_CATEGORIES:
            msg = "class recalls must contain every safety category exactly once"
            raise ValueError(msg)
        return self


class QueryStage1Metrics(GateModel):
    forbidden_direct_count: int = Field(ge=0)
    safety_bypass_count: int = Field(ge=0)
    pipeline_error_count: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    medical_effective_retrieval_recall: float = Field(
        ge=0.0, le=1.0, allow_inf_nan=False
    )
    medical_tool_decision_recall: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    benign_direct_recall: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    effective_action_accuracy: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class SafetyStage1Metrics(RecallSet):
    fallback_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    macro_f1: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    emergency_miss_count: int = Field(ge=0)
    personal_miss_count: int = Field(ge=0)
    injection_miss_count: int = Field(ge=0)
    out_of_scope_miss_count: int = Field(ge=0)
    answerable_false_positive_count: int = Field(ge=0)


class FullMetrics(RecallSet):
    behavior_match: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    chit_chat_quality: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    correctness: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    groundedness: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    holdout_correctness: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    safety_macro_f1: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    medical_effective_retrieval_recall: float = Field(
        ge=0.0, le=1.0, allow_inf_nan=False
    )
    critical_miss_count: int = Field(ge=0)
    answerable_false_positive_count: int = Field(ge=0)
    forbidden_direct_count: int = Field(ge=0)
    safety_bypass_count: int = Field(ge=0)
    boundary_replay_violation_count: int = Field(ge=0)
    whole_cost_usd: float = Field(ge=0.0, allow_inf_nan=False)
    whole_latency_p50_s: float = Field(ge=0.0, allow_inf_nan=False)
    classifier_cost_usd: float = Field(ge=0.0, allow_inf_nan=False)
    classifier_latency_p50_s: float = Field(ge=0.0, allow_inf_nan=False)
    fallback_count: int = Field(ge=0)
    error_count: int = Field(ge=0)


class QueryEvidence(GateModel):
    reference_binding: ArmBinding
    control_binding: ArmBinding
    candidate_binding: ArmBinding
    reference_stage1: QueryStage1Metrics
    control_stage1: QueryStage1Metrics
    candidate_stage1: QueryStage1Metrics
    reference_stage2: FullMetrics | None = None
    control_stage2: FullMetrics | None = None
    candidate_stage2: FullMetrics | None = None
    integrity_errors: tuple[str, ...] = ()


class SafetyEvidence(GateModel):
    reference_binding: ArmBinding
    candidate_binding: ArmBinding
    reference_residual: SafetyStage1Metrics
    candidate_residual: SafetyStage1Metrics
    reference_full_shell: SafetyStage1Metrics
    candidate_full_shell: SafetyStage1Metrics
    reference_stage2: FullMetrics | None = None
    candidate_stage2: FullMetrics | None = None
    integrity_errors: tuple[str, ...] = ()


class MetricDeltas(GateModel):
    behavior_match: float | None = None
    chit_chat_quality: float | None = None
    safety_macro_f1: float | None = None
    classifier_cost_ratio: float | None = None
    classifier_latency_p50_ratio: float | None = None
    whole_cost_ratio: float | None = None
    whole_latency_p50_ratio: float | None = None


class GateFailure(GateModel):
    name: str
    kind: Literal["error", "quality", "operational"]


class GateDecision(GateModel):
    verdict: Verdict
    exit_code: Literal[0, 1, 2, 3]
    stage: Literal[1, 2]
    stage2_evaluated: bool
    failures: tuple[GateFailure, ...]
    deltas: MetricDeltas
