---
type: runbook
title: Local development and Weaviate operations
description: Set up with uv, operate the local vector store, ingest corpus chunks, run the CLI, and recover safely.
tags: [operations, setup, weaviate]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-31T08:29:16.011Z
sources:
  - id: openwiki-source-b79fbbd921df689b4bbdc82f
    resource: repo://docker-compose.yml
  - id: openwiki-source-d6dbe2ca06d9e4feabdcde4d
    resource: repo://docs/decisions/dependabot-requirements-txt.md
  - id: openwiki-source-9ea0ad09734f76a2a018f757
    resource: repo://healthcare_rag/cli/ingestion.py
  - id: openwiki-source-5c711a56e5188717c4713fff
    resource: repo://healthcare_rag/cli/interactive.py
  - id: openwiki-source-184fc99d49c5faae867575f7
    resource: repo://healthcare_rag/graph/engine.py
  - id: openwiki-source-677e462492608ccb485d5838
    resource: repo://healthcare_rag/graph/resources.py
  - id: openwiki-source-9b0965dd12a5f2c42ed4d2a7
    resource: repo://healthcare_rag/graph/settings.py
  - id: openwiki-source-e388d26ca384c3908b72d915
    resource: repo://healthcare_rag/models/answers.py
  - id: openwiki-source-904a6ad11b7380a83f2adb25
    resource: repo://healthcare_rag/models/queries.py
  - id: openwiki-source-54388b396a525f7713df8466
    resource: repo://healthcare_rag/storage/vector_store.py
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-012f2c78e3b1446dfc35803f
    resource: repo://Makefile
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-5bb7d0b1a38391514c8323ce
    resource: repo://scripts/verify/AGENTS.md
  - id: openwiki-source-3acebc4115d4367a1b6d536d
    resource: repo://scripts/verify/f2_quality.sh
  - id: openwiki-source-e06cba2513dc7197f9b11911
    resource: repo://tests/test_cli_interactive.py
generated: { by: "openwiki/0.4.3", at: "2026-08-31T08:29:16.011Z" }
---

# Local development and Weaviate operations

## Verification status

This page is compiled from the Makefile, `docker-compose.yml`, `pyproject.toml`, CLI/graph source, and repository decision docs; the commands below were **not executed as part of authoring this page** — no live `make venv` / `make weaviate` / `make ingest` / `make run` / `make test` / eval run was performed in this session, so treat their described behavior as documented-from-source, not freshly re-verified. Two things narrow that gap: `docs/decisions/dependabot-requirements-txt.md` records that the "full offline suite" was actually run against the current dependency graph when `requirements.txt` was removed, and `scripts/verify/f2_quality.sh` is itself an executable gate (`make test`, scoped `ruff check`, scoped `basedpyright`, frontend build/test) that CI or a contributor runs and reports PASS/FAIL per stage — it is not merely descriptive prose. The one exception below is the CLI's initialization-failure behavior, which is corroborated by `tests/test_cli_interactive.py`, an actual subprocess-based test that asserts on the printed message and exit code. If a command's behavior matters for a safety- or release-affecting decision, re-run it locally rather than trusting this page alone.

## Configuration ownership and first run

Configuration has several owners: `pyproject.toml` + `uv.lock` define the Python dependency graph; `.env` is local secret input; `GraphSettings.from_env` snapshots RAG choices; `langgraph.json` selects graphs, auth, custom app, API version and store index; Compose supplies local service overrides; and Fly manifests/workflow provide deployed topology. The [deployment page](deploy.md) describes precedence/production boundaries. Do not assume an environment variable used by one surface is consumed by every other surface.

## First run

1. Install `uv`, Docker, and Docker Compose. Use Python **3.11 or newer**; the project metadata requires `>=3.11,<3.15` because models use `typing.Self` (`pyproject.toml#L1-L6`).
2. Put only required values in local `.env`: `OPENAI_API_KEY` is required. Optionally set `WEAVIATE_HOST` (default `127.0.0.1`), `WEAVIATE_PORT`, `WEAVIATE_GRPC_PORT`, model variables, LangSmith variables, and `PINECONE_API_KEY` (needed only for the [pinecone arm and reranker](../retrieval/arms-and-reranking.md)). Never commit or document secret values.
3. Run `make venv`. It intentionally creates Python 3.12 and installs `.[evals,dev,graph-sqlite]` via uv (`Makefile#L23-L25`).
4. Run `make weaviate`; it starts Compose and polls `http://127.0.0.1:8080/v1/.well-known/ready`.
5. Run `make ingest`, then `make run` for `python -m healthcare_rag`.

The CLI shows a raw preliminary response after up to 30 seconds and later a verified response. The preliminary response is not citation-validated; do not use the CLI as a safety boundary ([safety posture](../safety/posture.md)).

