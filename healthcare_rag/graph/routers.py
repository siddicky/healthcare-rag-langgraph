"""Pure graph routing decisions and canonical node names.

A node that both updates state and picks its successor returns
``Command[Literal[...]]``; it computes ``goto`` by calling the router here on its
own post-update state, so the decision logic stays pure and testable and
``build.py`` wires no edge for it. LangGraph reads the ``Literal`` inside
``Command`` to render those dynamic edges, so each such node annotates one of the
aliases below (``GateTarget``, ``DecomposeTarget``, ``MergeTarget``,
``EvaluateCommandTarget``) — a bare ``X | Y`` union of literals is *not* read.
``validate_answer`` is the exception: its "finalize" target is ``finalize`` in the
public graph and ``END`` in the bare pipeline, so it stays on a conditional edge
with an explicit path map. ``tests/graph/test_router_typing.py`` pins every
literal to the node constants, to the nodes' ``Command`` annotations and to the
compiled graph's edges, so the three cannot drift apart silently.
"""

from typing import Final, Literal

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

# Router target types. The string values must equal the NODE_* constants above
# (pinned by tests); Literal cannot reference the constants directly.
GateTerminalTarget = Literal["finalize"]
GateFanOutTarget = Literal["clarify_query", "extract_conversation_context"]
MergeTarget = Literal["evaluate_retrieval", "generate_answer"]
EvaluateTarget = Literal["generate_answer"]
ValidateTarget = Literal["generate_follow_ups", "finalize"]
DecomposeTarget = Literal["retrieve_documents"]

# ``Command[...]`` annotations for the nodes that route themselves. LangGraph only
# reads the targets when the argument's origin is ``Literal``, so a branch with two
# shapes of destination is spelled as one *nested* Literal (which flattens) rather
# than as a union of literals.
GateTarget = Literal[GateTerminalTarget, GateFanOutTarget]
EvaluateCommandTarget = Literal[EvaluateTarget, DecomposeTarget]
NodeName = Literal[
    "safety_gate",
    "clarify_query",
    "extract_conversation_context",
    "decompose_query",
    "retrieve_documents",
    "merge_retrievals",
    "evaluate_retrieval",
    "generate_answer",
    "validate_answer",
    "generate_follow_ups",
    "finalize",
]


def route_after_gate(
    state: RAGState,
) -> GateTerminalTarget | list[GateFanOutTarget]:
    """Route refusals to their terminal path and safe queries to preprocessing.

    A refusal goes to one terminal node; a safe query fans out to both
    preprocessing nodes in parallel. ``safety_gate`` calls this for the ``goto``
    of its ``Command[GateTarget]``.
    """
    if state.get("safety_response"):
        return "finalize"
    return ["clarify_query", "extract_conversation_context"]


def route_after_decompose(state: RAGState) -> list[Send]:
    """Fan out the parent query first, followed by capped decomposition queries.

    ``decompose_query`` calls this for the ``goto`` of its
    ``Command[DecomposeTarget]``; every ``Send`` targets ``retrieve_documents``.
    """
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


def route_after_merge(state: RAGState) -> MergeTarget:
    """Skip a second evaluation after gap-fill retrievals are merged.

    ``merge_retrievals`` calls this for the ``goto`` of its ``Command[MergeTarget]``.
    """
    return "generate_answer" if state.get("gap_filled") else "evaluate_retrieval"


def route_after_evaluate(state: RAGState) -> list[Send] | EvaluateTarget:
    """Launch one capped gap-fill round or continue directly to generation.

    ``evaluate_retrieval`` calls this for the ``goto`` of its
    ``Command[EvaluateCommandTarget]``, whose Literal names ``retrieve_documents``
    alongside ``generate_answer`` because the ``Send`` targets carry it.
    """
    if not state.get("gap_pending"):
        return "generate_answer"
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


def route_after_validate(state: RAGState) -> ValidateTarget:
    """Generate follow-ups only for validated answers.

    The one router still wired as a conditional edge: ``build.py`` maps
    ``"finalize"`` onto the builder's terminal (``finalize`` in the public graph,
    ``END`` in the bare pipeline), which a fixed ``Command`` Literal cannot express.
    """
    return "generate_follow_ups" if state.get("validated") else "finalize"
