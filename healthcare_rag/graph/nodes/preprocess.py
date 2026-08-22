from __future__ import annotations

from typing import cast

from langgraph.types import Command

from healthcare_rag.graph.llm import LangChainLLMGateway
from healthcare_rag.graph.resources import get as get_resources
from healthcare_rag.graph.routers import DecomposeTarget, route_after_decompose
from healthcare_rag.graph.state import RAGState
from healthcare_rag.models.answers import RelevantHistoryContext
from healthcare_rag.models.queries import ClarifiedQuery, DecomposedQuery
from healthcare_rag.processors.safety import ascrub_phi
from healthcare_rag.services.models import (
    decompose_only_complex,
    disabled_stages,
    max_subqueries,
)

GATEWAY: LangChainLLMGateway | None = None


def _gateway() -> LangChainLLMGateway:
    return GATEWAY or get_resources().gateway


async def extract_conversation_context(state: RAGState) -> RAGState:
    processed = state.get("processed_history", [])
    if not processed:
        return {
            "summary": RelevantHistoryContext(
                required_context=False,
                explanation="No conversation history available",
                relevant_snippets="",
            ).model_dump(mode="json")
        }
    history_text = "Previous conversation:\n"
    for index, entry in enumerate(processed, start=1):
        history_text += f"[{index}] User: {entry['user_query']}\n"
        history_text += f"    Assistant: {entry['answer']}\n\n"
    default = RelevantHistoryContext(
        required_context=False,
        explanation="No relevant context found",
        relevant_snippets="",
    )
    summary = await _gateway().astructured(
        "extract_conversation_context",
        RelevantHistoryContext,
        temperature=0.1,
        default=default,
        current_query=state.get("working_query", ""),
        history_text=history_text,
    )
    summary_data = (summary or default).model_dump(mode="json")
    summary_data["relevant_snippets"] = (
        await ascrub_phi(str(summary_data.get("relevant_snippets") or ""))
    )[0]
    summary_data["explanation"] = (
        await ascrub_phi(str(summary_data.get("explanation") or ""))
    )[0]
    return {"summary": summary_data}


async def clarify_query(state: RAGState) -> RAGState:
    history_context = state.get("history_context", "")
    if not history_context or "clarify" in disabled_stages():
        return {"clarified": None}
    query = state.get("working_query", "")
    default = ClarifiedQuery(
        original_query=query,
        clarified_query=query,
        ambiguity_level="clear and specific",
    )
    result = await _gateway().astructured(
        "clarify_query",
        ClarifiedQuery,
        default=default,
        user_query=query,
        conversation_context=history_context,
    )
    clarified = (await ascrub_phi((result or default).clarified_query))[0]
    if clarified == query:
        return {"clarified": None}
    return {
        "working_query": clarified,
        "clarified": clarified,
        "selected_branch_type": "clarified",
        "branch_events": [
            {
                "phase": 0,
                "kind": "clarify",
                "index": 0,
                "branch": "clarified",
                "status": "COMPLETED",
            }
        ],
    }


async def decompose_query(state: RAGState) -> Command[DecomposeTarget]:
    """Decide the fan-out shape and dispatch it in the same step.

    ``route_after_decompose`` turns the post-update ``decomposed`` /
    ``sub_queries`` / ``selected_branch_type`` channels into one ``Send`` per
    retrieval branch, so the router is called on the state this update produces.
    """
    query = state.get("working_query", "")
    default = DecomposedQuery(
        original_query=query,
        query_complexity="simple",
        decomposed_query=[query],
    )
    if "decompose" in disabled_stages():
        result = default
    else:
        result = await _gateway().astructured(
            "decompose_query",
            DecomposedQuery,
            default=default,
            user_query=query,
        ) or default
    proposed = [
        (await ascrub_phi(query))[0] for query in (result.decomposed_query or [])
    ]
    fan_out = len(proposed) >= 2 and (
        not decompose_only_complex() or result.query_complexity == "complex"
    )
    if fan_out:
        return _decompose_command(
            state,
            {
                "decomposed": True,
                "sub_queries": proposed[: max_subqueries()],
                "selected_branch_type": "synthesized",
            },
        )

    has_clarify_event = any(
        event.get("kind") == "clarify" for event in state.get("branch_events", [])
    )
    update: RAGState = {
        "decomposed": False,
        "sub_queries": [query],
        "selected_branch_type": (
            state.get("selected_branch_type") if has_clarify_event else "initial"
        ),
    }
    if not has_clarify_event:
        update["branch_events"] = [
            {
                "phase": 0,
                "kind": "clarify",
                "index": 0,
                "branch": "initial",
                "status": "COMPLETED",
            }
        ]
    return _decompose_command(state, update)


def _decompose_command(
    state: RAGState,
    update: RAGState,
) -> Command[DecomposeTarget]:
    """Apply the decomposition update and fan out from the state it produces."""
    return Command(
        update=update,
        goto=route_after_decompose(cast(RAGState, cast(object, {**state, **update}))),
    )
