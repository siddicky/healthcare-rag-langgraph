<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# data/

## Purpose
Pre-processed source data for the two drug monographs this RAG assistant
answers questions about (Lipitor, Metformin): chunked/contextualized text for
the primary hybrid-search retrieval path, plus prebuilt PageIndex trees for
the alternate (off-by-default) retrieval arm. `make ingest` loads the chunk
files into Weaviate; nothing here is generated at runtime by the app itself —
these are checked-in build artifacts of an offline ingestion pipeline.

## Key Files
| File | Description |
|------|-------------|
| `chunks_lipitor.json` | 119 chunk records for the Lipitor monograph. Each record: `id`, `text` (raw chunk), `contextualized` (LLM-augmented chunk text used for embedding/retrieval), `metadata`, `page_numbers`, `doc_source`. Loaded into Weaviate by `make ingest` (see `healthcare_rag/storage/vector_store.py`). |
| `chunks_metformin.json` | Same shape as above, 54 records, for the Metformin monograph. |
| `pageindex_tree_lipitor.json` | Prebuilt PageIndex tree for Lipitor: `{source_pdf, collection, index_model, mode, page_count, generated_at, tree}`. Used only by the PageIndex retrieval arm (`healthcare_rag/processors/pageindex_retrieval.py`, `healthcare_rag/storage/pageindex_index.py`), which is A/B and off by default. |
| `pageindex_tree_metformin.json` | Same shape as above, for Metformin. |

## For AI Agents

### Working In This Directory
- These files are **inputs to ingestion**, not something the app mutates — never write to them from graph/processor code; regenerate them via whatever offline pipeline produced them (contextualization + chunking for `chunks_*.json`; PageIndex tree-building for `pageindex_tree_*.json`) if the source PDFs change.
- `chunks_*.json`'s `contextualized` field (not `text`) is almost certainly what gets embedded — check `healthcare_rag/storage/vector_store.py` before assuming which field a retrieval change should touch.
- Any change to these files changes retrieval, which is one of the areas `AGENTS.md`'s "Measure before/after" rule applies to — run `make eval PREFIX=<change>` and compare against `evals/results/` before merging.
- Adding a third drug monograph means adding both a `chunks_<name>.json` and (if the PageIndex arm should support it) a `pageindex_tree_<name>.json`, plus wiring the new collection name through `make ingest` / `healthcare_rag/storage/vector_store.py`.

### Testing Requirements
- No unit tests target these files directly; `make weaviate ingest` followed by `make eval` is the practical way to verify a data change didn't regress retrieval quality (golden dataset: `evals/golden_dataset.json`).

### Common Patterns
- JSON, not JSONL — each `chunks_*.json` is a single top-level array; each `pageindex_tree_*.json` is a single top-level object keyed by the fields listed above.

## Dependencies

### Internal
- Consumed by `healthcare_rag/storage/vector_store.py` (`chunks_*.json`, via `make ingest`) and `healthcare_rag/storage/pageindex_index.py` / `healthcare_rag/processors/pageindex_retrieval.py` (`pageindex_tree_*.json`)

### External
(none — static data)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
