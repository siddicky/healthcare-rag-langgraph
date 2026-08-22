"""
Optional LangSmith tracing for the healthcare RAG pipeline.

Tracing is opt-in and only available with LangSmith input hiding:

    LANGSMITH_TRACING=true
    LANGSMITH_HIDE_INPUTS=true
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
from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger("MedicalRAG")

P = ParamSpec("P")
R = TypeVar("R")

_TRACING_ENVIRONMENT_VARIABLES = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
)


def _is_enabled(variable: str) -> bool:
    """Match LangSmith's exact environment opt-in convention."""
    return os.getenv(variable) == "true"


def tracing_enabled() -> bool:
    """True when LangSmith tracing has been requested via env vars."""
    return any(_is_enabled(variable) for variable in _TRACING_ENVIRONMENT_VARIABLES)


def enforce_input_hiding() -> bool:
    """Disable environment tracing unless LangSmith is configured to hide inputs."""
    if tracing_enabled() and not _is_enabled("LANGSMITH_HIDE_INPUTS"):
        for variable in _TRACING_ENVIRONMENT_VARIABLES:
            os.environ[variable] = "false"
    return tracing_enabled()


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


def traceable(
    *t_args: Any, **t_kwargs: Any
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Drop-in for ``langsmith.traceable`` that is a no-op when tracing is off.

    Usage mirrors langsmith::

        @traceable(name="clarify_query", run_type="chain")
        async def clarify_query(...): ...
    """

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        if not tracing_enabled():
            return fn
        try:
            from langsmith import traceable as ls_traceable
        except ImportError:  # pragma: no cover
            return fn
        traced = ls_traceable(*t_args, **t_kwargs)(fn)

        @wraps(fn)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            return traced(*args, **kwargs)

        return wrapped

    return decorator


def query_result_list_to_documents(result: Any) -> dict[str, list[dict[str, Any]]]:
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
    except Exception as exc:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK - fail-soft tracing adapter.
        logger.debug(f"Could not convert retrieval result for tracing: {exc}")
    return {"documents": documents}


def rag_stage(
    name: str, run_type: str = "chain", **extra: Any
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator naming a pipeline stage for tracing (no-op when disabled)."""
    return traceable(name=name, run_type=run_type, **extra)


__all__ = [
    "enforce_input_hiding",
    "query_result_list_to_documents",
    "rag_stage",
    "traceable",
    "tracing_enabled",
    "wrap_openai_client",
]
