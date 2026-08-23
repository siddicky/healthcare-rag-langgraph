<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# storage

## Purpose
Vector-store construction and data-loading scripts for all three retrieval
backends: the default Weaviate hybrid-search collection, the offline
PageIndex tree-index builder, and the Pinecone serverless hybrid index. Only
`vector_store.py` is on the default runtime path (`HC_RAG_RETRIEVER=weaviate`);
the other two build artifacts consumed by the A/B arms in `processors/`.

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | Package docstring only, no exports. |
| `vector_store.py` | The default (Weaviate) storage layer: `connect_to_weaviate`, `create_collection` (schema + `text2vec-openai` vectorizer config), `prepare_data_for_import`, `import_data`, `load_data_from_json`, `ingest_json_to_collection` (used by `cli/ingestion.py::load_to_weaviate` and `make ingest`), `main()` (CLI). Chunk ids are stored as `id_` (`id` is reserved in Weaviate). |
| `pinecone_store.py` | Builds/loads the `HC_RAG_RETRIEVER=pinecone` serverless index (mirrors `vector_store.py`'s CLI shape and chunk files, one namespace per collection): `require_pinecone_api_key`/`require_openai_api_key` (fail-fast, never hang), `connect_to_pinecone`, `namespace_for`, `ensure_index`, `dense_embeddings` (OpenAI `text-embedding-3-small` over `contextualized` text only — a deliberate divergence from Weaviate's vectorizer, which embeds class name + all properties), `sparse_embeddings` (Pinecone Inference), `chunk_metadata` (stores `page_numbers` as strings — Pinecone metadata has no int lists), `ingest_chunks`, `main()` (`make ingest-pinecone`). |
| `pageindex_index.py` | Builds the cached PageIndex tree JSON consumed by `processors/pageindex_retrieval.py` (`HC_RAG_RETRIEVER=pageindex`): `build_tree`, `_normalize`, `_stats`, `main()`. **Never imported by the runtime** — it depends on `pageindex`/`litellm`/`openai>=2`, incompatible with the app venv's `openai<2` pin, so it must run in an ephemeral env (`uv run --no-project --with pageindex ...` / `make index-pageindex`). The runtime only reads the JSON artifact this script writes. |

## For AI Agents

### Working In This Directory
- **`pageindex_index.py` must never be imported from anywhere in the app
  venv's import graph.** It exists purely as a standalone script run in its
  own `uv run --no-project` environment; adding an `import` of it (even
  conditional) from `processors/` or `graph/` would break the app's
  `openai<2` pin.
- Chunk ids are `id_` in Weaviate, not `id` — remember this when writing
  queries or schema changes in `vector_store.py`.
- `pinecone_store.py`'s dense embeddings intentionally embed only
  `contextualized` text (not the full Weaviate-style property concatenation)
  — the two arms' dense vectors are close but not identical by design; don't
  "fix" this without re-reading `docs/retrieval-experiments.md` first.
- Every Pinecone credential getter here must raise
  `ValueError("PINECONE_API_KEY is not set")` rather than hang — preserve
  that fail-fast behavior in any new Pinecone code path.
- Before proposing any retrieval-storage change, read
  `docs/retrieval-experiments.md`: PageIndex, Pinecone hybrid, and a bge
  reranker were all measured and rejected against Weaviate hybrid on
  2026-08-20.

### Testing Requirements
- `tests/test_vector_store.py` — `vector_store.py`.
- `tests/test_pinecone_retrieval.py` — exercises `pinecone_store.py`'s
  ingestion shape indirectly via the retrieval arm in `processors/`.
- `pageindex_index.py` has no test file (it's a standalone offline script);
  `tests/test_pageindex_retrieval.py` covers the runtime consumer of its
  output instead.

### Common Patterns
- All three modules follow the same CLI shape: argparse entrypoint
  (`main()`), `--collection <Name> <chunks.json>` repeated args, and a
  `load_data_from_json` helper reading the same `data/chunks_*.json` files.

## Dependencies

### Internal
- Consumed by `healthcare_rag/cli/ingestion.py` (`vector_store.py` only) and read by `healthcare_rag/processors/pageindex_retrieval.py` / `processors/pinecone_retrieval.py` (JSON/index artifacts, not Python imports).

### External
- `weaviate` (`vector_store.py`), `pinecone` (`pinecone_store.py`), `pageindex`/`litellm` (`pageindex_index.py`, ephemeral env only), `openai` (embeddings, all three).

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
