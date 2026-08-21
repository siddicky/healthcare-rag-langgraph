from __future__ import annotations

from typing import TypeAlias

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import StateGraph as StateGraphType

from .documents import claim_document, review_document
from .gate import RouteTarget, coach_gate
from .rag_relay import rag_relay
from .state import CoachInput, CoachOutput, CoachState

CoachBuilder: TypeAlias = StateGraphType[CoachState, None, CoachInput, CoachOutput]


def _pending_route(state: CoachState) -> CoachState:
    del state
    return {}


def build_coach_graph() -> CoachBuilder:
    builder: CoachBuilder = StateGraph(
        CoachState,
        input_schema=CoachInput,
        output_schema=CoachOutput,
    )
    _ = builder.add_node("coach_gate", coach_gate, input_schema=CoachState)
    _ = builder.add_node("rag_relay", rag_relay, input_schema=CoachState)
    _ = builder.add_node("claim_document", claim_document, input_schema=CoachState)
    _ = builder.add_node("review_document", review_document, input_schema=CoachState)
    _ = builder.add_edge("rag_relay", END)
    _ = builder.add_edge("review_document", END)
    pending: tuple[RouteTarget, ...] = (
        "_pending_short_circuit",
        "_pending_coach_agent",
        "_pending_erase_my_data",
        "_pending_reminder_delivery",
    )
    for node_name in pending:
        _ = builder.add_node(node_name, _pending_route, input_schema=CoachState)
        _ = builder.add_edge(node_name, END)
    _ = builder.add_edge(START, "coach_gate")
    return builder


coach = build_coach_graph().compile(name="coach")
