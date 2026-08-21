#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run from the repository root:
#      uv run python scripts/coach_smoke.py
# ──────────────────

from __future__ import annotations

import anyio
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph

from healthcare_rag.agent import gate
from healthcare_rag.agent import rag_relay as relay_module
from healthcare_rag.agent.build import build_coach_graph
from healthcare_rag.graph.state import GraphInput, GraphOutput, RAGState
from healthcare_rag.models.safety import SafetyAssessment


async def _offline_answer(state: RAGState) -> RAGState:
    return {
        "answer": f"Offline monograph answer for: {state.get('question', '')}",
        "follow_ups": ["What else does the monograph cover?"],
        "safety": {"contains_phi": False, "short_circuited": False},
        "error": None,
    }


async def _classify(**_kwargs: str) -> SafetyAssessment:
    return SafetyAssessment(
        category="in_scope_informational",
        contains_phi=False,
        phi_spans=[],
        drug_mentioned="lipitor",
        rationale="offline smoke",
    )


async def main() -> None:
    child_builder = StateGraph(
        RAGState,
        input_schema=GraphInput,
        output_schema=GraphOutput,
    )
    _ = child_builder.add_node("offline_answer", _offline_answer)
    _ = child_builder.add_edge(START, "offline_answer")
    relay_module.child = child_builder.compile(checkpointer=True)
    gate.GATEWAY = _classify
    graph = build_coach_graph().compile(checkpointer=InMemorySaver())
    output = await graph.ainvoke(
        {"question": "What does the Lipitor monograph cover?"},
        {"configurable": {"thread_id": "coach-smoke-thread"}},
    )
    print(output["messages"][-1].text)


if __name__ == "__main__":
    anyio.run(main)
