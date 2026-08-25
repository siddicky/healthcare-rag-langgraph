from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Literal, TypeAlias

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.store.base import BaseStore
from langgraph.types import Command

from healthcare_rag.processors.safety import (
    identifier_recall_requested,
    injection_flags,
    red_flag_terms,
    scrub_phi,
)

from .features import is_erase_request
from .memory import principal_mapping
from .state import CoachState

RouteTarget: TypeAlias = Literal[
    "short_circuit",
    "coach_agent",
    "erase_my_data",
    "claim_document",
    "reminder_delivery",
]


async def coach_gate(
    state: CoachState,
    config: RunnableConfig,
    *,
    store: BaseStore | None = None,
) -> Command[RouteTarget]:
    question = state.get("question") or ""
    scrubbed = scrub_phi(question)[0]
    update: CoachState = {
        "question": "",
        "messages": [HumanMessage(content=scrubbed)] if scrubbed else [],
        "follow_ups": [],
    }
    wake = state.get("cron_wake")
    if wake is not None:
        configurable = config.get("configurable", {})
        thread_id = configurable.get("thread_id")
        principal = principal_mapping(configurable.get("langgraph_auth_user"))
        member_context = (
            principal is not None and principal.get("role") == "member"
        )
        record = None
        if store is not None and not member_context:
            record = await store.aget(
                ("users", wake["user_id"], "reminders"), wake["reminder_id"]
            )
        value = record.value if record is not None else {}
        valid = (
            isinstance(value, Mapping)
            and value.get("active") is True
            and value.get("reminder_id") == wake["reminder_id"]
            and value.get("thread_id") == wake["thread_id"] == thread_id
            and value.get("user_id", wake["user_id"]) == wake["user_id"]
            and isinstance(value.get("wake_token"), str)
            and hmac.compare_digest(value["wake_token"], wake["wake_token"])
        )
        update["cron_wake"] = None
        update["reminder_wake"] = wake if valid else None
        update["route"] = "reminder_delivery" if valid else "short_circuit"
        return Command(
            update=update,
            goto="reminder_delivery" if valid else "short_circuit",
        )
    if state.get("attachment_id"):
        update["route"] = "claim_document"
        return Command(update=update, goto="claim_document")
    if (
        red_flag_terms(question)
        or injection_flags(question)
        or identifier_recall_requested(question)
    ):
        update["route"] = "short_circuit"
        return Command(update=update, goto="short_circuit")
    if is_erase_request(question):
        update["route"] = "erase_my_data"
        return Command(update=update, goto="erase_my_data")
    update["route"] = "coach_agent"
    return Command(update=update, goto="coach_agent")
