from __future__ import annotations

from langchain_core.messages import BaseMessage
from langgraph.types import Command
from pydantic import TypeAdapter, ValidationError

from healthcare_rag.graph.llm import (
    LangChainLLMGateway,
    QueryOrRespondDecision,
    RouterAction,
)
from healthcare_rag.graph.resources import get as get_resources
from healthcare_rag.graph.routers import (
    QueryOrRespondTarget,
    route_after_query_or_respond,
)
from healthcare_rag.graph.state import JSONValue, RAGState
from healthcare_rag.models.safety import SocialIntent
from healthcare_rag.processors.social_responses import social_response

GATEWAY: LangChainLLMGateway | None = None
SOCIAL_INTENT_ADAPTER = TypeAdapter(SocialIntent)


def _telemetry(
    decision: QueryOrRespondDecision,
    effective_action: RouterAction,
    fallback_reason: str | None,
) -> dict[str, JSONValue]:
    return {
        "backend": "tool",
        "model_action": decision.action,
        "effective_action": effective_action,
        "fallback": fallback_reason is not None,
        "error": fallback_reason is not None,
        "fallback_reason": fallback_reason,
        "tool_call_count": decision.tool_call_count,
    }


def _trusted_social_intent(safety: dict[str, JSONValue] | None) -> SocialIntent | None:
    intent = safety.get("social_intent") if safety is not None else None
    try:
        return SOCIAL_INTENT_ADAPTER.validate_python(intent)
    except ValidationError:
        return None


async def generate_query_or_respond(state: RAGState) -> RAGState:
    """Map one model decision onto safe direct or existing-pipeline state channels."""
    resources = get_resources()
    if resources.settings.query_response_arm != "tool":
        return {}

    query = state.get("scrubbed_question", "")
    history: list[BaseMessage] = list(state.get("messages", []))
    gateway = GATEWAY or resources.gateway
    decision = await gateway.aquery_or_respond(history, query)
    safety = state.get("safety")
    social_intent = _trusted_social_intent(safety)
    benign_social = (
        safety is not None
        and safety.get("category") == "out_of_scope"
        and safety.get("benign_social") is True
        and social_intent is not None
    )

    if benign_social:
        assert social_intent is not None
        fallback_reason = (
            None
            if decision.action == "direct" and decision.fallback_reason is None
            else decision.fallback_reason or "social_tool_call"
        )
        direct_response = (
            decision.direct_content
            if fallback_reason is None
            else social_response(social_intent)
        )
        return {
            "working_query": query,
            "direct_response": direct_response,
            "response_action": "direct",
            "follow_ups": [],
            "query_router": _telemetry(decision, "direct", fallback_reason),
        }

    category = safety.get("category") if safety is not None else None
    if category != "in_scope_informational":
        return {
            "working_query": query,
            "direct_response": None,
            "response_action": "retrieve",
            "query_router": _telemetry(
                decision,
                "retrieve",
                "invalid_social_authority",
            ),
        }

    fallback_reason = decision.fallback_reason
    if decision.action == "direct" and fallback_reason is None:
        fallback_reason = "medical_free_text"
    if decision.tool_query is None:
        working_query = query
        fallback_reason = fallback_reason or "invalid_tool_decision"
    else:
        working_query = decision.tool_query
    return {
        "working_query": working_query,
        "direct_response": None,
        "response_action": "retrieve",
        "query_router": _telemetry(decision, "retrieve", fallback_reason),
    }


async def route_query_or_respond(state: RAGState) -> Command[QueryOrRespondTarget]:
    update = await generate_query_or_respond(state)
    next_state: RAGState = {**state, **update}
    return Command(update=update, goto=route_after_query_or_respond(next_state))
