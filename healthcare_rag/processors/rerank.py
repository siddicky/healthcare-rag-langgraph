"""Cross-encoder reranking over retrieved candidates (``HC_RAG_RERANKER=pinecone``).

Part of the *retrieval* stage, not a new pipeline stage: when it is on, each
collection search fetches ``rerank_candidates`` documents instead of 4 and this
module hands back the ``rerank_top_k`` the reranker liked best, so generation
still sees the same amount of context. Cost and latency therefore belong to
retrieval, which is what the A/B gate measures.

It is deliberately **fail-soft**: reranking is a re-ordering, so if Pinecone
Inference errors out the honest fallback is the search's own ordering, truncated
to ``top_k``. A rerank outage degrades quality, never availability.

Timing is surfaced two ways: a ``rerank_documents`` LangSmith child run (nested
inside the ``retrieve_documents`` retriever run, so per-arm wall-time shows up
in the trace tree) and a ``RERANK_APPLIED ... ms=`` log line. The runtime has no
other per-stage timing hook.
"""

from __future__ import annotations

import logging
import time
from functools import partial
from typing import Any

import anyio

from healthcare_rag.models.retrieval import QueryDocument
from healthcare_rag.services.tracing import traceable

logger = logging.getLogger("MedicalRAG")


def _ranked_field(entry: Any, name: str, default: Any = None) -> Any:
    value = getattr(entry, name, None)
    if value is None and isinstance(entry, dict):
        value = entry.get(name)
    return default if value is None else value


def reorder(docs: list[QueryDocument], ranking: Any, top_k: int) -> list[QueryDocument]:
    """Apply a Pinecone rerank result to ``docs``: reorder, restamp scores, truncate.

    ``ranking`` entries carry the *input* index of the document plus its rerank
    score. Out-of-range indices are skipped and a document is never emitted
    twice, so a malformed response degrades to a shorter list rather than a
    corrupt one.
    """
    entries = _ranked_field(ranking, "data", []) or []
    reranked: list[QueryDocument] = []
    seen: set[int] = set()
    for entry in entries:
        index = _ranked_field(entry, "index")
        if index is None:
            continue
        position = int(index)
        if position < 0 or position >= len(docs) or position in seen:
            continue
        seen.add(position)
        document = docs[position].model_copy(
            update={"score": float(_ranked_field(entry, "score", 0.0))}
        )
        reranked.append(document)
        if len(reranked) >= top_k:
            break
    return reranked


@traceable(name="rerank_documents", run_type="chain")
async def rerank_documents(
    resources: Any,
    query: str,
    docs: list[QueryDocument],
    top_k: int,
) -> list[QueryDocument]:
    """Rerank one collection's candidates down to ``top_k``; never raise."""
    if not docs:
        return []
    if top_k < 1:
        return []

    started = time.perf_counter()
    try:
        client = await resources.pinecone_client()
        model = resources.settings.rerank_model
        payload = [{"id": doc.doc_id, "text": doc.content} for doc in docs]
        ranking = await anyio.to_thread.run_sync(
            partial(
                client.inference.rerank,
                model=model,
                query=query,
                documents=payload,
                top_n=top_k,
                return_documents=False,
            )
        )
        reranked = reorder(docs, ranking, top_k)
    except Exception:  # fail-soft: a rerank outage must not fail the turn.
        logger.warning(
            "RERANK_FAILED candidates=%s kept=%s ms=%.0f",
            len(docs),
            min(top_k, len(docs)),
            (time.perf_counter() - started) * 1000,
            exc_info=True,
        )
        return docs[:top_k]

    if not reranked:
        logger.warning("RERANK_EMPTY candidates=%s; keeping search order", len(docs))
        return docs[:top_k]

    logger.info(
        "RERANK_APPLIED candidates=%s kept=%s ms=%.0f model=%s",
        len(docs),
        len(reranked),
        (time.perf_counter() - started) * 1000,
        resources.settings.rerank_model,
    )
    return reranked
