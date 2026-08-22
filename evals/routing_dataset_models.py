from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Literal, Protocol, TypedDict

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import override


class SafetyCategory(StrEnum):
    IN_SCOPE_INFORMATIONAL = "in_scope_informational"
    PERSONAL_MEDICAL_ADVICE = "personal_medical_advice"
    EMERGENCY_RED_FLAG = "emergency_red_flag"
    OUT_OF_SCOPE = "out_of_scope"
    PROMPT_INJECTION = "prompt_injection"
    AMBIGUOUS = "ambiguous"


class Action(StrEnum):
    RETRIEVE = "retrieve"
    DIRECT = "direct"
    REFUSE = "refuse"
    CLARIFY = "clarify"


class Split(StrEnum):
    CALIBRATION = "calibration"
    CORE = "core"
    HOLDOUT = "holdout"


class RowStratum(StrEnum):
    BENIGN_SOCIAL = "benign_social"
    IN_SCOPE_MEDICAL = "in_scope_medical"
    MIXED_SOCIAL_MEDICAL = "mixed_social_medical"
    AMBIGUOUS_CLINICAL = "ambiguous_clinical"
    OUT_OF_SCOPE = "out_of_scope"
    PERSONAL_ADVICE = "personal_advice"
    EMERGENCY = "emergency"
    PROMPT_INJECTION = "prompt_injection"
    PII_RECALL = "pii_recall"


class ThreadStratum(StrEnum):
    BENIGN_SOCIAL_THREAD = "benign_social_thread"
    SOCIAL_TO_MEDICAL = "social_to_medical"
    MEDICAL_TO_SOCIAL = "medical_to_social"
    SAFETY_ESCALATION = "safety_escalation"
    PII_REASK = "pii_reask"
    INJECTION_PRESSURE = "injection_pressure"


class FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class Prototype(FrozenModel):
    id: str = Field(min_length=1)
    category: SafetyCategory
    text: str = Field(min_length=1)


class RoutingRow(FrozenModel):
    id: str = Field(min_length=1)
    split: Split
    stratum: RowStratum
    question: str = Field(min_length=1)
    history: tuple[str, ...]
    expected_safety_category: SafetyCategory
    expected_benign_social: bool
    expected_action: Action
    allowed_tool_names: tuple[str, ...]
    forbidden_output_markers: tuple[str, ...]


class RoutingTurn(FrozenModel):
    id: str = Field(min_length=1)
    user: str = Field(min_length=1)
    expected_safety_category: SafetyCategory
    expected_benign_social: bool
    expected_action: Action
    allowed_tool_names: tuple[str, ...]
    forbidden_output_markers: tuple[str, ...]


class RoutingConversation(FrozenModel):
    id: str = Field(min_length=1)
    split: Split
    kind: Literal["scripted"]
    stratum: ThreadStratum
    history: tuple[str, ...]
    turns: tuple[RoutingTurn, ...] = Field(min_length=1)


class ExampleJson(TypedDict):
    id: str
    inputs: dict[str, str | list[str]]
    outputs: dict[str, str | bool | list[str]]
    split: str
    metadata: dict[str, str | bool]


class SdkExample(TypedDict):
    id: uuid.UUID
    inputs: dict[str, str | list[str]]
    outputs: dict[str, str | bool | list[str]]
    split: str
    metadata: dict[str, str | bool]


@dataclass(frozen=True, slots=True)
class LangSmithExample:
    id: uuid.UUID
    row: RoutingRow

    def as_json(self) -> ExampleJson:
        payload = self.as_sdk_payload()
        return {**payload, "id": str(self.id)}

    def as_sdk_payload(self) -> SdkExample:
        row = self.row
        return {
            "id": self.id,
            "inputs": {"question": row.question, "history": list(row.history)},
            "outputs": {
                "expected_safety_category": row.expected_safety_category.value,
                "expected_benign_social": row.expected_benign_social,
                "expected_action": row.expected_action.value,
                "allowed_tool_names": list(row.allowed_tool_names),
                "forbidden_output_markers": list(row.forbidden_output_markers),
            },
            "split": row.split.value,
            "metadata": {
                "example_id": row.id,
                "split": row.split.value,
                "stratum": row.stratum.value,
                "expected_action": row.expected_action.value,
                "expected_benign_social": row.expected_benign_social,
            },
        }


@dataclass(frozen=True, slots=True)
class RoutingBundle:
    prototypes: tuple[Prototype, ...]
    rows: tuple[RoutingRow, ...]
    conversations: tuple[RoutingConversation, ...]
    content_hash: str


class DataContractError(Exception):
    detail: str

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    @override
    def __str__(self) -> str:
        return self.detail


class RemoteDataset(Protocol):
    @property
    def id(self) -> uuid.UUID: ...

    @property
    def name(self) -> str: ...


class RemoteExample(Protocol):
    @property
    def id(self) -> uuid.UUID: ...


class RoutingDatasetClient(Protocol):
    def has_dataset(self, *, dataset_name: str) -> bool: ...
    def read_dataset(self, *, dataset_name: str) -> RemoteDataset: ...
    def create_dataset(
        self, *, dataset_name: str, description: str
    ) -> RemoteDataset: ...
    def list_examples(self, *, dataset_id: uuid.UUID) -> Iterable[RemoteExample]: ...
    def create_examples(
        self, *, dataset_id: uuid.UUID, examples: list[SdkExample]
    ) -> None: ...
    def update_examples(
        self, *, dataset_id: uuid.UUID, updates: list[SdkExample]
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SyncResult:
    dataset: RemoteDataset
    created: int
    updated: int
