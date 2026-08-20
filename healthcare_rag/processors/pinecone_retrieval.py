"""Pinecone serverless hybrid retrieval arm (``HC_RAG_RETRIEVER=pinecone``).

The A/B alternative to Weaviate hybrid search over the *same* chunks. One
namespace per collection in one serverless index (built by
``make ingest-pinecone``); each query is embedded twice — dense with the same
OpenAI ``text-embedding-3-small`` Weaviate's vectoriser uses, sparse with
Pinecone Inference — and the two halves are combined by **convex scaling**
(``dense *= alpha``, ``sparse *= 1 - alpha``), Pinecone's documented substitute
for Weaviate's ``alpha`` on a dotproduct index.

``pinecone_search`` mirrors ``hybrid_search``'s signature so the two arms are
interchangeable at ``Resources.hybrid_search``; its first argument is the
Pinecone index handle rather than a Weaviate client.

The Pinecone SDK is synchronous, so every call crosses ``anyio.to_thread``.
That is deliberate: the asyncio client's aiohttp session is bound to the event
loop that opened it, which does not survive the lazily-constructed,
process-wide ``Resources`` singleton that outlives individual loops.
"""

from __future__ import annotations

import logging
from functools import partial
from threading import Lock
from typing import Any

import anyio

from healthcare_rag.models.retrieval import (
    QueryDocument,
    QueryResult,
    QueryResultList,
)
from healthcare_rag.storage.pinecone_store import (
    dense_embeddings,
    namespace_for,
    sparse_embeddings,
)

logger = logging.getLogger("MedicalRAG")

DEFAULT_SEARCH_LIMIT = 4

_openai_client: Any = None
_CLIENT_LOCK = Lock()


def embedding_client(settings: Any) -> Any:
    """Process-wide sync OpenAI client for query embeddings.

    Sync on purpose: it is only ever called from a worker thread, so it has no
    event-loop affinity and can be cached for the life of the process.
    """
    global _openai_client
    with _CLIENT_LOCK:
        if _openai_client is None:
            from openai import OpenAI

            api_key = getattr(settings, "openai_api_key", "") or ""
            if not api_key:
                message = "OPENAI_API_KEY is not set"
                raise ValueError(message)
            _openai_client = OpenAI(api_key=api_key)
        return _openai_client


def reset_embedding_client() -> None:
    """Drop the cached OpenAI client (tests, or after a settings change)."""
    global _openai_client
    with _CLIENT_LOCK:
        _openai_client = None


def convex_scale(
    dense: list[float], sparse: dict[str, list[Any]], alpha: float
) -> tuple[list[float], dict[str, list[Any]]]:
    """Weight the two halves of a hybrid query: ``dense *= alpha``, ``sparse *= 1 - alpha``.

    ``alpha == 1`` is dense-only, ``alpha == 0`` sparse-only. Pinecone scores a
    hybrid query as the dotproduct sum of both halves, so scaling the *vectors*
    is what makes ``alpha`` mean the same thing it means on the Weaviate arm.
    """
    if not 0.0 <= alpha <= 1.0:
        message = f"alpha must be between 0.0 and 1.0, got {alpha}"
        raise ValueError(message)
    scaled_dense = [value * alpha for value in dense]
    scaled_sparse = {
        "indices": list(sparse.get("indices", [])),
        "values": [value * (1.0 - alpha) for value in sparse.get("values", [])],
    }
    return scaled_dense, scaled_sparse


async def embed_query(
    query: str, settings: Any, pinecone_client: Any
) -> tuple[list[float], dict[str, list[Any]]]:
    """Dense + sparse query embeddings, fetched concurrently in worker threads."""
    dense: list[list[float]] = []
    sparse: list[dict[str, list[Any]]] = []

    async def embed_dense() -> None:
        client = embedding_client(settings)
        vectors = await anyio.to_thread.run_sync(
            partial(dense_embeddings, client, settings.embedding_model, [query])
        )
        dense.append(list(vectors[0]))

    async def embed_sparse() -> None:
        vectors = await anyio.to_thread.run_sync(
            partial(
                sparse_embeddings,
                pinecone_client,
                settings.pinecone_sparse_model,
                [query],
                input_type="query",
            )
        )
        sparse.append(vectors[0])

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(embed_dense)
        task_group.start_soon(embed_sparse)

    return dense[0], sparse[0]


def page_numbers_from_metadata(metadata: dict[str, Any]) -> list[int]:
    """Undo the string coercion the ingest applies (Pinecone metadata has no int lists)."""
    pages: list[int] = []
    for page in metadata.get("page_numbers") or []:
        try:
            pages.append(int(float(page)))
        except (TypeError, ValueError):
            continue
    return pages


def _match_field(match: Any, name: str, default: Any = None) -> Any:
    """Read a field off a Pinecone match, whichever shape the SDK hands back."""
    value = getattr(match, name, None)
    if value is None and isinstance(match, dict):
        value = match.get(name)
    return default if value is None else value


def to_query_documents(matches: Any, collection_name: str) -> list[QueryDocument]:
    """Mirror ``retrieval.to_query_documents`` so downstream stages cannot tell the arms apart."""
    documents: list[QueryDocument] = []
    for match in matches or []:
        metadata = dict(_match_field(match, "metadata", {}) or {})
        page_numbers = page_numbers_from_metadata(metadata)
        raw_id = metadata.get("id_")
        chunk_id = int(float(raw_id)) if raw_id is not None else -1
        documents.append(
            QueryDocument(
                content=str(metadata.get("contextualized") or ""),
                score=float(_match_field(match, "score", 0.0)),
                doc_id=f"pinecone:{collection_name}:{chunk_id}",
                metadata={
                    "id_": chunk_id,
                    "text": str(metadata.get("text") or ""),
                    "doc_source": str(metadata.get("doc_source") or ""),
                    "page_numbers": page_numbers,
                },
                source_name=collection_name,
                page_numbers=page_numbers,
            )
        )
    return documents


def _matches(response: Any) -> list[Any]:
    matches = getattr(response, "matches", None)
    if matches is None and isinstance(response, dict):
        matches = response.get("matches")
    return list(matches or [])


async def pinecone_search(
    pinecone_index: Any,
    collection_name: str,
    query: str,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> QueryResultList:
    """Drop-in replacement for ``hybrid_search`` backed by a Pinecone namespace."""
    from healthcare_rag.graph.resources import get

    resources = get()
    settings = resources.settings
    pinecone_client = await resources.pinecone_client()

    dense, sparse = await embed_query(query, settings, pinecone_client)
    dense, sparse = convex_scale(dense, sparse, settings.pinecone_alpha)
    namespace = namespace_for(collection_name)

    response = await anyio.to_thread.run_sync(
        partial(
            pinecone_index.query,
            namespace=namespace,
            vector=dense,
            sparse_vector=sparse,
            top_k=limit,
            include_metadata=True,
        )
    )
    documents = to_query_documents(_matches(response), collection_name)
    logger.info(
        "PINECONE_SEARCHED namespace=%s top_k=%s hits=%s alpha=%s",
        namespace,
        limit,
        len(documents),
        settings.pinecone_alpha,
    )
    return QueryResultList(
        results=[QueryResult(source=collection_name, query=query, docs=documents)]
    )
