from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.channels.untracked_value import UntrackedValue
from langgraph.graph.message import add_messages


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
