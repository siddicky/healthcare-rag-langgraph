from __future__ import annotations

from typing import TypeAlias

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import StateGraph as StateGraphType

from .gate import RouteTarget, coach_gate
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
    pending: tuple[RouteTarget, ...] = (
        "_pending_short_circuit",
        "_pending_rag_relay",
        "_pending_coach_agent",
        "_pending_erase_my_data",
        "_pending_claim_document",
        "_pending_reminder_delivery",
    )
    for node_name in pending:
        _ = builder.add_node(node_name, _pending_route, input_schema=CoachState)
        _ = builder.add_edge(node_name, END)
    _ = builder.add_edge(START, "coach_gate")
    return builder


coach = build_coach_graph().compile(name="coach")
