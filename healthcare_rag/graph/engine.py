"""Runtime engine for executing the healthcare StateGraph with isolated telemetry."""

from __future__ import annotations

import time
from importlib import import_module, metadata
from typing import Any, Protocol, Self
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult
from langgraph.checkpoint.memory import InMemorySaver
from langsmith import traceable

from healthcare_rag.graph.build import build_graph
from healthcare_rag.graph.engine_record import (
    ResultContext,
    TurnTiming,
    build_result,
    fold_branches,
)
from healthcare_rag.graph.history import LegacyTurn, seed_messages
from healthcare_rag.graph.resources import get as get_resources
from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.monitor import QueryMonitor
from healthcare_rag.processors.safety import scrub_phi

__all__ = ["Engine", "GraphEngine", "UsageRecorder", "build_engine", "fold_branches"]

class Engine(Protocol):
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: ...
    async def seed_history(self, thread_id: str, turns: list[LegacyTurn]) -> None: ...
    async def run_turn(
        self, thread_id: str, question: str, monitor: QueryMonitor | None = None
    ) -> dict[str, Any]: ...
    async def process_query(
        self, question: str, thread_id: str, monitor: QueryMonitor | None = None
    ) -> dict[str, Any]: ...
    def describe(self) -> dict[str, Any]: ...
    def history_used(self, thread_id: str) -> bool: ...
    async def aclose(self) -> None: ...


def _redact_root_inputs(inputs: dict[str, Any]) -> dict[str, str]:
    """Fail closed because LangSmith otherwise retains the original arguments."""
    try:
        return {"question": scrub_phi(inputs["question"])[0]}
    except Exception:  # noqa: BLE001 - mandated fail-closed tracing boundary.
        return {}


class UsageRecorder(AsyncCallbackHandler):
    """Mutable per-run callback accumulator; one instance belongs to one turn."""

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self._started: dict[UUID, tuple[float, str]] = {}

    async def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        del prompts, parent_run_id, tags, kwargs
        model = str((metadata or {}).get("ls_model_name") or serialized.get("name") or "?")
        self._started[run_id] = (time.perf_counter(), model)

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        del parent_run_id, tags, kwargs
        from evals.usage import LLMCallUsage

        started, model_hint = self._started.pop(run_id, (time.perf_counter(), "?"))
        output = response.llm_output or {}
        model = str(output.get("model_name") or output.get("model") or model_hint)
        usage: dict[str, Any] = {}
        if response.generations and response.generations[0]:
            message = getattr(response.generations[0][0], "message", None)
            usage = dict(getattr(message, "usage_metadata", None) or {})
        if not usage:
            usage = dict(output.get("token_usage") or {})
        details = usage.get("input_token_details") or {}
        self.calls.append(
            LLMCallUsage(
                model=model,
                prompt_tokens=int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
                cached_prompt_tokens=int(details.get("cache_read") or 0),
                latency_s=time.perf_counter() - started,
                kind="create",
            )
        )


