<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# healthcare_rag

## Purpose
Top-level package for the healthcare RAG assistant. Holds the package
entrypoint (`python -m healthcare_rag`), shared config/env helpers, the
`QueryMonitor` used to stream progress to the CLI, and the subpackages that
make up the two runtimes: the retrieval **graph** (`graph/`, the RAG
StateGraph) and the coach **agent** platform (`agent/`).

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | Package docstring + `__version__`; no exports. |
| `__main__.py` | `python -m healthcare_rag` entry: calls `dotenv.load_dotenv()` **before** importing `cli.interactive.main` so `.env` secrets exist before any model client is constructed. |
| `config.py` | `get_env_var(name, default, required)` — thin env-var getter with a required-var `ValueError`; also loads `.env` at import time for legacy call sites still reading `OPENAI_API_KEY` directly. Model/sampling config actually lives in `services/models.py`, not here. |
| `monitor.py` | `QueryMonitor` — mutable per-turn progress tracker (`current_step`, `status_message`, `steps_completed`, asyncio `Event`s for raw/final answer) used by the CLI to render live status; scrubs PHI via `processors.safety.scrub_phi` before logging. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `agent/` | Coach agent platform: LangGraph `CoachState` graph, safety gate, Route A (RAG relay) / Route B (tool-calling) split, reminders, documents/uploads, HTTP app, auth/perimeter (see `agent/AGENTS.md`). |
| `agent/tools/` | Route B domain tools (`change_schedule`, `log_metric`, `log_injection`, `view_schedule`) exposed to the coach's tool-calling agent (see `agent/tools/AGENTS.md`). |
| `cli/` | Interactive chat CLI and the data-ingestion CLI (see `cli/AGENTS.md`). |
| `graph/` | The LangGraph StateGraph that is the actual RAG runtime — build, engine, routers, state, nodes (see `graph/AGENTS.md`). |
| `graph/nodes/` | Individual graph node implementations (see `graph/nodes/AGENTS.md`). |
| `models/` | Pydantic models shared across the graph, processors, and prompts (see `models/AGENTS.md`). |
| `processors/` | LLM-calling and pure-logic steps used by graph nodes: retrieval, generation, validation, safety, privacy (see `processors/AGENTS.md`). |
| `prompts/` | Jinja YAML prompt templates, one per LLM call site (see `prompts/AGENTS.md`). |
| `services/` | Centralized model selection/sampling and optional LangSmith tracing (see `services/AGENTS.md`). |
| `storage/` | Weaviate/Pinecone/PageIndex ingestion and index-building scripts (see `storage/AGENTS.md`). |

## For AI Agents

### Working In This Directory
- `__main__.py`'s import order is load-bearing: `load_dotenv()` must run
  before `.cli.interactive` is imported, because that import chain eventually
  constructs an OpenAI client that reads `OPENAI_API_KEY` from the environment
  at construction time. Do not reorder those two lines.
- `config.py` is legacy-adjacent — new model/sampling config belongs in
  `services/models.py`, not here (see that module's docstring).
- `QueryMonitor` in `monitor.py` is CLI-facing telemetry, not graph state; the
  actual per-turn state lives in `graph/state.py`'s `RAGState`.

### Testing Requirements
- `tests/test_cli_interactive.py` covers the CLI entrypoint / monitor wiring.
- No dedicated test file for `config.py`; it's exercised indirectly wherever
  env vars are read.

### Common Patterns
- Logging goes through `logging.getLogger("MedicalRAG")` everywhere in this
  package.
- Any text that might contain PHI is passed through
  `processors.safety.scrub_phi` before being logged or displayed.

## Dependencies

### Internal
- `cli/` (entrypoint), `graph/` and `agent/` (the two runtimes), `processors/safety` (PHI scrubbing).

### External
- `python-dotenv` (`.env` loading).

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
