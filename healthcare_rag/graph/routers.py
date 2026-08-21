"""Pure graph routing decisions and canonical node names."""

from typing import Final

from langgraph.types import Send

from healthcare_rag.graph.resources import get as get_resources
from healthcare_rag.graph.state import RAGState, RetrieveInput

NODE_SAFETY: Final = "safety_gate"
NODE_CLARIFY: Final = "clarify_query"
NODE_CONTEXT: Final = "extract_conversation_context"
NODE_DECOMPOSE: Final = "decompose_query"
NODE_RETRIEVE: Final = "retrieve_documents"
NODE_MERGE: Final = "merge_retrievals"
NODE_EVALUATE: Final = "evaluate_retrieval"
NODE_GENERATE: Final = "generate_answer"
NODE_VALIDATE: Final = "validate_answer"
NODE_FOLLOW_UPS: Final = "generate_follow_ups"
NODE_FINALIZE: Final = "finalize"

_GAP_FILL_CAP: Final = 3


def route_after_gate(state: RAGState) -> list[str] | str:
    """Route refusals to their terminal path and safe queries to preprocessing."""
    if state.get("safety_response"):
        return NODE_FINALIZE
    return [NODE_CLARIFY, NODE_CONTEXT]


def route_after_decompose(state: RAGState) -> list[Send]:
    """Fan out the parent query first, followed by capped decomposition queries."""
    cap = get_resources().settings.max_subqueries
    parent_kind = (
        "clarified" if state.get("selected_branch_type") == "clarified" else "initial"
    )
    parent: RetrieveInput = {
        "query": state.get("working_query", ""),
        "index": 0,
        "kind": parent_kind,
        "phase": 0,
        "branch": parent_kind,
    }
    sends = [Send(NODE_RETRIEVE, parent)]
    if state.get("decomposed"):
        sends.extend(
            Send(
                NODE_RETRIEVE,
                RetrieveInput(
                    query=query,
                    index=index,
                    kind="decomposed",
                    phase=0,
                    branch=f"decomposed_{index - 1}",
                ),
            )
            for index, query in enumerate(
                state.get("sub_queries", [])[:cap], start=1
            )
        )
    return sends


def route_after_merge(state: RAGState) -> str:
    """Skip a second evaluation after gap-fill retrievals are merged."""
    return NODE_GENERATE if state.get("gap_filled") else NODE_EVALUATE


def route_after_evaluate(state: RAGState) -> list[Send] | str:
    """Launch one capped gap-fill round or continue directly to generation."""
    if not state.get("gap_pending"):
        return NODE_GENERATE
    evaluation = state.get("evaluation") or {}
    match evaluation.get("additional_queries"):
        case list() as queries:
            string_queries: list[str] = []
            for query in queries:
                match query:
                    case str() as text:
                        string_queries.append(text)
                    case _:
                        continue
        case _:
            string_queries = []
    return [
        Send(
            NODE_RETRIEVE,
            RetrieveInput(
                query=query,
                index=index,
                kind="gap_fill",
                phase=1,
                branch="gap_fill",
            ),
        )
        for index, query in enumerate(string_queries[:_GAP_FILL_CAP])
    ]


def route_after_validate(state: RAGState) -> str:
    """Generate follow-ups only for validated answers."""
    return NODE_FOLLOW_UPS if state.get("validated") else NODE_FINALIZE
