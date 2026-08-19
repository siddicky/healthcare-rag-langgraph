"""
Eval harness: builds the RAG system once and exposes a LangSmith-compatible
``target`` coroutine that runs one example through the *real* orchestrator and
returns everything the evaluators need (answer, retrieved contexts, branch info,
latency, token usage, estimated cost).

Nothing here changes application behaviour; it only observes it.
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import logging
import os
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Optional

from dotenv import load_dotenv

from healthcare_rag.config import setup_medical_rag
from healthcare_rag.orch.monitor import QueryMonitor
from healthcare_rag.orch.orchestrator import RefactoredOrchestrator
from healthcare_rag.pipeline.medical_rag import MedicalRAG
from healthcare_rag.storage.history import ConversationHistory

if TYPE_CHECKING:
    from healthcare_rag.graph.engine import Engine

from .usage import LLMCallUsage, summarize_usage

load_dotenv()

logger = logging.getLogger("evals")


# --------------------------------------------------------------------------- #
# Token / cost accounting                                                      #
# --------------------------------------------------------------------------- #

# A per-example collector list, propagated to child asyncio tasks via contextvars
# (asyncio.create_task copies the current context, and the *list object* is shared).
_usage_sink: contextvars.ContextVar[Optional[list[LLMCallUsage]]] = contextvars.ContextVar(
    "usage_sink", default=None
)


def _record_usage(response: Any, model_hint: str, kind: str, latency_s: float) -> None:
    sink = _usage_sink.get()
    if sink is None:
        return
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    sink.append(
        LLMCallUsage(
            model=getattr(response, "model", None) or model_hint,
            prompt_tokens=usage.prompt_tokens or 0,
            completion_tokens=usage.completion_tokens or 0,
            cached_prompt_tokens=cached,
            latency_s=latency_s,
            kind=kind,
        )
    )


def instrument_openai_client(client: Any) -> Any:
    """Patch ``chat.completions.create`` and ``beta.chat.completions.parse`` on an
    (already possibly LangSmith-wrapped) AsyncOpenAI client to record usage.

    Idempotent: safe to call more than once.
    """
    if getattr(client, "_evals_instrumented", False):
        return client

    orig_create = client.chat.completions.create

    async def create(*args: Any, **kwargs: Any) -> Any:
        t0 = time.perf_counter()
        resp = await orig_create(*args, **kwargs)
        if not kwargs.get("stream"):
            _record_usage(resp, kwargs.get("model", "?"), "create", time.perf_counter() - t0)
        return resp

    client.chat.completions.create = create

    beta_completions = getattr(getattr(client, "beta", None), "chat", None)
    beta_completions = getattr(beta_completions, "completions", None)
    if beta_completions is not None and hasattr(beta_completions, "parse"):
        orig_parse = beta_completions.parse

        async def parse(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            resp = await orig_parse(*args, **kwargs)
            _record_usage(resp, kwargs.get("model", "?"), "parse", time.perf_counter() - t0)
            return resp

        beta_completions.parse = parse

    client._evals_instrumented = True
    return client


# --------------------------------------------------------------------------- #
# RAG construction                                                             #
# --------------------------------------------------------------------------- #

async def build_rag(history_dir: Optional[str] = None) -> MedicalRAG:
    """Build the production MedicalRAG (Weaviate + OpenAI) with an isolated
    conversation-history directory so eval runs never touch data/conversations."""
    rag = await setup_medical_rag()
    history_dir = history_dir or tempfile.mkdtemp(prefix="hc_rag_eval_history_")
    rag.conversation_history = ConversationHistory(history_dir)
    instrument_openai_client(rag.async_client)
    return rag


def _extract_contexts(
    orch: RefactoredOrchestrator,
) -> tuple[list[dict[str, Any]], Optional[str], dict[str, Any]]:
    """Pull retrieved docs + raw answer from the branch that produced the final
    answer (falling back to the most recent branch with results)."""
    branch = None
    if orch.final_answer_source_branch_id:
        branch = orch.branches.get(orch.final_answer_source_branch_id)
    if branch is None:
        candidates = [b for b in orch.branches.values() if b.merged_results is not None]
        branch = candidates[-1] if candidates else None

    contexts: list[dict[str, Any]] = []
    raw_answer = None
    if branch is not None:
        results = branch.merged_results or branch.retrieved_results
        if results is not None:
            for qr in results.results:
                for d in qr.docs:
                    meta = d.metadata or {}
                    contexts.append(
                        {
                            "content": d.content,
                            "source": d.source_name,
                            "chunk_id": meta.get("id_"),
                            "page_numbers": d.page_numbers or [],
                            "score": d.score,
                            "routed_query": qr.query,
                        }
                    )
        if branch.raw_answer is not None:
            raw_answer = branch.raw_answer.plain_answer

    branch_info = {
        "n_branches": len(orch.branches),
        "branch_types": [b.branch_type for b in orch.branches.values()],
        "branch_statuses": [b.status.name for b in orch.branches.values()],
        "selected_branch_type": branch.branch_type if branch else None,
        "selected_branch_query": branch.query if branch else None,
    }
    return contexts, raw_answer, branch_info


async def _run_legacy_turn(
    rag: MedicalRAG,
    user_id: str,
    question: str,
    monitor: QueryMonitor | None = None,
) -> tuple[dict[str, Any], bool]:
    """Capture one legacy orchestrator turn behind the engine adapter."""
    sink: list[LLMCallUsage] = []
    token = _usage_sink.set(sink)
    monitor = monitor or QueryMonitor()
    orch = RefactoredOrchestrator(rag)

    t0 = time.perf_counter()
    first_answer_at: dict[str, Optional[float]] = {"t": None}

    async def _watch_first_answer() -> None:
        await monitor.raw_answer_event.wait()
        first_answer_at["t"] = time.perf_counter() - t0

    watcher = asyncio.create_task(_watch_first_answer())
    error: Optional[str] = None
    answer: Optional[str] = None
    follow_ups: Optional[list[str]] = None
    try:
        answer, follow_ups = await orch.process_query(question, user_id, monitor)
    except Exception as exc:  # keep the eval running; the failure is itself a datapoint
        logger.exception("Pipeline raised for question %r", question)
        error = f"{type(exc).__name__}: {exc}"
    finally:
        latency_s = time.perf_counter() - t0
        watcher.cancel()
        _usage_sink.reset(token)

    contexts, raw_answer, branch_info = _extract_contexts(orch)
    usage = summarize_usage(sink)
    # What the runtime safety gate decided about this query (category, contains_phi,
    # short_circuited, ...). None when the gate is disabled (HC_RAG_SAFETY_GATE=false).
    safety = getattr(orch, "safety_outcome", None)

    result = {
        "answer": answer,
        "answered": bool(answer),
        "raw_answer": raw_answer,
        "follow_ups": follow_ups or [],
        "contexts": contexts,
        "retrieved_chunk_ids": [c["chunk_id"] for c in contexts if c["chunk_id"] is not None],
        "retrieved_pages": sorted({p for c in contexts for p in c["page_numbers"]}),
        "retrieved_sources": sorted({c["source"] for c in contexts}),
        "latency_s": round(latency_s, 3),
        "time_to_first_answer_s": (
            round(first_answer_at["t"], 3) if first_answer_at["t"] is not None else None
        ),
        "usage": usage,
        "per_call_usage": [dataclasses.asdict(c) | {"cost_usd": c.cost_usd} for c in sink],
        "safety_outcome": safety.model_dump() if safety is not None else None,
        "error": error,
        **branch_info,
    }
    return result, bool(getattr(orch.summary_result, "required_context", False))


async def run_one(
    engine: Engine,
    question: str,
    history: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Run one question through either engine with the legacy result contract."""
    user_id = f"eval_{uuid.uuid4().hex[:10]}"
    await engine.seed_history(
        user_id,
        [
            {
                "user_query": turn["question"],
                "answer": turn["answer"],
            }
            for turn in history or []
        ],
    )
    return await engine.run_turn(user_id, question)


def make_target(
    engine: Engine,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return the async target callable expected by ``langsmith.aevaluate``."""

    async def target(inputs: dict[str, Any]) -> dict[str, Any]:
        return await run_one(engine, inputs["question"], inputs.get("history") or [])

    return target