There is no root `requirements.txt` in this repository, and none should be added or restored. A frozen `pip freeze` file by that name used to sit at the repo root; it was deleted because its pins were mutually unsatisfiable (nothing could actually `pip install -r` it) and it was the source of 136 of 142 open Dependabot alerts against code no build or deploy path ever consumed — see `docs/decisions/dependabot-requirements-txt.md`. `pyproject.toml` + `uv.lock` remain the only dependency source for the application; the separate, deliberately preserved `tests/server/oracle/requirements.txt` pins an unrelated, isolated oracle test environment and is not a substitute.

## Common startup/runtime failures and recovery

The interactive CLI deliberately hides implementation detail on init failure: `interactive_main()` wraps `build_engine()` in a bare `except Exception` and prints only `✗ Error initializing system: PRIVACY_OR_RUNTIME_INITIALIZATION_FAILED` with a nonzero exit code — no traceback, no config values, no distinction between "Weaviate unreachable" and "bad env var" (`healthcare_rag/cli/interactive.py#L57-L62`). `tests/test_cli_interactive.py` asserts exactly this: nonzero exit, the fixed message present, `Traceback` and the offending config value absent. Because the CLI will not tell you *which* dependency failed, diagnose using the underlying resource checks directly:

| Symptom | Likely cause | Recovery |
|---|---|---|
| CLI prints `PRIVACY_OR_RUNTIME_INITIALIZATION_FAILED` immediately | Any exception during `build_engine()` — most commonly a missing/invalid env var or an unreachable Weaviate | Re-run with `python -c "from healthcare_rag.graph.settings import GraphSettings; GraphSettings.from_env()"` or call the failing path directly to surface the real exception; check the two causes below first |
| `Required environment variable OPENAI_API_KEY is not set` (raised from `Resources.weaviate()` or `vector_store.py`) | `.env` missing `OPENAI_API_KEY`, or `.env` not loaded (wrong working directory) | Add `OPENAI_API_KEY` to `.env` at the repo root; the vectorizer (`text2vec-openai`) and the CLI both need it, not just the LLM calls |
| Weaviate connect hangs or raises `WeaviateStartUpError` / connection refused | Weaviate container not started, still starting, or `WEAVIATE_HOST`/`WEAVIATE_PORT`/`WEAVIATE_GRPC_PORT` point somewhere else than the Compose defaults (`127.0.0.1:8080`/`50051`) | Run `make weaviate` (blocks until `/v1/.well-known/ready` succeeds) or `docker compose ps` / `docker compose logs weaviate`; confirm `.env` host/port match Compose; because `restart: on-failure:0`, a crashed container is **not** retried automatically — rerun `make weaviate` explicitly |
| `make ingest` exits nonzero or logs many batch errors | Malformed rows in `data/chunks_*.json`, or Weaviate rejecting the schema (e.g. it wasn't fully cleared from a previous partial run) | Re-run `make ingest` (it always starts with `--delete-all`, so it self-heals a partially-loaded collection); if it still fails, inspect the batch error log lines directly — the importer stops after more than 10 batch errors and only logs an approximate success count, so absence of a crash does not mean ingestion is complete |
| `container-ingest` / `container-run` never start | `docker-compose.yml`'s `healthcare-rag` service has `depends_on: weaviate: condition: service_healthy`, and Weaviate's own healthcheck polls every 2s with 30 retries and a 5s start period — if Weaviate never reaches ready, Compose will not start the app container at all | Check `docker compose logs weaviate` for the underlying startup error before assuming the app image is broken |
| `container-run`/`container-ingest` crash on an unexpected file write | The `healthcare-rag` service runs with `read_only: true` and only `/tmp` mounted as writable (`tmpfs: [/tmp]`) | Direct any ad hoc output to `/tmp` inside the container, or run the equivalent `.venv`-based command (`make ingest`/`make run`) instead of the containerized one for exploratory work |

## Make targets

| Target | Minimal purpose |
|---|---|
| `make venv` | create `.venv` (Python 3.12), install app + evals + dev + graph-sqlite extras |
| `make weaviate` | start and wait for local Weaviate |
| `make ingest` | destructive rebuild from checked-in chunks (Weaviate) |
| `make ingest-pinecone` | rebuild the same chunks into the Pinecone serverless index (needs `PINECONE_API_KEY` + `OPENAI_API_KEY`) |
| `make index-pageindex` | build `data/pageindex_tree_*.json` in an isolated uv env (~$0.10; the `pageindex` package needs openai>=2 and never touches `.venv`) |
| `make container-build` / `make container-ingest` / `make container-run` | build the app image with the pinned Presidio/spaCy model and run ingest/CLI from it (`docker compose --profile app`); the container runs read-only except for `/tmp` and only starts once Weaviate's own healthcheck passes |
| `make run` | interactive CLI |
| `make dev` | local LangGraph Agent Server (`langgraph dev`) serving both the `healthcare_rag` graph and the [coach agent](../agent/coach.md) per `langgraph.json` |
| `make test` | offline pytest: evaluator calibration, privacy/routing/graph suites (`tests/graph/`), safety gate, and parity gate; no network |
| `make test-judges` | LLM-judge calibration tests, `-m judge`, need `OPENAI_API_KEY` (~$0.10) |
| `make calibrate` | print evaluator calibration report |
| `make dataset-sync` / `make dataset-sync-multiturn` | upsert golden / multi-turn datasets to LangSmith |
| `make eval-smoke` | three-example real pipeline smoke |
| `make eval PREFIX=name` | full eval and report |
| `make eval-nojudge PREFIX=name` | full deterministic-only eval |
| `make eval-holdout` | hold-out split only |
| `make eval-ablations` | stage-ablation experiments (no-validate/no-evaluate/no-decompose) |
| `make eval-multiturn` / `make eval-multiturn-smoke` | multi-turn conversation eval (full / 2-conversation no-judge) |
| `make compare EXPS="a b c"` | side-by-side experiment comparison by category |
| `make eval-agent` / `make eval-agent-multiturn` | offline in-process [coach agent](../agent/coach.md) evaluations |
| `make deployed-smoke` | ten-check smoke against `LANGGRAPH_DEPLOYMENT_URL` |
| `make deployed-smoke-gate` | fast LLM-free subset of the same smoke (`--profile gate`), seconds not minutes |
| `make forget-member` | self-erase a member through the deployed coach flow (`FORGET_ARGS=--dry-run`) |
| `make routing-gate-query-smoke` / `make routing-gate-safety-smoke` | fixture smoke of the routing A/B gates ([routing evaluations](../observability/routing-evals.md)) |
| `make journey` | rebuild `docs/journey.html` from `docs/journey.json` |
| `make wiki-init` / `make wiki-update` | regenerate / refresh these OpenWiki docs |
| `make next-version` / `make release-prep BUMP=...` | preview the next release version / bump `pyproject.toml`+`uv.lock` to it (reviewed PR, not a tag) |
| `make release TAG=vX.Y.Z` / `make release-digest TAG=vX.Y.Z` | print the tag-push commands / resolve a released tag to its immutable image digest (both hermetic, read-only) |
| `make rollback TAG=vX.Y.Z REASON=...` | print the exact `workflow_dispatch` command for a production rollback (hermetic; the actual rollback still requires the gated dispatch) — see [deployment](deploy.md#release-identity-version-bumps-and-rollback) |

## Lint and type-check

There is no `make lint` or `make type-check` target. Static checks are run ad hoc via `uv run ruff check <paths>` and `uv run basedpyright <paths>`, and both are **deliberately scoped**, not repo-wide: `scripts/verify/f2_quality.sh` (the quality gate for one specific plan's own contributions) only lints `healthcare_rag/agent`, a short list of `evals/`, `tests/agent`, and `scripts/` files, and only type-checks `healthcare_rag/agent` with `basedpyright`, expecting `0 errors,` in its output (`scripts/verify/f2_quality.sh#L17-L50`). The rest of the repository predates ruff/basedpyright adoption and is intentionally not retroactively linted; do not widen that scope without a separate decision to adopt these tools repo-wide (`scripts/verify/AGENTS.md#L18-L27`). `make test` (offline pytest) is the one check that does run repo-wide; its default `pytest` invocation is scoped by `pyproject.toml`'s `addopts = "-m 'not judge'"`, so `judge`-marked tests are opt-in only.

## Weaviate hazards and recovery

Compose exposes HTTP 8080 and gRPC 50051, stores data in named volume `weaviate_data`, allows anonymous access, and has `restart: on-failure:0`—that policy means **no automatic retry** after a failure (`docker-compose.yml#L1-L29`). The container's own healthcheck (`wget` against `/v1/.well-known/ready`, every 2s, 30 retries, 5s start period) is what `depends_on: condition: service_healthy` waits on for the `healthcare-rag` app service; `make weaviate`'s own `curl` polling loop against the same endpoint is a separate, Makefile-level wait used by the bare (non-containerized) workflow. Diagnose/start Weaviate explicitly with `make weaviate` or `docker compose up -d`; a crashed container will sit stopped until you do.

`make ingest` passes `--delete-all`: it erases every collection in the persistent volume before loading `Lipitor` and `Metformin` (`Makefile#L31-L35`). Use it only when that scope is intended. Loader batches 100 rows, skips rows without `text`, stops after more than ten errors, and gives an approximate success count. A non-crashing command is not proof of complete ingestion. If ingestion fails/partially loads: capture the error, correct chunk/schema/service issue, delete the affected collection (or intentionally all), reingest, then run a narrow retrieval eval. Full schema/ID/routing rules are in [retrieval and ingestion](../retrieval/weaviate-and-ingestion.md).

For PDF rechunking, install the `ingest` extra and run `uv run python healthcare_rag/processors/pdf_chunker.py --source <allowed-pdf-path>` or the ingestion CLI (`healthcare_rag/cli/ingestion.py`, which also exposes `process-pdf`, `load-weaviate`, and a combined `pipeline` subcommand). Regeneration changes expected chunk IDs/pages, so update the golden dataset/evals in the same change; do not inspect or copy PDF contents into documentation.

**Broad validation only when needed:** run full judge eval after corpus/model/prompt safety changes. For ordinary code changes use `make eval-smoke` or a filtered deterministic baseline first.
