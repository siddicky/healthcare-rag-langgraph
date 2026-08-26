---
type: service architecture
title: Clean-room agent server
description: The clean-room server that exposes configured graphs through thread, run, cron, store, assistant, auth, custom-app, readiness, and parity-compatible surfaces, with a dual-backend (memory or Postgres) storage seam that runs Postgres in production.
tags: [server, langgraph, api, parity]
---

# Clean-room agent server

`server/` is a clean-room implementation of the Agent Server surface, with a dual-backend storage seam (`memory` or `postgres` — see below). `langgraph.json` selects the RAG and coach graphs, the auth/custom HTTP modules, store embedding configuration, API version `0.12.6`, and disables MCP/A2A. It is not the base RAG `GraphEngine`; it hosts compiled graphs behind HTTP.

## Startup and readiness

`python -m server` loads configuration and starts Uvicorn. App lifespan creates storage, installs the local compatibility module, loads/recompiles graphs against shared saver/store, enters the custom app lifespan, creates the run engine, starts the cron scheduler, and marks readiness components. Shutdown clears scheduler readiness, cancels scheduler work/runs, and exits the custom lifespan. `/ok` returns 503 until every component is ready; `/info` exposes API version.

```mermaid
flowchart TD
  B["server startup"] --> ST["storage and compatibility shim"]
  ST --> GR["load and compile configured graphs"]
  GR --> CA["enter custom coach app lifespan"]
  CA --> RE["create run engine and cron scheduler"]
  RE --> OK["readiness OK"]
  OK --> SD["shutdown cancels scheduler and runs"]
```

Caption: readiness is not reported before storage, graphs, custom app, and scheduler are established.

## HTTP surface and authorization

Auth applies before dispatch except public `/ok` and `/info`. Native resources are threads/state/copy, runs/wait/stream/join/cancel, crons, store items, and read-only assistants. Custom coach routes are inserted before native routes and add uploads/status, feedback, and internal version. The member-specific authorization and response projection are canonical in [member perimeter](../agent/member-perimeter.md).

The fallback returns 501 only for enumerated documented-but-unimplemented paths; MCP/A2A stay unmounted and return 404/405. Member graph routing/catalog behavior is [coach routing](../agent/coach-routing.md).

## Run/thread/cron semantics

Thread scope mismatches appear as 404. Run input contains exactly one of input or command; server-authenticated identity is injected and client-provided identity stripped. Per-thread conflicts reject, enqueue, or interrupt according to policy; queue overflow is 503 with `Retry-After`. Resume commands are replay-key idempotent. Cancellation rolls back pre-run state; graph exceptions record `error`; shutdown marks pending work interrupted.

Cron schedules validate cron/IANA zone, poll once per second, enqueue due work, and leave queue-conflicted records due for a later pass. Thread delete cascades best-effort run/cron/checkpoint cleanup.

## Storage: dual-backend, Postgres live in production

`SERVER_STORAGE` selects between two backends in `server/storage.py:create_storage()`: `memory` (default; `InMemorySaver`/`InMemoryStore` plus in-process dict registries — nothing survives a restart except Weaviate's own volume) and `postgres` (`AsyncPostgresSaver`/`AsyncPostgresStore` over a pooled `psycopg` connection, plus durable `hc_threads`/`hc_runs`/`hc_crons` tables created with advisory-locked DDL in `server/registries.py`). `server/config.py` requires `DATABASE_URI` (or the Fly-provided `DATABASE_URL` alias) whenever `SERVER_STORAGE=postgres` and rejects any other value.

Production (`deploy/fly.prod.toml`) runs `SERVER_STORAGE=postgres` — the flip shipped in v1.0.7 (2026-08-24) against a dedicated Fly Postgres cluster with the `vector` extension; see [deployment](../operations/deploy.md) and `docs/deploy.md` §§8–9. Threads, store items, and cron registrations now survive deploys/restarts/OOMs; in-flight runs, the pending-run queue, and open SSE streams remain process-local and are still lost on a deploy (`docs/deploy.md` §8). Postgres mode also redacts run `input`/`command` payloads to `[redacted]` at rest (`PERSISTED_PAYLOAD_REDACTION` in `run_engine.py`), while memory mode echoes them raw in process memory — code must not assume either behavior universally. Local/dev defaults to `memory`; embedding-index startup may fall back to lexical store search on recognized dependency/auth failures. `SERVER_LOCAL_DEV` is dev-only and the image sets it off.

Focused tests: `tests/server/test_storage_postgres.py`, `test_threads_postgres.py`, `test_crons_postgres.py`, `test_postgres_durability_gaps.py` (need `POSTGRES_TEST_DSN`, run via `make server-test-pg`); the memory path is covered by the rest of `tests/server/`.

## ThreadStream protocol (member perimeter v2)

`server/protocol_stream.py` + `server/protocol_events.py` implement the v2-native ThreadStream transport that the frontend's `@langchain/react` `useStream` client submits through: `POST /threads/{id}/stream/events` (SSE, validated `channels`/`namespaces`/`depth`/`since`) and `POST /threads/{id}/commands` (per-method validation of `run.start`, `input.respond`, and read-only `state.get`/`state.listCheckpoints`/`state.fork`; `update`/`goto`/`metadata` and unknown methods fail closed with 400). These routes are admitted only when [`HC_RAG_MEMBER_STREAM_PERIMETER=v2`](../agent/member-perimeter.md) — v1 rejects both outright. Implementation reuses the run engine, checkpoint saver, and queue; production parity is pinned by `tests/server/test_protocol_stream.py`, and the hermetic Playwright suite exercises the full protocol (chat, history/time-travel, recovery) against real `langgraph-api` behavior behind the perimeter.

## Boundaries and validation

Use `make server-test`; use `make parity` for pinned-oracle compatibility; `make container-server-smoke` checks compose readiness. Tests under `tests/server/` pin SSE, rollback, cancellation, copy/delete cascade, cron, scoping, and license boundary. Operations/deployment topology is [deployment](../operations/deploy.md).
