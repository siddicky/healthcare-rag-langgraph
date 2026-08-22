"""PageIndex tree-search retrieval arm (``HC_RAG_RETRIEVER=pageindex``).

The A/B alternative to Weaviate hybrid search. One LLM call reads the cached
section outline of a monograph (``data/pageindex_tree_<collection>.json``, built
by ``make index-pageindex``) and picks up to ``pageindex_max_nodes`` nodes; the
selected nodes' 1-based, inclusive page ranges are mapped back onto the *same*
contextualised chunks the Weaviate arm returns (``data/chunks_<collection>.json``),
so ``chunk_recall`` / ``page_recall`` / citations keep working unchanged.

``pageindex_search`` deliberately mirrors ``hybrid_search``'s signature; the
first argument (the Weaviate client) is accepted and ignored so the two arms are
interchangeable at ``Resources.hybrid_search``. This module never imports the
``pageindex`` package — that lives only in the offline indexer
(``healthcare_rag/storage/pageindex_index.py``), which runs in its own env.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any

from healthcare_rag.models.retrieval import (
    PageIndexSelection,
    QueryDocument,
    QueryResult,
    QueryResultList,
)
from healthcare_rag.services.tracing import traceable

logger = logging.getLogger("MedicalRAG")

DEFAULT_DATA_DIR = "data"
_SUMMARY_CHARS = 240

_cache: dict[str, Any] = {}
_CACHE_LOCK = Lock()


def data_dir() -> Path:
    """Directory holding ``pageindex_tree_*.json`` and ``chunks_*.json``."""
    return Path(os.getenv("HC_RAG_PAGEINDEX_DIR", DEFAULT_DATA_DIR))


def _load_json(path: Path) -> Any:
    key = str(path.resolve())
    with _CACHE_LOCK:
        if key in _cache:
            return _cache[key]
    payload = json.loads(path.read_text())
    with _CACHE_LOCK:
        _cache[key] = payload
    return payload


def clear_cache() -> None:
    """Drop the memoised trees/chunks (tests, or after a re-index)."""
    with _CACHE_LOCK:
        _cache.clear()


def load_tree(collection_name: str) -> dict[str, Any]:
    path = data_dir() / f"pageindex_tree_{collection_name.lower()}.json"
    if not path.exists():
        message = (
            f"PageIndex tree {path} is missing — build it with `make index-pageindex` "
            "before running with HC_RAG_RETRIEVER=pageindex."
        )
        raise FileNotFoundError(message)
    return _load_json(path)


def load_chunks(collection_name: str) -> list[dict[str, Any]]:
    path = data_dir() / f"chunks_{collection_name.lower()}.json"
    if not path.exists():
        message = f"Chunk file {path} is missing; the PageIndex arm needs the same chunks Weaviate ingests."
        raise FileNotFoundError(message)
    return _load_json(path)


def _walk(nodes: list[dict[str, Any]], depth: int = 0):
    for node in nodes:
        yield node, depth
        yield from _walk(node.get("nodes") or [], depth + 1)


def render_outline(tree: dict[str, Any]) -> str:
    """One compact line per node: node_id, indented title, page range, summary."""
    lines: list[str] = []
    for node, depth in _walk(tree.get("tree") or []):
        summary = (node.get("summary") or "").strip()
        if len(summary) > _SUMMARY_CHARS:
            summary = summary[: _SUMMARY_CHARS - 1].rstrip() + "…"
        start, end = node["start_index"], node["end_index"]
        pages = f"p{start}" if start == end else f"p{start}-{end}"
        title = node.get("title") or "(untitled)"
        lines.append(
            f"[{node['node_id']}] {'  ' * depth}{title} ({pages})"
            + (f" — {summary}" if summary else "")
        )
    return "\n".join(lines)


def _pages_for_node(node: dict[str, Any]) -> set[int]:
    """All pages a node covers, including every descendant's range."""
    pages: set[int] = set()
    for child, _ in _walk([node]):
        pages.update(range(int(child["start_index"]), int(child["end_index"]) + 1))
    return pages


