from __future__ import annotations

from typing import Any

import pytest

from evals.multiturn_harness import run_turn
from evals.run_multiturn import _slim
from healthcare_rag.graph.engine import GraphEngine
from healthcare_rag.graph.history import LegacyTurn
from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.monitor import QueryMonitor


class _RecordEngine:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    async def seed_history(self, thread_id: str, turns: list[LegacyTurn]) -> None:
        del thread_id, turns

    async def run_turn(
        self, thread_id: str, question: str, monitor: QueryMonitor | None = None
    ) -> dict[str, Any]:
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
            "safety_outcome": {"category": "in_scope_informational"},
            "error": None,
            "n_branches": 1,
            "branch_types": ["initial"],
            "branch_statuses": ["COMPLETED"],
            "selected_branch_type": "initial",
            "selected_branch_query": "question",
        }

    def history_used(self, thread_id: str) -> bool:
        return thread_id == "thread"

    async def process_query(
        self, question: str, thread_id: str, monitor: QueryMonitor | None = None
    ) -> dict[str, Any]:
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
        "safety_outcome",
        "error",
        "used_history",
        "n_branches",
        "selected_branch_type",
    }


def test_multiturn_slim_preserves_safety_outcome_for_every_turn() -> None:
    outputs = {
        "turns": [
            {"index": 1, "contexts": ["chunk"], "safety_outcome": {"category": "first"}},
            {"index": 2, "contexts": ["chunk"], "safety_outcome": {"category": "second"}},
        ]
    }

    slimmed = _slim(outputs)

    assert [turn["safety_outcome"] for turn in slimmed["turns"]] == [
        {"category": "first"},
        {"category": "second"},
    ]


def test_engine_description_identifies_the_graph_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HC_RAG_REFUSAL_BOUNDARY", "false")
    description = GraphEngine(GraphSettings.from_env()).describe()

    assert description["engine"] == "graph"
    assert description["refusal_boundary_enabled"] is False
