<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# cli

## Purpose
Command-line entry points: the interactive chat CLI (`make run` /
`python -m healthcare_rag`) that drives the RAG `GraphEngine`, and a separate
data-ingestion CLI (`process_pdf` / `load_to_weaviate` / `run_pipeline`) for
building chunk files and loading them into Weaviate.

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | Re-exports `main`, `interactive_main`, `process_query_with_orchestrator`, `QueryMonitor` from `interactive.py`. Deliberately does **not** import `ingestion.py` — that module pulls in docling/torch/easyocr, and chat-only startup must not force-load the PDF stack; import `healthcare_rag.cli.ingestion` directly when you need it. |
| `interactive.py` | The chat CLI: `process_query_with_orchestrator` (drives one turn through `graph.engine.Engine`), `print_banner`, `interactive_main` (the REPL loop, builds the engine via `build_engine()`), `main()` (sync wrapper, `asyncio.run`). |
| `ingestion.py` | The `make ingest`-style CLI: `process_pdf` (calls `processors.pdf_chunker.run_chunker`), `load_to_weaviate` (calls `storage.vector_store.ingest_json_to_collection`), `run_pipeline` (chains both), `main()` (argparse entry). |

## For AI Agents

### Working In This Directory
- Keep the docling/torch/easyocr import chain confined to `ingestion.py`;
  never import it from `interactive.py` or re-export it from `__init__.py`,
  or plain chat startup (`python -m healthcare_rag`) will drag in the entire
  PDF processing stack.
- `interactive.py` builds its `Engine` once via `build_engine()` and reuses it
  across turns in the REPL loop — don't rebuild it per query.
- PHI scrubbing (`processors.safety.scrub_phi`) is applied before anything is
  printed to the terminal.

### Testing Requirements
- `tests/test_cli_interactive.py` covers `interactive.py`.
- No dedicated test file for `ingestion.py`; it's exercised via
  `make weaviate ingest` and `tests/test_vector_store.py` at the storage layer.

### Common Patterns
- Logging is configured at module import time in both files via
  `logging.basicConfig`, targeting `logging.getLogger("MedicalRAG")` (and
  quieting `httpx`).

## Dependencies

### Internal
- `healthcare_rag/graph/engine.py` (`Engine`, `build_engine`) — the runtime `interactive.py` drives.
- `healthcare_rag/monitor.py` (`QueryMonitor`).
- `healthcare_rag/processors/pdf_chunker.py`, `healthcare_rag/storage/vector_store.py` — `ingestion.py` only.

### External
- `asyncio` (REPL loop), `argparse` (`ingestion.py`).

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
