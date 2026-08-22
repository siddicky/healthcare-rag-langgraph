from __future__ import annotations

from typing import TypeAlias

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import StateGraph as StateGraphType
from langgraph.store.base import BaseStore
from langgraph.types import Command

from .coach_agent import coach_agent
from .documents import claim_document, review_document
from .erase import erase_my_data
from .finalize import finalize_coach
from .gate import RouteTarget, coach_gate
from .rag_relay import rag_relay
from .reminders import reminder_delivery
from .short_circuit import short_circuit
from .state import CoachInput, CoachOutput, CoachState

CoachBuilder: TypeAlias = StateGraphType[CoachState, None, CoachInput, CoachOutput]


async def _coach_gate_with_store(
    state: CoachState, config: RunnableConfig, *, store: BaseStore
) -> Command[RouteTarget]:
    return await coach_gate(state, config, store=store)


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
    _ = builder.add_node("short_circuit", short_circuit, input_schema=CoachState)
    _ = builder.add_node("coach_agent", coach_agent, input_schema=CoachState)
    _ = builder.add_node("erase_my_data", erase_my_data, input_schema=CoachState)
    _ = builder.add_node("finalize", finalize_coach, input_schema=CoachState)
    for node_name in (
        "rag_relay",
        "review_document",
        "reminder_delivery",
        "short_circuit",
        "coach_agent",
        "erase_my_data",
    ):
        _ = builder.add_edge(node_name, "finalize")
    _ = builder.add_edge("finalize", END)
    _ = builder.add_edge(START, "coach_gate")
    return builder


coach = build_coach_graph().compile(name="coach")