def select_chunks(
    tree: dict[str, Any],
    chunks: list[dict[str, Any]],
    node_ids: list[str],
    max_chunks: int,
) -> list[dict[str, Any]]:
    """Map selected node ids → pages → chunks, in node order then chunk-id order.

    Unknown node ids are ignored, pages outside the document are ignored (the
    chunk index simply has nothing on them), and a chunk reached through two
    nodes appears once, at its first position.
    """
    by_id = {str(node["node_id"]): node for node, _ in _walk(tree.get("tree") or [])}
    page_count = int(tree.get("page_count") or 0)
    selected: list[dict[str, Any]] = []
    seen: set[Any] = set()

    for node_id in node_ids:
        node = by_id.get(str(node_id))
        if node is None:
            continue
        pages = _pages_for_node(node)
        if page_count:
            pages = {page for page in pages if 1 <= page <= page_count}
        if not pages:
            continue
        matches = [
            chunk
            for chunk in chunks
            if pages.intersection(chunk.get("page_numbers") or [])
        ]
        for chunk in sorted(matches, key=lambda c: int(c["id"])):
            if chunk["id"] in seen:
                continue
            seen.add(chunk["id"])
            selected.append(chunk)
            if len(selected) >= max_chunks:
                return selected
    return selected


def to_query_documents(
    chunks: list[dict[str, Any]], collection_name: str
) -> list[QueryDocument]:
    """Mirror the Weaviate result shape so downstream stages cannot tell the arms apart."""
    documents: list[QueryDocument] = []
    for rank, chunk in enumerate(chunks):
        page_numbers = list(chunk.get("page_numbers") or [])
        documents.append(
            QueryDocument(
                content=chunk.get("contextualized", ""),
                score=round(max(1.0 - 0.1 * rank, 0.1), 4),
                doc_id=f"pageindex:{collection_name}:{chunk['id']}",
                metadata={
                    "id_": int(chunk["id"]),
                    "text": chunk.get("text", ""),
                    "doc_source": chunk.get("doc_source", ""),
                    "page_numbers": page_numbers,
                },
                source_name=collection_name,
                page_numbers=page_numbers,
            )
        )
    return documents


@traceable(name="pageindex_select", run_type="chain")
async def select_nodes(collection_name: str, query: str, tree: dict[str, Any]) -> list[str]:
    """One structured LLM call over the outline; fail-soft to an empty selection."""
    from healthcare_rag.graph.resources import get

    resources = get()
    max_nodes = resources.settings.pageindex_max_nodes
    selection = await resources.gateway.astructured(
        "pageindex_select",
        PageIndexSelection,
        default=PageIndexSelection(),
        question=query,
        document=collection_name,
        outline=render_outline(tree),
        max_nodes=str(max_nodes),
    )
    node_ids = list((selection or PageIndexSelection()).node_ids)[:max_nodes]
    return [str(node_id).strip().strip("[]") for node_id in node_ids if str(node_id).strip()]


async def pageindex_search(
    weaviate_client: Any, collection_name: str, query: str
) -> QueryResultList:
    """Drop-in replacement for ``hybrid_search``; ``weaviate_client`` is ignored."""
    from healthcare_rag.graph.resources import get

    tree = load_tree(collection_name)
    chunks = load_chunks(collection_name)
    node_ids = await select_nodes(collection_name, query, tree)
    picked = select_chunks(
        tree, chunks, node_ids, get().settings.pageindex_max_chunks
    )
    logger.info(
        "PAGEINDEX_SELECTED collection=%s nodes=%s chunks=%s",
        collection_name,
        len(node_ids),
        len(picked),
    )
    documents = to_query_documents(picked, collection_name)
    return QueryResultList(
        results=[QueryResult(source=collection_name, query=query, docs=documents)]
    )
