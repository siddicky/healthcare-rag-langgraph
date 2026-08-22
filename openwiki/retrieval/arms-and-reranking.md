---
type: data system
title: Retrieval arms and reranking
description: A/B retrieval backends selectable with HC_RAG_RETRIEVER (weaviate, pageindex, pinecone) plus the fail-soft cross-encoder reranker, resolved per turn by the retrieve_documents node.
tags: [retrieval, pageindex, pinecone, rerank, ab-testing]
openwiki:
  roles: [architecture, integration, testing]
  change_kinds: [configuration, public-api]
  source_paths: [healthcare_rag/processors/pageindex_retrieval.py, healthcare_rag/processors/pinecone_retrieval.py, healthcare_rag/processors/rerank.py, healthcare_rag/storage/pinecone_store.py, healthcare_rag/graph/resources.py]
  symbols: [pageindex_search, select_nodes, select_chunks, render_outline, pinecone_search, embed_query, convex_scale, rerank_documents, reorder, Resources.hybrid_search, Resources.pinecone_client]
  test_paths: [tests/test_pageindex_retrieval.py, tests/test_pinecone_retrieval.py, tests/test_rerank.py, tests/test_pageindex_gate.py]
  invariants: [Every arm mirrors hybrid_search's signature and returns QueryResultList over the same contextualized chunks, so routing, merge, citations, and chunk_recall are arm-independent., Reranking is fail-soft: an outage keeps the search's own order truncated to top_k., Pinecone calls are synchronous and must cross anyio.to_thread because Resources outlives event loops.]
  validation_commands: [".venv/bin/python -m pytest -q tests/test_pageindex_retrieval.py tests/test_pinecone_retrieval.py tests/test_rerank.py"]
---

# Retrieval arms and reranking

The default retrieval is Weaviate hybrid search ([Weaviate retrieval and corpus
ingestion](weaviate-and-ingestion.md)). Two A/B alternatives swap only the
per-collection search callable: the `retrieve_documents` node resolves the arm
from `GraphSettings.retriever` via `_ARMS`/`resolve_arm`
(`healthcare_rag/graph/nodes/retrieve.py#L38-L57`), so routing, merge,
gap-fill, citations, and every downstream stage are unchanged. An explicitly
injected `Resources.hybrid_search` (tests, fixtures) always wins over the knob.
Each arm also declares the SDK error class worth retrying — Weaviate arms
retry `WeaviateBaseError`, pinecone retries `PineconeException`, three
attempts with 1 s/2 s backoff. `HC_RAG_RETRIEVER` selects the arm
(`weaviate` default, `pageindex`, `pinecone`; unknown names raise), documented
in `healthcare_rag/services/models.py`.

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart LR
  R["retrieve_documents node"] --> S["resolve_arm(settings.retriever)"]
  S -->|weaviate default| W["hybrid_search: Weaviate hybrid alpha=0.65"]
  S -->|pageindex| P["pageindex_search: LLM picks tree nodes -> page ranges -> chunks"]
  S -->|pinecone| N["pinecone_search: dense + sparse, convex-scaled"]
  W & P & N --> RR{"HC_RAG_RERANKER=pinecone?"}
  RR -->|yes| X["rerank_documents: candidates -> top_k, fail-soft"]
  RR -->|no| Q["QueryResultList -> union_results"]
  X --> Q
```

When reranking is on, the search call is given `limit=rerank_candidates` (only
if the callable accepts a `limit` kwarg, `accepts_limit`) and
`rerank_documents` trims each result back to `rerank_top_k` inside the traced
retriever run, so generation still sees the same amount of context
(`graph/nodes/retrieve.py#L100-L138`).

## PageIndex arm (`HC_RAG_RETRIEVER=pageindex`)

`healthcare_rag/processors/pageindex_retrieval.py` is a tree-search adapter:

- One structured LLM call (`pageindex_select` prompt → `PageIndexSelection`)
  reads a compact outline of a monograph's section tree and picks up to
  `HC_RAG_PAGEINDEX_MAX_NODES` (default 4) nodes (`select_nodes`,
  `render_outline`; prompt/model registered in `graph/prompts.py`
  `STAGE_FILES`/`RESPONSE_MODELS`).
