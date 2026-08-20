from __future__ import annotations

from typing import Any

import pytest

from evals.multiturn_harness import run_turn
from healthcare_rag.graph.engine import GraphEngine
from healthcare_rag.graph.resources import get


class _RecordEngine:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    async def seed_history(self, thread_id: str, turns: list[dict[str, Any]]) -> None:
        del thread_id, turns

    async def run_turn(self, thread_id: str, question: str, monitor=None) -> dict[str, Any]:
        del thread_id, question, monitor
        return {
            "answer": "answer",
            "answered": True,
            "raw_answer": "raw",
            "follow_ups": [],
            "contexts": [],
            "retrieved_chunk_ids": [],
            "retrieved_pages": [],
            "retrieved_sources": [],
            "latency_s": 0.1,
            "time_to_first_answer_s": 0.05,
            "usage": {},
            "per_call_usage": [],
            "safety_outcome": None,
            "error": None,
            "n_branches": 1,
            "branch_types": ["initial"],
            "branch_statuses": ["COMPLETED"],
            "selected_branch_type": "initial",
            "selected_branch_query": "question",
        }

    def history_used(self, thread_id: str) -> bool:
        return thread_id == "thread"

    async def process_query(self, question: str, thread_id: str, monitor=None) -> dict[str, Any]:
        return await self.run_turn(thread_id, question, monitor)

    def describe(self) -> dict[str, Any]:
        return {"engine": "record"}

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_multiturn_adapter_adds_used_history_only_to_turn_record() -> None:
    turn = await run_turn(_RecordEngine(), "thread", "question", 1)

    assert turn["used_history"] is True
    assert set(turn) == {
        "index",
        "user",
        "answer",
        "answered",
        "raw_answer",
        "follow_ups",
        "contexts",
        "retrieved_chunk_ids",
        "retrieved_pages",
        "retrieved_sources",
        "latency_s",
        "time_to_first_answer_s",
        "usage",
        "error",
        "used_history",
        "n_branches",
        "selected_branch_type",
    }


def test_engine_description_identifies_the_graph_runtime() -> None:
    assert GraphEngine(get().settings).describe()["engine"] == "graph"
