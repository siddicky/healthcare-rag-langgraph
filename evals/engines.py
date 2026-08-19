"""Evaluation-facing engine protocol and legacy adapter."""

from __future__ import annotations

from typing import Any, Self

from healthcare_rag.graph.engine import Engine, GraphEngine, build_engine
from healthcare_rag.graph.history import LegacyTurn
from healthcare_rag.orch.monitor import QueryMonitor


class LegacyEngine:
    """Adapt the speculative orchestrator to the graph engine's result contract."""

    def __init__(self, rag: Any) -> None:
        self.rag = rag
        self._history_usage: dict[str, bool] = {}

    @classmethod
    async def create(cls) -> "LegacyEngine":
        from evals.harness import build_rag

        return cls(await build_rag())

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        await self.aclose()

    async def seed_history(self, thread_id: str, turns: list[LegacyTurn]) -> None:
        for turn in turns:
            self.rag.conversation_history.add_entry(
                thread_id,
                str(turn.get("user_query", "")),
                str(turn.get("answer", "")),
            )

    async def run_turn(
        self, thread_id: str, question: str, monitor: QueryMonitor | None = None
    ) -> dict[str, Any]:
        from evals.harness import _run_legacy_turn

        result, used_history = await _run_legacy_turn(self.rag, thread_id, question, monitor)
        self._history_usage[thread_id] = used_history
        return result

    async def process_query(
        self, question: str, thread_id: str, monitor: QueryMonitor | None = None
    ) -> dict[str, Any]:
        return await self.run_turn(thread_id, question, monitor)

    def history_used(self, thread_id: str) -> bool:
        return self._history_usage.get(thread_id, False)

    def describe(self) -> dict[str, Any]:
        from healthcare_rag.graph.settings import GraphSettings

        settings = GraphSettings.from_env()
        return {
            "engine": "legacy",
            "safety": settings.safety_gate_enabled,
            "max_subqueries": settings.max_subqueries,
            "decompose_only_complex": settings.decompose_only_complex,
            "structured_strict": settings.structured_strict,
            "llm_model": self.rag.generator.llm_model,
            "validator_model": self.rag.validator.llm_model,
            "reasoning_effort": settings.reasoning_effort,
        }

    async def aclose(self) -> None:
        await self.rag.weaviate_client.close()


__all__ = ["Engine", "GraphEngine", "LegacyEngine", "build_engine"]
