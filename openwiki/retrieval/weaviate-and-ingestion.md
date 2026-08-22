---
type: data system
title: Weaviate retrieval and corpus ingestion
description: Collection routing, hybrid search configuration, chunk schema, and safe destructive rebuild procedures.
tags: [retrieval, weaviate, ingestion]
---

# Weaviate retrieval and corpus ingestion

## Request path

Routing builds one OpenAI function tool per configured collection — `query_<collection.lower()>` — via `build_routing_tools` (`healthcare_rag/processors/retrieval.py#L13-L35`). The graph node `retrieve_documents` asks the shared `LangChainLLMGateway.aroute_tools` which tools to call; each call's tool name is mapped back to a collection (`query_lipitor` → `Lipitor`) and its `query` argument is the search text (`healthcare_rag/graph/nodes/retrieve.py`). Thus configured collection names must stay compatible with that mapping and with the ingested collection names. Renaming a collection requires updating runtime defaults/configuration, ingestion target, and eval expectations together.

Each tool accepts one required string argument, `query`, intended to be the user query verbatim. A routing failure is caught and degraded to an empty result list rather than aborting the request; each collection search is retried up to three times (1 s / 2 s backoff) on the arm's SDK error class (`WeaviateBaseError` here, `PineconeException` on the pinecone arm) (`graph/nodes/retrieve.py#L85-L149`). Two A/B alternatives replace only the per-collection search callable — see [retrieval arms and reranking](arms-and-reranking.md).

For each tool call, `hybrid_search` uses `collection.query.hybrid` with:

| Parameter | Value | Consequence |
|---|---:|---|
| `query_properties` | `["contextualized"]` | hybrid query targets contextualized chunk text only |
| `limit` | `4` | max four objects per routed collection/query |
| `alpha` | `0.65` | semantic vector signal weighted over keyword signal |
| `fusion_type` | `HybridFusion.RELATIVE_SCORE` | relative-score hybrid fusion |
| metadata | `score=True` | exposes Weaviate score in `QueryDocument.score` |

`to_query_documents` uses the returned object UUID as `QueryDocument.doc_id`, with all properties other than `contextualized` as metadata (`processors/retrieval.py#L38-L58`). Those UUIDs—not chunk `id_`—are citations' durable runtime IDs. `id_` remains metadata used by eval chunk-recall.

## Stored schema and ingestion

`storage/vector_store.py` creates collections with `text2vec_openai` and exactly these properties: `id_` INT, `text` TEXT, `contextualized` TEXT, `doc_source` TEXT, and `page_numbers` INT_ARRAY (`healthcare_rag/storage/vector_store.py#L41-L50`, `#L101-L136`). **Use `id_`, not `id`:** Weaviate reserves `id`; `prepare_data_for_import` bridges legacy input `id` to `id_` (`#L144-L163`). Extra JSON fields, including chunk metadata, are discarded on import.

Checked-in `data/chunks_lipitor.json` and `data/chunks_metformin.json` are the default ingest artifacts. Do not paste or hand-edit their contents casually. They are produced by `DocumentChunkProcessor`: Docling PDF conversion without OCR, table structure enabled, `HybridChunker` with MiniLM tokenizer, `max_tokens=8192`, peer merging; table fragments sharing a table ref are merged, and adjacent same-heading sentence fragments may merge (`healthcare_rag/processors/pdf_chunker.py#L28-L154`). Saved rows contain `id_`, `text`, `contextualized`, metadata, page numbers, and document source (`#L166-L189`). Regeneration needs the `ingest` extra and allowed source PDFs.

## Operational invariants and recovery

Docker persists `/var/lib/weaviate` in `weaviate_data` (`docker-compose.yml#L15-L16`), so collections survive container recreation. `make ingest` calls loader `--delete-all`, which deletes **all collections**, not just Lipitor/Metformin (`Makefile#L18-L21`; `vector_store.py#L379-L409`). `ingest_json_to_collection(delete_existing=True)` is narrower and deletes only its named collection (`#L273-L327`). Confirm scope before either operation.

Import validates only nonempty `text`, batches at 100, stops after **more than 10** batch errors, and reports an *approximate* successful count. It can therefore leave a partially loaded collection without failing loudly (`#L187-L234`). Recovery: inspect loader errors, fix the artifact/schema/network issue, delete the affected collection (or intentionally use whole-store `--delete-all`), then reingest; do not assume a rerun repairs duplicates or partial state. After rebuild, issue a narrow known-drug query/eval and verify collection routing plus expected chunk/page recall.

Retrieval evaluation is the graph node `evaluate_retrieval` (`graph/nodes/evaluate.py`): it sends the merged context to `retrieval_evaluation.yaml.j2` and issues a gap-fill round only when the parsed result says `is_sufficient=False`, `additional_queries` is nonempty, **and** no gap round has run yet (`gap_round == 0`); extra queries are capped at three. Gap-fill queries fan out via `Send` back into `retrieve_documents`, and `route_after_merge` skips re-evaluation for the merged gap-fill results so the flow goes straight to generation (`graph/routers.py`). All retrieval envelopes are merged and deduplicated by Weaviate UUID in `union_results`, ordered by phase then kind (`initial`/`clarified` < `decomposed` < `gap_fill`) in `merge_retrievals` (`graph/nodes/retrieve.py`; `processors/retrieval.py#L79-L99`). Changes to retrieval breadth affect answer prompt size, citations, cost, and the `gap_filled` telemetry; validate in [evaluations](../observability/evaluations.md).

**Focused validation:** `make weaviate`, `make ingest`, then `uv run python -m evals.run_baseline --category factual_single --no-judges`. Use a category matching a changed drug/corpus and inspect `chunk_recall`, `page_recall`, and `right_collection_routed`.
