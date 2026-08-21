from __future__ import annotations

from typing import TypeAlias

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import StateGraph as StateGraphType
from langgraph.store.base import BaseStore
from langgraph.types import Command

from .documents import claim_document, review_document
from .gate import RouteTarget, coach_gate
from .rag_relay import rag_relay
from .reminders import reminder_delivery
from .state import CoachInput, CoachOutput, CoachState

CoachBuilder: TypeAlias = StateGraphType[CoachState, None, CoachInput, CoachOutput]


async def _coach_gate_with_store(
    state: CoachState, config: RunnableConfig, *, store: BaseStore
) -> Command[RouteTarget]:
    return await coach_gate(state, config, store=store)


def _pending_route(state: CoachState) -> CoachState:
    del state
    return {}


def build_coach_graph() -> CoachBuilder:
    builder: CoachBuilder = StateGraph(
        CoachState,
        input_schema=CoachInput,
        output_schema=CoachOutput,
    )
    _ = builder.add_node("coach_gate", _coach_gate_with_store, input_schema=CoachState)
    _ = builder.add_node("rag_relay", rag_relay, input_schema=CoachState)
    _ = builder.add_node("claim_document", claim_document, input_schema=CoachState)
    _ = builder.add_node("review_document", review_document, input_schema=CoachState)
    _ = builder.add_node(
        "reminder_delivery", reminder_delivery, input_schema=CoachState
    )
    _ = builder.add_edge("rag_relay", END)
    _ = builder.add_edge("review_document", END)
    _ = builder.add_edge("reminder_delivery", END)
    pending: tuple[RouteTarget, ...] = (
        "_pending_short_circuit",
        "_pending_coach_agent",
        "_pending_erase_my_data",
    )
    for node_name in pending:
        _ = builder.add_node(node_name, _pending_route, input_schema=CoachState)
        _ = builder.add_edge(node_name, END)
    _ = builder.add_edge(START, "coach_gate")
    return builder


coach = build_coach_graph().compile(name="coach")
