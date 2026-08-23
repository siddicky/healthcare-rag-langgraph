<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# tests/server

## Purpose
Tests for the OSS Agent Server (`server/`) — a clean-room, in-memory reimplementation
of the LangGraph Agent Server surface (auth/perimeter topology, threads/runs/crons
engines, storage seam). Unit tests here exercise `server/` in isolation; the
`contract/` and `oracle/` subdirectories together form the parity gate that proves
`server/` behaves identically to the real `langgraph-api` for the endpoints it
implements.

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | Package marker (no tests) |
| `test_assistants_store.py` | The assistants-namespace `/search` route stays a deliberate `501` (not implemented) |
| `test_auth_engine.py` | Scope-filter merging and specific-resource-mismatch hiding; the real auth module loads and is registered by the app factory |
| `test_crons.py` | Cron engine behaviour (creation, ownership, scheduling contract) |
| `test_license_boundary.py` | Source scan finds no forbidden imports; dependency licenses stay within the allowlist; the real `langgraph-api` package is absent from the production SBOM/image — the guardrail keeping the clean-room implementation legally clean-room |
| `test_runs.py` | Runs engine behaviour |
| `test_scaffold.py` | Storage backend rejects a bogus config; `/` and `/ok` info routes are public; unimplemented manifest routes return `501`/`404` per the documented map; graph-storage attachment mutation; no `langgraph_api` import anywhere in `server/` |
| `test_threads.py` | Threads engine behaviour (CRUD, ownership, search) |
| `test_topology.py` | Upload reservation uses the shared shim store; custom routes retain priority ahead of the native catch-all |
| `test_topology_upload.py` | Upload-specific topology wiring |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `contract/` | The parity suite proper: `test_parity.py` (anyio + stub-auth harness) runs the same requests against `server/` and against the pinned oracle, asserting identical responses; `fixtures/` holds one JSON fixture per contract case (`sse_framing`, `rollback`, `delete_cascade`, `resume_idempotency`, `threads_search`, `auth_403_vs_404`, `copy_thread`, `cron_schema`, `if_exists`, `local_dev_studio_trigger`) |
| `oracle/` | The pinned real `langgraph-api==0.12.6` environment used as the parity oracle — `README.md` documents the exact pinned-pair resolution and how to run it; `langgraph.json` is a verbatim copy of the repo root config so relative graph paths resolve the same from either cwd; `.venv/` and `requirements.txt` are the isolated, generated oracle environment — **never hand-edit `.venv/`**, rebuild it per `README.md` instead |

## For AI Agents

### Working In This Directory
- The parity suites are the server contract (see root `AGENTS.md`): `scripts/langgraph_smoke.py` and `scripts/deployed_smoke.py` must pass **unchanged** against `server/`. If `contract/test_parity.py` disagrees with `server/`, the bug is in `server/`, never in the smoke script or the oracle fixtures.
- `contract/` tests are gated by `ORACLE=1` or `CI in ("true","1")` (see `contract/conftest.py`'s `oracle_server` fixture, session-scoped, spawns `tests/server/oracle/.venv/bin/langgraph dev` as a subprocess) — they silently skip otherwise. Set `ORACLE=1` locally before trusting a "green" run to mean parity held.
- `oracle/.venv` is a real installed virtualenv checked into the tree as generated infrastructure; treat it exactly like `evals/results/` — read-only, rebuilt via the documented `uv venv` + `uv pip install --no-config` command in `oracle/README.md`, never edited by hand.
- `test_license_boundary.py` and `test_scaffold.py`'s "no langgraph_api import" checks are load-bearing for the clean-room claim — don't add a `server/` import of `langgraph_api` to work around a missing feature; implement it or route the request through the documented `501` map instead.
- MCP and A2A stay unmounted by `langgraph.json` config (`disable_mcp`/`disable_a2a`) on both the real config and the oracle copy — the smoke suites assert exactly `404`/`405` for those paths; don't wire them up without updating both configs and the smoke assertions together.

### Testing Requirements
```
make server-test    # unit tests only (this directory minus contract/oracle)
make parity         # ORACLE=1, runs the full oracle contract suite — needs the one-time pinned-venv setup in tests/server/oracle/README.md
uv run pytest tests/server/ -q -m "not oracle"
ORACLE=1 uv run pytest tests/server/contract/ -q
```

### Common Patterns
- Contract fixtures are one JSON file per behavioural case under `contract/fixtures/`, loaded by `test_parity.py` rather than inlined — add a new fixture file when adding a new parity case, matching the naming style (`snake_case` behaviour name).
- Server tests build a stub `langgraph_sdk.Auth()` (see `test_parity.py::_stub_auth`) rather than the real auth module when they don't need to test auth itself — reuse that pattern instead of standing up full auth for unrelated engine tests.

## Dependencies

### Internal
- `server/` (the system under test: `config.py`, routes, engines, storage)
- `healthcare_rag/agent/` graphs (`coach`) and `healthcare_rag/graph/` (`healthcare_rag`) — both are mounted in `langgraph.json` and exercised through the oracle

### External
- `httpx`, `anyio` (contract test HTTP client + async harness)
- `langgraph_sdk.Auth` (stub auth in contract tests)
- Pinned `langgraph-cli[inmem]==0.4.31` / `langgraph-api==0.12.6` inside `oracle/.venv` only — not the repo dev venv, which resolves a different (newer) `langgraph-api`

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
