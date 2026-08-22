---
type: runbook
title: Local development and Weaviate operations
description: Set up with uv, operate the local vector store, ingest corpus chunks, run the CLI, and recover safely.
tags: [operations, setup, weaviate]
---

# Local development and Weaviate operations

## First run

1. Install `uv`, Docker, and Docker Compose. Use Python **3.11 or newer**; the project metadata requires `>=3.11` because models use `typing.Self` (`pyproject.toml#L1-L16`).
2. Put only required values in local `.env`: `OPENAI_API_KEY` is required. Optionally set `WEAVIATE_HOST`, `WEAVIATE_PORT`, `WEAVIATE_GRPC_PORT`, model variables, LangSmith variables, and `PINECONE_API_KEY` (needed only for the [pinecone arm and reranker](../retrieval/arms-and-reranking.md)). Never commit or document secret values.
3. Run `make venv`. It intentionally creates Python 3.12 and installs `.[evals,dev,graph-sqlite]` via uv (`Makefile#L11-L13`). Do **not** use `requirements.txt`: it is a large frozen list whose pins conflict with the declared constrained dependencies and is known unsatisfiable for this project brief.
4. Run `make weaviate`; it starts Compose and polls `http://127.0.0.1:8080/v1/.well-known/ready`.
5. Run `make ingest`, then `make run` for `python -m healthcare_rag`.

The CLI shows a raw preliminary response after up to 30 seconds and later a verified response. The preliminary response is not citation-validated; do not use the CLI as a safety boundary ([safety posture](../safety/posture.md)).

## Make targets

| Target | Minimal purpose |
|---|---|
| `make venv` | create `.venv` (Python 3.12), install app + evals + dev + graph-sqlite extras |
| `make weaviate` | start and wait for local Weaviate |
| `make ingest` | destructive rebuild from checked-in chunks |
| `make ingest-pinecone` | rebuild the same chunks into the Pinecone serverless index (needs `PINECONE_API_KEY` + `OPENAI_API_KEY`) |
| `make index-pageindex` | build `data/pageindex_tree_*.json` in an isolated uv env (~$0.10; the `pageindex` package needs openai>=2 and never touches `.venv`) |
| `make container-build` / `make container-ingest` / `make container-run` | build the app image with the pinned Presidio/spaCy model and run ingest/CLI from it (`docker compose --profile app`) |
| `make run` | interactive CLI |
| `make dev` | local LangGraph Agent Server (`langgraph dev`) serving both the `healthcare_rag` graph and the [coach agent](../agent/coach.md) per `langgraph.json` |
| `make test` | offline pytest: evaluator calibration, graph runtime suite (`tests/graph/`), safety gate, and parity gate; no network |
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
| `make journey` | rebuild `docs/journey.html` from `docs/journey.json` |
| `make wiki-init` / `make wiki-update` | regenerate / refresh these OpenWiki docs |

## Weaviate hazards and recovery

Compose exposes HTTP 8080 and gRPC 50051, stores data in named `weaviate_data`, allows anonymous access, and has `restart: on-failure:0`—that policy means **no automatic retry** after a failure (`docker-compose.yml#L3-L25`). Diagnose/start it explicitly with `make weaviate` or `docker compose up -d`.

`make ingest` passes `--delete-all`: it erases every collection in the persistent volume before loading `Lipitor` and `Metformin` (`Makefile#L18-L21`). Use it only when that scope is intended. Loader batches 100 rows, skips rows without `text`, stops after more than ten errors, and gives an approximate success count. A non-crashing command is not proof of complete ingestion. If ingestion fails/partially loads: capture the error, correct chunk/schema/service issue, delete the affected collection (or intentionally all), reingest, then run a narrow retrieval eval. Full schema/ID/routing rules are in [retrieval and ingestion](../retrieval/weaviate-and-ingestion.md).

For PDF rechunking, install the `ingest` extra and run `uv run python healthcare_rag/processors/pdf_chunker.py --source <allowed-pdf-path>` or the ingestion CLI. Regeneration changes expected chunk IDs/pages, so update the golden dataset/evals in the same change; do not inspect or copy PDF contents into documentation.

**Broad validation only when needed:** run full judge eval after corpus/model/prompt safety changes. For ordinary code changes use `make eval-smoke` or a filtered deterministic baseline first. 
anges use `make eval-smoke` or a filtered deterministic baseline first. 
