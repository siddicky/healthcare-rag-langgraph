---
type: data system
title: Retrieval arms and reranking
description: The three interchangeable retrieval backends (weaviate hybrid, pageindex tree-search, pinecone serverless hybrid), the Pinecone reranker, their ingestion/index commands, and how arms are selected and gated.
tags: [retrieval, pageindex, pinecone, reranking, ab-testing]
openwiki:
  roles: [architecture, integration, testing]
  change_kinds: [public-api, routing]
  source_paths: [healthcare_rag/graph/nodes/retrieve.py, healthcare_rag/processors/pageindex_retrieval.py, healthcare_rag/processors/pinecone_retrieval.py, healthcare_rag/processors/rerank.py, healthcare_rag/storage/pinecone_store.py, healthcare_rag/storage/pageindex_index.py]
  symbols: [resolve_arm, retrieve_documents, pageindex_search, pinecone_search, rerank_documents, convex_scale, select_nodes, select_chunks, to_query_documents]
  test_paths: [tests/test_pageindex_retrieval.py, tests/test_pinecone_retrieval.py, tests/test_rerank.py]
  invariants: [Each arm swaps only the per-collection search callable; routing, merge and every downstream stage are unchanged.,An arm's doc_ids are prefixed arm-specifically (weaviate UUID, pageindex:<collection>:<id>, pinecone:<collection>:<id>); citations and chunk-recall eval read id_ from metadata.,The reranker never changes how many documents generation sees; a rerank outage degrades to the search's own top_k ordering.]
  validation_commands: [uv run python -m evals.pageindex_gate --json --smoke]
---

# Retrieval arms and reranking

`HC_RAG_RETRIEVER` selects one of three arms (`weaviate` default, `pageindex`, `pinecone`) and `HC_RAG_RERANKER` optionally adds a reranking pass (`none` default, `pinecone`). All knobs live in [models and runtime](../configuration/models-and-runtime.md); all three arms run over the **same** chunks (`data/chunks_<collection>.json`) so `chunk_recall`/`page_recall`/citations keep working unchanged.

## Arm selection

`retrieve_documents` (`healthcare_rag/graph/nodes/retrieve.py`) resolves the arm via `_ARMS` → `resolve_arm(backend)`, mapping each backend to a search callable plus the SDK error class worth retrying (`WeaviateBaseError` for weaviate/pageindex, `PineconeException` for pinecone; PageIndex keeps the Weaviate class purely for byte-identical behaviour — it opens no client). An explicitly injected `resources.hybrid_search` (tests, fixtures) always wins. Only the Weaviate/Pinecone arms open a client (`resources.weaviate()` / `resources.pinecone()`); PageIndex reads cached JSON.

| Arm | Search callable | Source | Candidates per collection/query |
|---|---|---|---:|
| `weaviate` | `hybrid_search` | `processors/retrieval.py` | 4 (see [Weaviate retrieval](weaviate-and-ingestion.md)) |
| `pageindex` | `pageindex_search` | `processors/pageindex_retrieval.py` | up to `pageindex_max_chunks` (default 8) |
| `pinecone` | `pinecone_search` | `processors/pinecone_retrieval.py` | 4, or `rerank_candidates` when reranking |

## PageIndex arm (`HC_RAG_RETRIEVER=pageindex`)

One structured LLM call (`pageindex_select.yaml.j2` → `PageIndexSelection`, fail-soft to empty) reads a cached section outline of the monograph (`data/pageindex_tree_<collection>.json`, built by `make index-pageindex`) and keeps up to `pageindex_max_nodes` (default 4) nodes; `select_chunks` maps the selected nodes' 1-based inclusive page ranges back onto the same chunk files. Doc IDs are `pageindex:<collection>:<id>` with rank-decayed scores; the module-level `_cache` memoises trees/chunks (drop with `clear_cache()` after a re-index). Trees are produced by the offline indexer `storage/pageindex_index.py`, which runs in an ephemeral uv env because `pageindex` needs `openai>=2` while the venv pins `openai<2` — the runtime never imports the package. `HC_RAG_PAGEINDEX_DIR` relocates the JSON dir. Missing tree/chunk files raise `FileNotFoundError` with the fix command.

## Pinecone arm (`HC_RAG_RETRIEVER=pinecone`)

One serverless index (`HC_RAG_PINECONE_INDEX`, dotproduct, dim 1536), one lower-cased namespace per collection, built by `make ingest-pinecone` (`storage/pinecone_store.py`; needs `PINECONE_API_KEY` + `OPENAI_API_KEY`). Each query is embedded twice — dense with `text-embedding-3-small` (the model Weaviate's vectoriser uses) and sparse with Pinecone Inference (`pinecone-sparse-english-v0`) — fetched concurrently, then combined by **convex scaling** (`convex_scale`: `dense *= alpha`, `sparse *= 1 - alpha` with `HC_RAG_PINECONE_ALPHA`, default 0.65 matching Weaviate's alpha). Ingest embeds `contextualized` alone (Weaviate embeds a class+property concatenation; the divergence is deliberate and documented in the module docstring) and coerces `page_numbers` to string lists; `page_numbers_from_metadata` converts back. The sync Pinecone SDK always crosses `anyio.to_thread` so the cached client has no event-loop affinity. Doc IDs are `pinecone:<collection>:<id_>`.

## Reranker (`HC_RAG_RERANKER=pinecone`)

`rerank_documents` (`processors/rerank.py`) is part of the retrieval stage, not a new pipeline stage: with it on, each collection search fetches `rerank_candidates` (default 12) and the Pinecone Inference reranker (`HC_RAG_RERANK_MODEL`, default `bge-reranker-v2-m3`) keeps `rerank_top_k` (default 4 — the un-reranked limit, so top-k into generation is constant). `reorder` skips out-of-range/duplicate indices, so a malformed response degrades to a shorter list. It is deliberately **fail-soft**: on any error the search's own ordering truncated to `top_k` is used. Timing surfaces as a nested `rerank_documents` LangSmith run plus a `RERANK_APPLIED ... ms=` log line. The reranker runs on the Weaviate arm too — `Resources.pinecone_client()` exists for exactly that reason.

## A/B gating

`evals/pageindex_gate.py` is the two-stage go/no-go gate over arm strings (`weaviate`, `pinecone`, `pageindex`, optionally `+rerank`): stage 1 compares retrieval-only mean `page_recall` on golden items (no generation, no judges); stage 2 runs a paired `run_baseline` for the reference and the best passing candidate. Exit codes: 0 pass, 2 stage-1 reject (terminal, no judge money), 3 stage-2 fail, 1 error. Cheap plumbing check: `uv run python -m evals.pageindex_gate --json --smoke`. Design notes and results: `docs/retrieval-experiments.md`. For the query/safety *routing* gates (decision arms, not retrieval arms) see [routing gates](../observability/routing-evals.md).

**Focused validation:** `make test` (`tests/test_pageindex_retrieval.py`, `tests/test_pinecone_retrieval.py`, `tests/test_rerank.py`, `tests/test_pageindex_gate.py`), then the gate above; after an arm/corpus change re-run a filtered factual eval and watch `chunk_recall`/`page_recall`/`right_collection_routed`.
