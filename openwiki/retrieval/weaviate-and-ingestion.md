---
type: data system
title: Weaviate retrieval and corpus ingestion
description: Collection routing, hybrid search configuration, chunk schema, and safe destructive rebuild procedures.
tags: [retrieval, weaviate, ingestion]
---

# Weaviate retrieval and corpus ingestion

## Request path

`QueryRouter` dynamically creates one OpenAI function tool per configured collection: `query_<collection.lower()>`. The model chooses zero or more tools; each valid call checks the collection exists, runs search, and becomes a source-grouped `QueryResult` (`healthcare_rag/processors/retrieval.py#L19-L107`). Thus configured collection names must remain compatible with `_extract_collection_name` (`query_lipitor` → `Lipitor`) and with the ingested collection names. Renaming a collection requires updating runtime defaults/configuration, ingestion target, and eval expectations together.

Each tool accepts one required string argument, `query`, intended to be the user query verbatim. No tool calls means `QueryResultList(results=[])`; a missing named collection or a malformed/failed individual tool call is logged and omitted while other calls continue. A top-level routing exception is caught and also returns an empty result list rather than aborting the request (`healthcare_rag/processors/retrieval.py#L67-L107`, `#L131-L167`).

For each tool call, `collection.query.hybrid` uses:

| Parameter | Value | Consequence |
|---|---:|---|
| `query_properties` | `["contextualized"]` | hybrid query targets contextualized chunk text only |
| `limit` | `4` | max four objects per routed collection/query |
| `alpha` | `0.65` | semantic vector signal weighted over keyword signal |
| `fusion_type` | `HybridFusion.RELATIVE_SCORE` | relative-score hybrid fusion |
| metadata | `score=True` | exposes Weaviate score in `QueryDocument.score` |

The router uses the returned object UUID as `QueryDocument.doc_id`, with all properties other than `contextualized` as metadata (`#L150-L201`). Those UUIDs—not chunk `id_`—are citations' durable runtime IDs. `id_` remains metadata used by eval chunk-recall.

## Stored schema and ingestion

`storage/vector_store.py` creates collections with `text2vec_openai` and exactly these properties: `id_` INT, `text` TEXT, `contextualized` TEXT, `doc_source` TEXT, and `page_numbers` INT_ARRAY (`healthcare_rag/storage/vector_store.py#L41-L50`, `#L101-L136`). **Use `id_`, not `id`:** Weaviate reserves `id`; `prepare_data_for_import` bridges legacy input `id` to `id_` (`#L144-L163`). Extra JSON fields, including chunk metadata, are discarded on import.

Checked-in `data/chunks_lipitor.json` and `data/chunks_metformin.json` are the default ingest artifacts. Do not paste or hand-edit their contents casually. They are produced by `DocumentChunkProcessor`: Docling PDF conversion without OCR, table structure enabled, `HybridChunker` with MiniLM tokenizer, `max_tokens=8192`, peer merging; table fragments sharing a table ref are merged, and adjacent same-heading sentence fragments may merge (`healthcare_rag/processors/pdf_chunker.py#L28-L154`). Saved rows contain `id_`, `text`, `contextualized`, metadata, page numbers, and document source (`#L166-L189`). Regeneration needs the `ingest` extra and allowed source PDFs.

## Operational invariants and recovery

Docker persists `/var/lib/weaviate` in `weaviate_data` (`docker-compose.yml#L15-L16`), so collections survive container recreation. `make ingest` calls loader `--delete-all`, which deletes **all collections**, not just Lipitor/Metformin (`Makefile#L18-L21`; `vector_store.py#L379-L409`). `ingest_json_to_collection(delete_existing=True)` is narrower and deletes only its named collection (`#L273-L327`). Confirm scope before either operation.

Import validates only nonempty `text`, batches at 100, stops after **more than 10** batch errors, and reports an *approximate* successful count. It can therefore leave a partially loaded collection without failing loudly (`#L187-L234`). Recovery: inspect loader errors, fix the artifact/schema/network issue, delete the affected collection (or intentionally use whole-store `--delete-all`), then reingest; do not assume a rerun repairs duplicates or partial state. After rebuild, issue a narrow known-drug query/eval and verify collection routing plus expected chunk/page recall.

Retrieval evaluation sends initial context to `retrieval_evaluation.yaml.j2`. It issues additional queries only when the parsed result explicitly says `is_sufficient=False` **and** supplies nonempty `additional_queries`; otherwise its default/failure behavior is to return the original results unaugmented. Additional routes run concurrently. If their routing/search work raises, `_fetch_additional_results` catches it and returns an empty addition, again preserving initial results. Returned follow-up groups append in place; it neither deduplicates docs nor enforces a global cap after gap filling (`healthcare_rag/processors/retrieval.py#L222-L313`). Changes to retrieval breadth affect answer prompt size, citations, cost, and branch winner `gap_filled`; validate in [evaluations](../observability/evaluations.md).

**Focused validation:** `make weaviate`, `make ingest`, then `uv run python -m evals.run_baseline --category factual_single --no-judges`. Use a category matching a changed drug/corpus and inspect `chunk_recall`, `page_recall`, and `right_collection_routed`.
