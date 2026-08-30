<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# scripts/

## Purpose
Standalone operational scripts — smoke tests against a running graph/server,
member-erasure tools, deploy-gate checks, and one-off probes — that are
deliberately outside `make test`/pytest collection. Most are PEP 723
self-executing scripts (`#!/usr/bin/env -S uv run --script` + inline
`# /// script` dependency block) runnable directly with `uv run python
scripts/<name>.py`; a few (`langgraph_smoke.py`, `parity_gate.py`,
`forget_member_api.py`, `seal_clean.py`) are plain modules meant to be run
via `.venv/bin/python` or imported.

## Key Files
| File | Description |
|------|-------------|
| `coach_smoke.py` | Builds a minimal offline `StateGraph` (fake LLM answer, no network) around `healthcare_rag.agent.build.build_coach_graph` to smoke-test the coach agent's gate/relay wiring without hitting OpenAI or Weaviate. |
| `deployed_smoke.py` | The ten-check deployed smoke test (`scripts/verify/f3_realenv.sh` calls it) — exercises a live `LANGGRAPH_DEPLOYMENT_URL` end-to-end with real member tokens; marked `# noqa: SIZE_OK` as one intentionally large file per the acceptance plan. |
| `forget_member.py` | CLI wrapper: logs a member in via Supabase, then drives the erasure flow against a running deployment (`--url`). |
| `forget_member_api.py` | The erasure protocol itself (`ERASE_MARKER_NAME`, `ERASE_QUESTION`, `RUN_ENVELOPE` — posts the exact "Delete all my saved data." run through the LangGraph runs API) — imported by `forget_member.py`, not run directly. |
| `langgraph_smoke.py` | SDK-level smoke test against `make dev` (127.0.0.1:2024) with real Weaviate + LLM calls; slow (5-20s/turn) because it exercises the actual retrieval/generation path. |
| `parity_gate.py` | CLI for `evals.parity.ParityGate` — compares baseline vs. candidate single- and multi-turn eval reports plus code/base SHAs and fails (`exit 1`) on any breach (code seal, provenance, population, metadata, metrics). |
| `probe_chat_openai.py` | Manual, opt-in network probe (~$0.01 real spend) verifying `ChatOpenAI` behavior for both model tiers from `healthcare_rag/services/models.py` — reasoning-effort/temperature interaction, structured output, tool binding, usage metadata, callbacks. Never collected by pytest. |
| `seal_clean.py` | Exit-code wrapper (`0`=clean, `1`=dirty, `2`=git error) around `evals.seal_clean.check_clean` — used to gate that eval runs happen against a sealed (committed) checkout. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `verify/` | Shell/Python gate scripts (`f1`-`f4`) enforcing plan compliance, quality, real-environment, and scope boundaries for a change (see `verify/AGENTS.md`). |

## For AI Agents

### Working In This Directory
- Don't add new scripts here to `make test`'s collection path — the PEP 723 self-executing scripts and the network/spend probes (`probe_chat_openai.py`, `deployed_smoke.py`, `langgraph_smoke.py`) are intentionally opt-in/manual.
- Scripts that hit a real deployment or real LLM read credentials from env vars / `.env`, never from CLI args in plaintext where avoidable, and must not print secrets (see `probe_chat_openai.py`'s comment: "the value is never printed").
- `forget_member_api.py` encodes the exact erasure-run contract (`ERASE_MARKER_NAME`/`ERASE_QUESTION`/`RUN_ENVELOPE`) — changing the marker name or question text breaks compatibility with whatever the graph side matches on; change both sides together.

### Testing Requirements
- These scripts are themselves verification tools rather than being unit-tested; `scripts/verify/f3_realenv.sh` invokes `deployed_smoke.py`, and CI/release flows invoke `parity_gate.py` and `seal_clean.py`. `coach_smoke.py` and `langgraph_smoke.py` are run manually per their header comments.

### Common Patterns
- PEP 723 header (`#!/usr/bin/env -S uv run --script`, `# /// script ... dependencies = [...] ///`) plus a "How to run" comment block at the top of self-executing scripts.
- `from __future__ import annotations` and modern typing (`Final`, `TypeAlias`, `Self`, `override`) throughout, matching the rest of the repo's Python ≥3.11 style.

## Dependencies

### Internal
- `healthcare_rag.agent.*` (`coach_smoke.py`), `evals.parity` (`parity_gate.py`), `evals.seal_clean` (`seal_clean.py`), `healthcare_rag.services.models` (`probe_chat_openai.py`)

### External
- `anyio`, `langgraph`, `langgraph_sdk`, `httpx`, `pydantic`, `langsmith`

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
