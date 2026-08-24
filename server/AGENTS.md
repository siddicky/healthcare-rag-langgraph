<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# server/

## Purpose
A clean-room, in-memory implementation of the LangGraph Agent Server HTTP API
(threads, runs, crons, assistants, store, auth), built to be behaviourally
compatible with the real `langgraph-api` package without depending on it. It
exists so the healthcare RAG graph can be served self-hosted (Fly.io) with the
same wire contract that `langgraph dev`/LangGraph Platform expose, while
keeping the implementation OSS and auditable. `server._compat` installs a
`langgraph_api` shim module only when the real package isn't importable, so
graph code that does `from langgraph_api.store import get_store` keeps
working either way. All state (threads/runs/crons/store) is in-process
dicts/`InMemorySaver`/`InMemoryStore` — nothing survives a restart except the
Weaviate vector data (`deploy/fly.weaviate-prod.toml`).

## Key Files
| File | Description |
|------|-------------|
| `app.py` | `create_app(config)` builds the Starlette `ASGIApp`: mounts all route modules with **CORS outermost** wrapping `AuthMiddleware` + `MemberPerimeterMiddleware` (auth inner), so preflight `OPTIONS` are exempted from auth by construction and every response including `401` carries CORS headers; plus lifespan (loads graphs, starts cron scheduler, flips `ReadinessState`) and the `UNIMPLEMENTED_PATHS`/`UNIMPLEMENTED_PREFIXES` → 501 fallback. |
| `__main__.py` | CLI entry (`python -m server`): loads `langgraph.json` via `load_config`, then `uvicorn.run`. |
| `config.py` | `ServerConfig` dataclass + `load_config(path="langgraph.json")` — parses `graphs`, `auth.path`, `http`, `store.index`, `api_version`, `storage`, `port`. |
| `_compat.py` | Installs the `langgraph_api`/`langgraph_api.store` shim (`get_store()`) backed by this server's `InMemoryStore`, but only if the real `langgraph_api` package is absent — never overrides a real install. |
| `manifest.py` | Explicit allow/deny lists (`UNIMPLEMENTED_PATHS`, `UNIMPLEMENTED_PREFIXES`) of documented-but-not-built endpoints that must 501 rather than 404, so client SDKs can distinguish "not supported here" from "route typo". MCP/A2A are deliberately absent from both — those return 404/405 (unmounted), not 501. |
| `auth.py` | `AuthMiddleware`, `AuthPolicyEngine`, `ScopeUser`, `load_auth_instance` — loads the user's `langgraph_sdk.Auth` instance from `auth.path` in `langgraph.json`, runs authN, then per-resource/per-action authZ (`Resource`/`Action` `Literal` unions cover runs/threads/crons/assistants/store). `require_scope_match` / `merge_scope_filter` are the helpers route modules call to enforce the resulting filter. |
| `threads.py` | Thread CRUD, history, state, and the search endpoint; largest file in the module — owns checkpoint-backed thread state via `Storage.saver`. |
| `runs.py` | HTTP routes for run creation/streaming/cancel; thin wrapper translating requests into `RunEngine` calls. |
| `run_engine.py` | `RunEngine`, `RunRequest`/`RunRuntime`/`CancelRequest`, `QueueFull`/`RunConflict`/`RunMissing` — the actual run lifecycle: invokes the loaded graph with `anyio` task groups, queues events (`QUEUE_LIMIT = 100`), supports `Command` resume. |
| `crons.py` | Cron CRUD routes + `start_scheduler()` — a `croniter`-driven asyncio loop that fires due crons as runs. Marked `SIZE_OK`/`ANYIO_OK` (intentionally one file; needs a real `asyncio.Task` handle). |
| `assistants.py` | Read-only assistant records synthesized from `graphs.py`'s loaded graph IDs (this server doesn't support assistant *creation*, matching `manifest.py`'s 501 list). |
| `graphs.py` | `load_raw_graphs`/`attach_graphs` — resolves `langgraph.json`'s `graphs` map (`"name": "./path/to/module.py:attr"`) via `importlib.util.spec_from_file_location`, attaches the compiled graphs to app state. |
| `storage.py` | `Storage` dataclass (`saver: InMemorySaver`, `store: InMemoryStore`, plus `threads`/`runs`/`crons` dicts) and `create_storage(config)` — the single storage seam every route module reads/writes through. |
| `store_routes.py` | `/store/*` item routes (put/get/delete/search) with namespace validation (`_validate_namespace` rejects empty labels and periods in labels). |
| `config.py` | (see above) |
| `Dockerfile` | Container build for the server (see `deploy/fly.prod.toml` for the runtime env it expects). |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `routes/` | Small standalone route modules not tied to one resource engine — currently just `/ok` and `/info` (see `routes/AGENTS.md`). |

## For AI Agents

### Working In This Directory
- This is a **parity target**, not a place for creative API design: any behaviour change must be checked against `tests/server/oracle/` (a pinned real `langgraph-api` venv) via `make parity`, or it can silently diverge from the contract clients expect.
- `manifest.py`'s two lists are the source of truth for "not implemented → 501" vs "not mounted → 404/405". Adding a real implementation for something in `UNIMPLEMENTED_PATHS`/`_PREFIXES` means removing it from the list, not adding a second route.
- `_compat.py` must never install its shim when a real `langgraph_api` is present — it re-raises any `ModuleNotFoundError` whose name isn't exactly `langgraph_api`, so don't broaden that except with a matching test.
- Everything is in-memory (`storage.py`). Don't add code elsewhere that assumes durability across a process restart.
- `SERVER_LOCAL_DEV` must stay unset/`0` in production (see `deploy/fly.prod.toml`); code paths gated on `config.local_dev` should not be reachable from a prod build.

### Testing Requirements
- Unit/behavioural tests: `tests/server/test_*.py` (auth engine, threads, runs, crons, assistants+store, topology, license boundary, scaffold).
- Contract/parity tests: `tests/server/contract/test_parity.py` plus the pinned-venv oracle suite under `tests/server/oracle/` (see its `README.md` for one-time setup) — run both with `make parity`.
- `scripts/langgraph_smoke.py` and `scripts/coach_smoke.py` exercise this server (or the SDK talking to it) end-to-end against a running graph.

### Common Patterns
- Route modules export a module-level `routes: list[Route]` consumed by `app.py`; keep new endpoints in that shape rather than registering directly on the Starlette app.
- Pydantic v2 models (`ConfigDict`, `JsonValue`, `model_validator`) for request/response bodies; `_to_jsonable()` helpers (duplicated per-file, e.g. `run_engine.py`, `threads.py`) normalize dataclasses/pydantic models/UUIDs to JSON before responding.
- Auth checks go through `auth.py`'s `require_scope_match`/`merge_scope_filter` rather than ad hoc `if user.identity == ...` checks in route handlers.

## Dependencies

### Internal
- `healthcare_rag.agent.perimeter_middleware.MemberPerimeterMiddleware` (mounted in `app.py`)
- `healthcare_rag/graph/` (the compiled graphs loaded via `graphs.py`)

### External
- `starlette`, `uvicorn`, `anyio`, `langgraph` (`langgraph.checkpoint.memory.InMemorySaver`, `langgraph.store.memory.InMemoryStore`, `langgraph.types.Command`), `langgraph_sdk.Auth`, `pydantic`, `croniter`

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
