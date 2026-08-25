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
#
# The top-level coach is a real `create_agent`: without a live OPENAI_API_KEY
# this script stands in a fixture model that always calls `medical_lookup`,
# so the RAG child fixture below still exercises the relay end to end.

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Self

import anyio
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph

from healthcare_rag.agent import coach_agent as coach_agent_module
from healthcare_rag.agent import rag_relay as relay_module
from healthcare_rag.agent.build import build_coach_graph
from healthcare_rag.graph.state import GraphInput, GraphOutput, RAGState


class _ToolCapableFakeModel(FakeMessagesListChatModel):
    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Self:
        return self


class _StubGateway:
    def chat_model(self, *_args: object, **_kwargs: object) -> _ToolCapableFakeModel:
        return _ToolCapableFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "smoke-lookup",
                            "name": "medical_lookup",
                            "args": {"query": "What does the Lipitor monograph cover?"},
                        }
                    ],
                )
            ]
        )


class _StubResources:
    gateway = _StubGateway()


async def _offline_answer(state: RAGState) -> RAGState:
    return {
        "answer": f"Offline monograph answer for: {state.get('question', '')}",
        "follow_ups": ["What else does the monograph cover?"],
        "safety": {"contains_phi": False, "short_circuited": False},
        "error": None,
    }


async def main() -> None:
    child_builder = StateGraph(
        RAGState,
        input_schema=GraphInput,
        output_schema=GraphOutput,
    )
    _ = child_builder.add_node("offline_answer", _offline_answer)
    _ = child_builder.add_edge(START, "offline_answer")
    relay_module.child = child_builder.compile(checkpointer=True)
    coach_agent_module.get_resources = lambda: _StubResources()
    graph = build_coach_graph().compile(checkpointer=InMemorySaver())
    output = await graph.ainvoke(
        {"question": "What does the Lipitor monograph cover?"},
        {
            "configurable": {
                "thread_id": "coach-smoke-thread",
                "langgraph_auth_user": {"identity": "smoke-user"},
            }
        },
    )
    print(output["messages"][-1].text)


if __name__ == "__main__":
    anyio.run(main)