- Selected nodes' 1-based inclusive page ranges expand back onto the *same*
  contextualised chunks Weaviate uses (`data/chunks_<collection>.json`), capped
  at `HC_RAG_PAGEINDEX_MAX_CHUNKS` (default 8) in node order then chunk-id
  order, deduplicated (`select_chunks`).
- `to_query_documents` mirrors the Weaviate result shape with synthetic ids
  `pageindex:<collection>:<chunk id>` and rank-based scores, so `chunk_recall` /
  `page_recall` / citations keep working.
- Trees come from `make index-pageindex` (writes `data/pageindex_tree_*.json`)
  via the offline indexer `healthcare_rag/storage/pageindex_index.py`, which
  runs in an ephemeral uv env because `pageindex` needs `openai>=2` and the app
  pins `openai<2`. Missing tree/chunk files raise `FileNotFoundError` with the
  fix in the message. Trees and chunks are memoised (`clear_cache()` for tests
  or re-index). `HC_RAG_PAGEINDEX_DIR` relocates the data directory.
- Cost/quality gating experiments live in `evals/pageindex_gate.py` (offline
  harness exercised by `tests/test_pageindex_gate.py`).

## Pinecone arm (`HC_RAG_RETRIEVER=pinecone`)

`healthcare_rag/processors/pinecone_retrieval.py` searches a serverless
Pinecone index (built by `make ingest-pinecone` → `storage/pinecone_store.py`)
over the same chunks — one namespace per collection, lower-cased
(`namespace_for`). Per query, `embed_query` fetches dense OpenAI
`text-embedding-3-small` vectors and Pinecone sparse vectors concurrently in
worker threads. There is no server-side `alpha`, so `convex_scale` implements
Weaviate's semantics on a dot-product index: `dense *= alpha`,
`sparse *= 1 - alpha` (`HC_RAG_PINECONE_ALPHA`, default `0.65` matching the
Weaviate arm; `alpha` outside 0..1 raises). Index name, sparse model, and
embedding model are `HC_RAG_PINECONE_INDEX` / `HC_RAG_PINECONE_SPARSE_MODEL` /
`HC_RAG_EMBEDDING_MODEL`; `PINECONE_API_KEY` is required (secret, `.env`).

The Pinecone SDK is synchronous; every call crosses `anyio.to_thread` because
the process-wide `Resources` singleton outlives the event loop that would own
an aiohttp session. The sync OpenAI embedding client is cached process-wide for
the same reason (`embedding_client`/`reset_embedding_client`).

## Reranker (`HC_RAG_RERANKER=pinecone`)

`healthcare_rag/processors/rerank.py` is part of the *retrieval* stage, not a
new pipeline stage: when on, each collection search fetches
`HC_RAG_RERANK_CANDIDATES` (default 12) documents and `rerank_documents` keeps
`HC_RAG_RERANK_TOP_K` (default 4 — the un-reranked limit, so the context size
into generation is constant) via Pinecone Inference
(`HC_RAG_RERANK_MODEL`, default `bge-reranker-v2-m3`; needs `PINECONE_API_KEY`,
works on the Weaviate arm too via `Resources.pinecone_client`). It is
deliberately **fail-soft**: any exception logs `RERANK_FAILED` and keeps the
search's own ordering truncated to `top_k` — an outage degrades quality, never
availability. `reorder` skips out-of-range and duplicate indices so a malformed
response yields a shorter list, never a corrupt one. Timing surfaces as a
nested `rerank_documents` LangSmith child run and a `RERANK_APPLIED ... ms=`
log line.

## Change guidance

- Adding an arm: mirror `hybrid_search`'s signature, return `QueryResultList`
  of `QueryDocument`s with `page_numbers` and chunk `id_` metadata, register it
  in `_ARMS` with its retry error class in `graph/nodes/retrieve.py`, add the
  settings getter and docstring entry in `services/models.py`, and snapshot it
  in `GraphSettings` (`graph/settings.py`).
- Tuning an arm or the reranker: validate offline with the focused tests
  above, then compare equal-config eval experiments
  (`make eval-nojudge PREFIX=<arm>`) watching `chunk_recall`, `page_recall`,
  latency, and cost; see [evaluations](../observability/evaluations.md).
- Environment knobs are summarized in [models and runtime](../configuration/models-and-runtime.md).
