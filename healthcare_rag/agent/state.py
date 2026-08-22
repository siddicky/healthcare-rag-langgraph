from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.channels.untracked_value import UntrackedValue
from langgraph.graph.message import add_messages

from healthcare_rag.models.safety import SafetyCategory

CoachingParse = Literal[
    "metric_log",
    "injection_log",
    "schedule_view",
    "schedule_change",
    "memory_write",
    "reminder_manage",
    "none",
]
PreviousContext = Literal["route_a", "tool_card", "interrupt_pending", "none"]


class CronWakePayload(TypedDict):
    reminder_id: str
    user_id: str
    thread_id: str
    wake_token: str


class CoachState(TypedDict, total=False):
    question: Annotated[str, UntrackedValue(str)]
    attachment_id: Annotated[str | None, UntrackedValue(str)]
    cron_wake: Annotated[CronWakePayload | None, UntrackedValue(dict)]
    reminder_wake: Annotated[CronWakePayload | None, UntrackedValue(dict)]
    messages: Annotated[list[AnyMessage], add_messages]
    route: str
    follow_ups: list[str]
    pending_document_op_id: str | None


class CoachInput(TypedDict, total=False):
    question: str | None
    attachment_id: str | None
    cron_wake: CronWakePayload | None


class CoachOutput(TypedDict):
    messages: list[AnyMessage]
    follow_ups: list[str]


class TurnFeatures(TypedDict):
    has_in_scope_drug: bool
    has_oos_drug: bool
    has_medical_cue: bool
    has_number_unit: bool
    is_content_request: bool
    coaching_parse: CoachingParse
    is_erase_request: bool
    is_smalltalk: bool
    has_attachment: bool
    prev_context: PreviousContext
    classifier_category: SafetyCategory
    classifier_failed: bool
