---
type: data system
title: Weaviate retrieval and corpus ingestion
description: Collection routing, hybrid search configuration, chunk schema, and safe destructive rebuild procedures.
tags: [retrieval, weaviate, ingestion]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-30T08:22:08.381Z
sources:
  - id: openwiki-source-85f9eed3af15b7e1b616fec3
    resource: repo://data/AGENTS.md
  - id: openwiki-source-b79fbbd921df689b4bbdc82f
    resource: repo://docker-compose.yml
  - id: openwiki-source-22eb5cb0d97d1128e139f52c
    resource: repo://healthcare_rag/graph/nodes/evaluate.py
  - id: openwiki-source-13a4df04285e450e70482893
    resource: repo://healthcare_rag/graph/nodes/generate.py
  - id: openwiki-source-56b79b6d8262f2037cd8bd60
    resource: repo://healthcare_rag/graph/nodes/retrieve.py
  - id: openwiki-source-5806962bd2364e46e9a55647
    resource: repo://healthcare_rag/graph/routers.py
  - id: openwiki-source-f4bb79f283dfd9566073cbf5
    resource: repo://healthcare_rag/processors/pdf_chunker.py
  - id: openwiki-source-6716f82708e52a00841d5c61
    resource: repo://healthcare_rag/processors/retrieval.py
  - id: openwiki-source-22237757ac3ace282e0833c0
    resource: repo://healthcare_rag/processors/validation_rendering.py
  - id: openwiki-source-5bfd2a59ff90e1d4a18105f7
    resource: repo://healthcare_rag/processors/validation.py
  - id: openwiki-source-54388b396a525f7713df8466
    resource: repo://healthcare_rag/storage/vector_store.py
  - id: openwiki-source-012f2c78e3b1446dfc35803f
    resource: repo://Makefile
  - id: openwiki-source-5932dc0bd5291eafa8ea72bf
    resource: repo://tests/graph/test_union_results.py
generated: { by: "openwiki/0.4.3", at: "2026-08-30T08:22:08.381Z" }
---

# Weaviate retrieval and corpus ingestion

## Request path

Routing builds one OpenAI function tool per configured collection — `query_<collection.lower()>` — via `build_routing_tools` (`healthcare_rag/processors/retrieval.py#L13-L35`). The graph node `retrieve_documents` asks the shared `LangChainLLMGateway.aroute_tools` which tools to call; each call's tool name is mapped back to a collection (`query_lipitor` → `Lipitor`) and its `query` argument is the search text (`healthcare_rag/graph/nodes/retrieve.py`). Thus configured collection names must stay compatible with that mapping and with the ingested collection names. Renaming a collection requires updating runtime defaults/configuration, ingestion target, and eval expectations together. Which search callable actually runs is decided by `HC_RAG_RETRIEVER` — see [retrieval arms](arms-and-reranking.md); the schema below is the default Weaviate arm.

Each tool accepts one required string argument, `query`, intended to be the user query verbatim. A routing failure is caught and degraded to an empty result list rather than aborting the request; each collection search is retried up to three times (1 s / 2 s backoff) on the arm's transient SDK error class (`WeaviateBaseError` for weaviate/pageindex, `PineconeException` for pinecone — `_ARMS` in `graph/nodes/retrieve.py`). Two A/B alternatives replace only the per-collection search callable — see [retrieval arms and reranking](arms-and-reranking.md).

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

Checked-in `data/chunks_lipitor.json` (119 chunk records) and `data/chunks_metformin.json` (54 chunk records) are the default ingest artifacts and the primary corpus behind the hybrid-search arm; they are checked-in build outputs of an offline pipeline, never written by the running app (`data/AGENTS.md`). Each file is a single top-level JSON array; each element is one chunk record with the same shape ingestion expects: `id_`, raw `text`, LLM-augmented `contextualized` text (the field actually embedded/searched), `metadata` (docling chunk metadata, discarded on import), `page_numbers`, and `doc_source`. Do not paste or hand-edit their contents casually. They are produced by `DocumentChunkProcessor`: Docling PDF conversion without OCR, table structure enabled, `HybridChunker` with MiniLM tokenizer, `max_tokens=8192`, peer merging; table fragments sharing a table ref are merged, and adjacent same-heading sentence fragments may merge (`healthcare_rag/processors/pdf_chunker.py#L28-L154`). Saved rows contain `id_`, `text`, `contextualized`, metadata, page numbers, and document source (`#L166-L189`). Regeneration needs the `ingest` extra and allowed source PDFs.

## Operational invariants and recovery

Docker persists `/var/lib/weaviate` in `weaviate_data` (`docker-compose.yml#L15-L16`), so collections survive container recreation. `make ingest` calls loader `--delete-all`, which deletes **all collections**, not just Lipitor/Metformin (`Makefile#L31-L35`; `vector_store.py#L379-L409`). `ingest_json_to_collection(delete_existing=True)` is narrower and deletes only its named collection (`#L273-L327`). Confirm scope before either operation.

Import validates only nonempty `text`, batches at 100, stops after **more than 10** batch errors, and reports an *approximate* successful count. It can therefore leave a partially loaded collection without failing loudly (`#L187-L234`). Recovery: inspect loader errors, fix the artifact/schema/network issue, delete the affected collection (or intentionally use whole-store `--delete-all`), then reingest; do not assume a rerun repairs duplicates or partial state. After rebuild, issue a narrow known-drug query/eval and verify collection routing plus expected chunk/page recall.