class GraphEngine:
    """Own a compiled graph, its checkpointer lifecycle, and per-thread history."""

    def __init__(self, settings: GraphSettings | None = None) -> None:
        self.settings = settings or GraphSettings.from_env()
        self.compiled: Any = None
        self._sqlite_context: Any = None
        self._history_usage: dict[str, bool] = {}

    async def __aenter__(self) -> Self:
        if self.compiled is None:
            await self._initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        del exc_type, exc, traceback
        await self.aclose()

    async def _initialize(self) -> None:
        uri = self.settings.checkpoint_uri
        if uri.startswith("sqlite:"):
            try:
                module = import_module("langgraph.checkpoint.sqlite.aio")
            except ModuleNotFoundError as exc:
                message = "SQLite checkpointing requires: pip install healthcare-rag[graph-sqlite]"
                raise RuntimeError(message) from exc
            self._sqlite_context = module.AsyncSqliteSaver.from_conn_string(uri.removeprefix("sqlite:"))
            saver = await self._sqlite_context.__aenter__()
            await saver.setup()
        else:
            saver = InMemorySaver()
        self.compiled = build_graph().compile(checkpointer=saver, name="healthcare_rag")

    async def seed_history(self, thread_id: str, turns: list[LegacyTurn]) -> None:
        await self.__aenter__()
        await self.compiled.aupdate_state(
            {"configurable": {"thread_id": thread_id}},
            {"messages": seed_messages(turns)},
            as_node="finalize",
        )

    @traceable(name="healthcare_rag.process_query", process_inputs=_redact_root_inputs)
    async def run_turn(
        self, thread_id: str, question: str, monitor: QueryMonitor | None = None
    ) -> dict[str, Any]:
        return await self._run(thread_id, question, monitor)

    @traceable(name="healthcare_rag.process_query", process_inputs=_redact_root_inputs)
    async def process_query(
        self, question: str, thread_id: str, monitor: QueryMonitor | None = None
    ) -> dict[str, Any]:
        return await self._run(thread_id, question, monitor)

    async def _run(
        self, thread_id: str, question: str, monitor: QueryMonitor | None
    ) -> dict[str, Any]:
        await self.__aenter__()
        recorder = UsageRecorder()
        config = {"configurable": {"thread_id": thread_id}, "callbacks": [recorder]}
        started = time.perf_counter()
        first_answer: float | None = None
        finalized: float | None = None
        refusal = False
        error: str | None = None
        try:
            async for part in self.compiled.astream(
                {"question": question, "user_id": thread_id},
                config,
                stream_mode="updates",
                durability="exit",
            ):
                for node, update in part.items():
                    if monitor is not None:
                        monitor.update_status(node)
                    if node == "safety_gate" and update.get("safety_response"):
                        refusal = True
                    if node == "generate_answer" and first_answer is None:
                        first_answer = time.perf_counter()
                        if monitor is not None and not refusal:
                            generation = update.get("generation") or {}
                            monitor.raw_answer = str(generation.get("plain_answer") or "")
                            monitor.raw_answer_event.set()
                    if node == "finalize":
                        finalized = time.perf_counter()
                        if monitor is not None:
                            monitor.final_answer = update.get("answer")
                            monitor.follow_up_questions = update.get("follow_ups")
                            monitor.final_answer_event.set()
        except Exception as exc:  # noqa: BLE001 - eval contract records pipeline failures.
            error = f"{type(exc).__name__}: {exc}"
        snapshot = await self.compiled.aget_state(config)
        state = snapshot.values
        if monitor is not None and refusal:
            monitor.raw_answer_event.set()
        if monitor is not None and not monitor.final_answer_event.is_set():
            monitor.final_answer_event.set()
        result, used_history = build_result(
            state,
            recorder.calls,
            ResultContext(
                TurnTiming(started, first_answer, finalized, time.perf_counter()),
                self.settings,
                error,
            ),
        )
        self._history_usage[thread_id] = used_history
        return result

    def history_used(self, thread_id: str) -> bool:
        return self._history_usage.get(thread_id, False)

    def describe(self) -> dict[str, Any]:
        return {
            "engine": "graph",
            "langgraph_version": metadata.version("langgraph"),
            "safety": self.settings.safety_gate_enabled,
            "max_subqueries": self.settings.max_subqueries,
            "decompose_only_complex": self.settings.decompose_only_complex,
            "structured_strict": self.settings.structured_strict,
            "llm_model": self.settings.llm_model,
            "validator_model": self.settings.validator_model,
            "reasoning_effort": self.settings.reasoning_effort,
        }

    async def aclose(self) -> None:
        if self._sqlite_context is not None:
            await self._sqlite_context.__aexit__(None, None, None)
            self._sqlite_context = None
        await get_resources().aclose()


async def build_engine() -> Engine:
    """Build the LangGraph engine — the only runtime since the Phase-2 cleanup."""
    engine = GraphEngine()
    return await engine.__aenter__()
