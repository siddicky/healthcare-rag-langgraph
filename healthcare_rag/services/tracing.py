"""
Optional LangSmith tracing for the healthcare RAG pipeline.

Tracing is opt-in and controlled entirely by environment variables:

    LANGSMITH_TRACING=true
    LANGSMITH_API_KEY=lsv2_...
    LANGSMITH_PROJECT=healthcare-rag        # optional, defaults to "default"

When tracing is disabled (the default) every helper here degrades to a no-op,
so the application has no hard runtime dependency on the ``langsmith`` package.

What gets traced when enabled:
  * Every OpenAI chat/parse call (via ``langsmith.wrappers.wrap_openai``) with
    token usage, so LangSmith can compute per-call cost.
  * Each orchestrator stage (clarify / decompose / retrieve / evaluate / answer /
    validate / follow-ups) as a named child run — see ``healthcare_rag/orch/tasks.py``.
  * The top-level ``RefactoredOrchestrator.process_query`` as the root run.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, TypeVar

logger = logging.getLogger("MedicalRAG")

F = TypeVar("F", bound=Callable[..., Any])


def tracing_enabled() -> bool:
    """True when LangSmith tracing has been requested via env vars."""
    flag = os.getenv("LANGSMITH_TRACING", os.getenv("LANGCHAIN_TRACING_V2", ""))
    return flag.strip().lower() in {"1", "true", "yes"}


def wrap_openai_client(client: Any) -> Any:
    """Wrap an OpenAI client so its calls are recorded as LangSmith LLM runs.

    Returns the client unchanged when tracing is off or langsmith is missing.
    """
    if not tracing_enabled():
        return client
    try:
        from langsmith.wrappers import wrap_openai
    except ImportError:  # pragma: no cover - only hit when langsmith not installed
        logger.warning("LANGSMITH_TRACING is set but the 'langsmith' package is not installed.")
        return client
    return wrap_openai(client)


def traceable(*t_args: Any, **t_kwargs: Any) -> Callable[[F], F]:
    """Drop-in for ``langsmith.traceable`` that is a no-op when tracing is off.

    Usage mirrors langsmith::

        @traceable(name="clarify_query", run_type="chain")
        async def clarify_query(...): ...
    """

    def decorator(fn: F) -> F:
        if not tracing_enabled():
            return fn
        try:
            from langsmith import traceable as ls_traceable
        except ImportError:  # pragma: no cover
            return fn
        return ls_traceable(*t_args, **t_kwargs)(fn)  # type: ignore[return-value]

    return decorator


def query_result_list_to_documents(result: Any) -> dict:
    """Convert a ``QueryResultList`` into LangSmith's retriever output shape.

    LangSmith renders ``run_type="retriever"`` runs nicely when their output is
    ``{"documents": [{"page_content": ..., "metadata": {...}}, ...]}``.
    """
    documents = []
    try:
        for qr in getattr(result, "results", []) or []:
            for doc in getattr(qr, "docs", []) or []:
                meta = dict(doc.metadata or {})
                meta.update(
                    {
                        "source": doc.source_name,
                        "doc_id": doc.doc_id,
                        "score": doc.score,
                        "page_numbers": doc.page_numbers,
                        "routed_query": qr.query,
                    }
                )
                documents.append({"page_content": doc.content, "metadata": meta})
    except Exception as exc:  # never let tracing break the pipeline
        logger.debug(f"Could not convert retrieval result for tracing: {exc}")
    return {"documents": documents}


def rag_stage(name: str, run_type: str = "chain", **extra: Any) -> Callable[[F], F]:
    """Decorator naming a pipeline stage for tracing (no-op when disabled)."""
    return traceable(name=name, run_type=run_type, **extra)


__all__ = [
    "tracing_enabled",
    "wrap_openai_client",
    "traceable",
    "rag_stage",
    "query_result_list_to_documents",
]