Retrieval evaluation is the graph node `evaluate_retrieval` (`graph/nodes/evaluate.py`): it sends the merged context to `retrieval_evaluation.yaml.j2` and issues a gap-fill round only when the parsed result says `is_sufficient=False`, `additional_queries` is nonempty, **and** no gap round has run yet (`gap_round == 0`); extra queries are capped at three. Gap-fill queries fan out via `Send` back into `retrieve_documents`, and `route_after_merge` skips re-evaluation for the merged gap-fill results so the flow goes straight to generation (`graph/routers.py`). All retrieval envelopes are merged and deduplicated (first occurrence wins, then grouped by source) by Weaviate UUID in `union_results` (`processors/retrieval.py#L87-L109`), after being ordered by phase then kind (`initial`/`clarified` < `decomposed` < `gap_fill`) in `merge_retrievals` (`graph/nodes/retrieve.py#L76-L81`, `#L179-L221`). `route_after_merge` sends the merge straight to `generate_answer` once a gap-fill round has already been folded in (`gap_filled=True`), otherwise back to `evaluate_retrieval` (`graph/routers.py#L137-L142`). Changes to retrieval breadth affect answer prompt size, citations, cost, and the `gap_filled` telemetry; validate in [evaluations](../observability/evaluations.md).

If a whole retrieval turn comes back with no documents at all (routing failure, empty hybrid-search hits, or all retries exhausted), `evaluate_retrieval` short-circuits to `is_sufficient=True` without calling the judge LLM (`graph/nodes/evaluate.py#L27-L39`), and `generate_answer` never calls the model or `AnswerValidator`: it returns the fixed string `"I'm sorry, I don't know the answer to that question."` directly (`graph/nodes/generate.py#L18-L31`). If some documents come back but the judge LLM flags them as insufficient (`is_sufficient=False`) and offers `additional_queries` and no gap round has run yet, `route_after_evaluate` fans out one capped gap-fill round (`graph/routers.py#L145-L178`); after that round is merged, `route_after_merge` forces generation regardless of remaining gaps — evidence gaps are given exactly one remediation attempt, never more. If retrieved documents exist but are weak or off-target in a way the LLM judge does not flag, the flow proceeds to generation and citation validation, where an ungrounded quote is caught downstream by fuzzy/exact citation matching and drops that statement, or replaces the whole answer with `FALLBACK_MESSAGE` — see [answer structuring and citation verification](../processors/validation.md).

## Safe procedure: add or replace a monograph

1. Obtain the source PDF from an allowed location; never inspect/copy proprietary PDF contents into documentation or commits (`docs/`-tracked source only, per [runbook](../operations/runbook.md)).
2. Install the `ingest` extra, then regenerate the chunk artifact with `DocumentChunkProcessor`/`healthcare_rag/processors/pdf_chunker.py` (e.g. `uv run python healthcare_rag/processors/pdf_chunker.py --source <allowed-pdf-path>`), producing `data/chunks_<name>.json`. A new drug means a **new** file; replacing an existing monograph means regenerating its existing file — either way, chunk `id_` values and page numbers can shift, so downstream eval expectations must be revisited in the same change.
3. Start Weaviate: `make weaviate` (docker compose up, waits for readiness).
4. Ingest. **Know which command you are running, because both are destructive at different scopes:**
   - `make ingest` invokes the loader with **`--delete-all`**, which calls `client.collections.delete_all()` and erases **every collection in the persistent Weaviate volume**, not just the ones being reloaded (`Makefile#L31-L35`; `healthcare_rag/storage/vector_store.py#L379-L386`). Only use it when you intend to rebuild the entire store (e.g. both Lipitor and Metformin from their checked-in files, or after adding a new drug that must also be re-wired into the Makefile's `--collection` list).
   - To add or replace a **single** collection without touching others, call the loader directly with `ingest_json_to_collection(..., delete_existing=True)` (or the equivalent `--collection <Name> data/chunks_<name>.json` invocation without `--delete-all`), which deletes only that named collection before reloading it (`vector_store.py#L273-L327`).
5. Watch the import log: it validates only nonempty `text`, batches at 100, stops after **more than 10** batch errors, and reports an *approximate* successful count — a clean-looking run is not proof of a complete load (`vector_store.py#L187-L234`). If it fails or partially loads, fix the artifact/schema/network issue, delete the affected collection (or intentionally rerun the whole-store `--delete-all` path), and reingest; do not assume a rerun repairs duplicates or partial state.
6. Wire the new/updated collection name through anywhere it is configured (runtime collection list, routing tool names, eval golden dataset) — collection names must stay consistent between ingestion, `query_<collection.lower()>` routing, and eval expectations.
7. Validate: run a narrow known-drug query or eval and confirm collection routing plus expected chunk/page recall before considering the change done.

**Focused validation:** `make weaviate`, `make ingest`, then `uv run python -m evals.run_baseline --category factual_single --no-judges`. Use a category matching a changed drug/corpus and inspect `chunk_recall`, `page_recall`, and `right_collection_routed`.
