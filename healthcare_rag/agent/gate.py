from __future__ import annotations

import asyncio  # noqa: ANYIO_OK - the plan pins asyncio.timeout around the classifier await.
import hmac
from collections.abc import Mapping
from typing import Final, Literal, TypeAlias, override

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.store.base import BaseStore
from langgraph.types import Command

from healthcare_rag.graph.resources import get as get_resources
from healthcare_rag.models.safety import SafetyAssessment
from healthcare_rag.processors.safety import (
    SafetyGate,
    SafetyLLMCall,
    ascrub_phi,
    identifier_recall_requested,
    injection_flags,
    red_flag_terms,
)
from healthcare_rag.processors.safety_responses import (
    emergency_response,
    injection_response,
    out_of_scope_response,
    personal_advice_response,
)

from .features import (
    compute_features,
    has_unexplained_medical_token,
    is_anaphoric_followup,
)
from .memory import principal_mapping
from .state import CoachState, PreviousContext

CLASSIFIER_TIMEOUT_SECONDS: Final = 5.0
GATEWAY: SafetyLLMCall | None = None

RouteTarget: TypeAlias = Literal[
    "short_circuit",
    "rag_relay",
    "coach_agent",
    "erase_my_data",
    "claim_document",
    "reminder_delivery",
]


def _fallback_assessment() -> SafetyAssessment:
    return SafetyAssessment(
        category="ambiguous",
        contains_phi=False,
        phi_spans=[],
        drug_mentioned="none",
        rationale="safety-gate LLM call failed; deterministic checks only",
    )


class CoachSafetyGate(SafetyGate):
    def __init__(self, gateway: SafetyLLMCall | None = None) -> None:
        super().__init__(gateway=gateway)
        self.classifier_failed: bool = False

    @override
    async def _llm_assess(
        self, query: str, history_context: str = ""
    ) -> SafetyAssessment:
        llm_call = self._llm_call
        if llm_call is None:
            self.classifier_failed = True
            return _fallback_assessment()
        try:
            async with asyncio.timeout(CLASSIFIER_TIMEOUT_SECONDS):
                result = await llm_call(
                    prompt_name="safety_gate",
                    temperature=0.0,
                    response_format=SafetyAssessment,
                    default_response=_fallback_assessment(),
                    user_query=query,
                    conversation_context=history_context or "",
                )
        except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
            self.classifier_failed = True
            return _fallback_assessment()
        if result is None:
            self.classifier_failed = True
            return _fallback_assessment()
        return result


async def _gateway_adapter(**kwargs: str) -> SafetyAssessment | None:
    gateway = get_resources().gateway
    return await gateway.astructured(
        kwargs["prompt_name"],
        SafetyAssessment,
        temperature=0.0,
        default=_fallback_assessment(),
        user_query=kwargs["user_query"],
        conversation_context=kwargs["conversation_context"],
    )


def _previous_context(state: CoachState) -> PreviousContext:
    routed_context: dict[str, PreviousContext] = {
        "rag_relay": "route_a",
        "interrupt_pending": "interrupt_pending",
    }
    previous = routed_context.get(state.get("route", ""))
    if previous is not None:
        return previous
    messages = state.get("messages", [])
    if messages and isinstance(messages[-1], ToolMessage):
        return "tool_card"
    if messages and isinstance(messages[-1], AIMessage) and messages[-1].tool_calls:
        return "tool_card"
    return "none"


async def coach_gate(
    state: CoachState,
    config: RunnableConfig,
    *,
    store: BaseStore | None = None,
) -> Command[RouteTarget]:
    question = state.get("question") or ""
    features = compute_features(
        question, state.get("attachment_id"), _previous_context(state)
    )
    scrubbed = (await ascrub_phi(question))[0]
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
    if features["has_attachment"]:
        update["route"] = "claim_document"
        return Command(update=update, goto="claim_document")
    if (
        red_flag_terms(question)
        or injection_flags(question)
        or identifier_recall_requested(question)
    ):
        update["route"] = "short_circuit"
        return Command(update=update, goto="short_circuit")
    classifier = CoachSafetyGate(gateway=GATEWAY or _gateway_adapter)
    assessment = await classifier.assess(question)
    features["classifier_category"] = assessment.category
    features["classifier_failed"] = classifier.classifier_failed
    if assessment.category in {
        "emergency_red_flag",
        "personal_medical_advice",
        "prompt_injection",
    }:
        body = {
            "emergency_red_flag": emergency_response(),
            "personal_medical_advice": personal_advice_response(),
            "prompt_injection": injection_response(),
        }[assessment.category]
        update["messages"].append(AIMessage(content=body))
        update["route"] = "short_circuit"
        return Command(update=update, goto="short_circuit")
    if features["classifier_failed"]:
        update["messages"].append(AIMessage(content=out_of_scope_response()))
        update["route"] = "short_circuit"
        return Command(update=update, goto="short_circuit")
    if features["is_erase_request"]:
        update["route"] = "erase_my_data"
        return Command(update=update, goto="erase_my_data")
    coaching = features["coaching_parse"] != "none"
    if coaching and has_unexplained_medical_token(question, features):
        update["route"] = "rag_relay"
        return Command(update=update, goto="rag_relay")
    if coaching:
        update["route"] = "coach_agent"
        return Command(update=update, goto="coach_agent")
    if (
        features["has_in_scope_drug"]
        or features["has_oos_drug"]
        or features["has_medical_cue"]
        or features["has_number_unit"]
    ):
        update["route"] = "rag_relay"
        return Command(update=update, goto="rag_relay")
    contextual_followup = features["prev_context"] in {
        "tool_card",
        "interrupt_pending",
    } and (is_anaphoric_followup(question))
    if features["is_smalltalk"] or contextual_followup:
        update["route"] = "coach_agent"
        return Command(update=update, goto="coach_agent")
    update["route"] = "rag_relay"
    return Command(update=update, goto="rag_relay")
