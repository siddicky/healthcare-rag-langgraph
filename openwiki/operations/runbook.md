---
type: runbook
title: Local development and Weaviate operations
description: Set up with uv, operate the local vector store, ingest corpus chunks, run the CLI, and recover safely.
tags: [operations, setup, weaviate]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-30T08:22:08.381Z
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
generated: { by: "openwiki/0.4.3", at: "2026-08-30T08:22:08.381Z" }
---

# Local development and Weaviate operations

## Verification status

This page is compiled from the Makefile, `docker-compose.yml`, `pyproject.toml`, CLI source, and repository decision docs; the commands below were **not executed as part of authoring this page** — no live `make venv` / `make weaviate` / `make ingest` / `make run` / `make test` / eval run was performed in this session, so treat their described behavior as documented-from-source, not freshly re-verified. Two things narrow that gap: `docs/decisions/dependabot-requirements-txt.md` records that the "full offline suite" was actually run against the current dependency graph when `requirements.txt` was removed, and `scripts/verify/f2_quality.sh` is itself an executable gate (`make test`, scoped `ruff check`, scoped `basedpyright`, frontend build/test) that CI or a contributor runs and reports PASS/FAIL per stage — it is not merely descriptive prose. If a command's behavior matters for a safety- or release-affecting decision, re-run it locally rather than trusting this page alone.

## Configuration ownership and first run

Configuration has several owners: `pyproject.toml` + `uv.lock` define the Python dependency graph; `.env` is local secret input; `GraphSettings.from_env` snapshots RAG choices; `langgraph.json` selects graphs, auth, custom app, API version and store index; Compose supplies local service overrides; and Fly manifests/workflow provide deployed topology. The [deployment page](deploy.md) describes precedence/production boundaries. Do not assume an environment variable used by one surface is consumed by every other surface.

## First run

1. Install `uv`, Docker, and Docker Compose. Use Python **3.11 or newer**; the project metadata requires `>=3.11` because models use `typing.Self` (`pyproject.toml#L1-L16`).
2. Put only required values in local `.env`: `OPENAI_API_KEY` is required. Optionally set `WEAVIATE_HOST`, `WEAVIATE_PORT`, `WEAVIATE_GRPC_PORT`, model variables, LangSmith variables, and `PINECONE_API_KEY` (needed only for the [pinecone arm and reranker](../retrieval/arms-and-reranking.md)). Never commit or document secret values.
3. Run `make venv`. It intentionally creates Python 3.12 and installs `.[evals,dev,graph-sqlite]` via uv (`Makefile#L23-L25`).
4. Run `make weaviate`; it starts Compose and polls `http://127.0.0.1:8080/v1/.well-known/ready`.
5. Run `make ingest`, then `make run` for `python -m healthcare_rag`.

The CLI shows a raw preliminary response after up to 30 seconds and later a verified response. The preliminary response is not citation-validated; do not use the CLI as a safety boundary ([safety posture](../safety/posture.md)).

There is no root `requirements.txt` in this repository, and none should be added or restored. A frozen `pip freeze` file by that name used to sit at the repo root; it was deleted because its pins were mutually unsatisfiable (nothing could actually `pip install -r` it) and it was the source of 136 of 142 open Dependabot alerts against code no build or deploy path ever consumed — see `docs/decisions/dependabot-requirements-txt.md`. `pyproject.toml` + `uv.lock` remain the only dependency source for the application; the separate, deliberately preserved `tests/server/oracle/requirements.txt` pins an unrelated, isolated oracle test environment and is not a substitute.

## Make targets

| Target | Minimal purpose |
|---|---|
| `make venv` | create `.venv` (Python 3.12), install app + evals + dev + graph-sqlite extras |
| `make weaviate` | start and wait for local Weaviate |
| `make ingest` | destructive rebuild from checked-in chunks (Weaviate) |
| `make ingest-pinecone` | rebuild the same chunks into the Pinecone serverless index (needs `PINECONE_API_KEY` + `OPENAI_API_KEY`) |
| `make index-pageindex` | build `data/pageindex_tree_*.json` in an isolated uv env (~$0.10; the `pageindex` package needs openai>=2 and never touches `.venv`) |
| `make container-build` / `make container-ingest` / `make container-run` | build the app image with the pinned Presidio/spaCy model and run ingest/CLI from it (`docker compose --profile app`) |
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
| `make forget-member` | self-erase a member through the deployed coach flow (`FORGET_ARGS=--dry-run`) |
| `make routing-gate-query-smoke` / `make routing-gate-safety-smoke` | fixture smoke of the routing A/B gates ([routing evaluations](../observability/routing-evals.md)) |
| `make journey` | rebuild `docs/journey.html` from `docs/journey.json` |
| `make wiki-init` / `make wiki-update` | regenerate / refresh these OpenWiki docs |
| `make next-version` / `make release-prep BUMP=...` | preview the next release version / bump `pyproject.toml`+`uv.lock` to it (reviewed PR, not a tag) |
| `make release TAG=vX.Y.Z` / `make release-digest TAG=vX.Y.Z` | print the tag-push commands / resolve a released tag to its immutable image digest (both hermetic, read-only) |
| `make rollback TAG=vX.Y.Z REASON=...` | print the exact `workflow_dispatch` command for a production rollback (hermetic; the actual rollback still requires the gated dispatch) — see [deployment](deploy.md#release-identity-version-bumps-and-rollback) |

## Lint and type-check

There is no `make lint` or `make type-check` target. Static checks are run ad hoc via `uv run ruff check <paths>` and `uv run basedpyright <paths>`, and both are **deliberately scoped**, not repo-wide: `scripts/verify/f2_quality.sh` (the quality gate for one specific plan's own contributions) only lints `healthcare_rag/agent`, a short list of `evals/`, `tests/agent`, and `scripts/` files, and only type-checks `healthcare_rag/agent` with `basedpyright`, expecting `0 errors,` in its output (`scripts/verify/f2_quality.sh#L17-L50`). The rest of the repository predates ruff/basedpyright adoption and is intentionally not retroactively linted; do not widen that scope without a separate decision to adopt these tools repo-wide (`scripts/verify/AGENTS.md#L18-L27`). `make test` (offline pytest) is the one check that does run repo-wide.

## Weaviate hazards and recovery

Compose exposes HTTP 8080 and gRPC 50051, stores data in named `weaviate_data`, allows anonymous access, and has `restart: on-failure:0`—that policy means **no automatic retry** after a failure (`docker-compose.yml#L3-L25`). Diagnose/start it explicitly with `make weaviate` or `docker compose up -d`.

`make ingest` passes `--delete-all`: it erases every collection in the persistent volume before loading `Lipitor` and `Metformin` (`Makefile#L31-L35`). Use it only when that scope is intended. Loader batches 100 rows, skips rows without `text`, stops after more than ten errors, and gives an approximate success count. A non-crashing command is not proof of complete ingestion. If ingestion fails/partially loads: capture the error, correct chunk/schema/service issue, delete the affected collection (or intentionally all), reingest, then run a narrow retrieval eval. Full schema/ID/routing rules are in [retrieval and ingestion](../retrieval/weaviate-and-ingestion.md).

For PDF rechunking, install the `ingest` extra and run `uv run python healthcare_rag/processors/pdf_chunker.py --source <allowed-pdf-path>` or the ingestion CLI. Regeneration changes expected chunk IDs/pages, so update the golden dataset/evals in the same change; do not inspect or copy PDF contents into documentation.

**Broad validation only when needed:** run full judge eval after corpus/model/prompt safety changes. For ordinary code changes use `make eval-smoke` or a filtered deterministic baseline first.
