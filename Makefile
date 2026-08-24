# Common tasks. All python runs through the uv-managed .venv.
PY := .venv/bin/python
UV := uv

# `pageindex` needs openai>=2, which conflicts with the venv's openai<2 pin
# (langchain-openai). It therefore runs in an ephemeral, throwaway environment
# and never touches .venv; the runtime only reads the JSON trees it writes.
PAGEINDEX_RUN := $(UV) run --no-project --with pageindex --with python-dotenv --python 3.12 \
	python healthcare_rag/storage/pageindex_index.py

.PHONY: journey help venv weaviate ingest run container-build container-ingest container-run \
        dev test test-judges calibrate eval-smoke eval eval-nojudge index-pageindex \
        ingest-pinecone \
        eval-multiturn eval-multiturn-smoke eval-agent eval-agent-multiturn deployed-smoke forget-member \
        dataset-sync dataset-sync-multiturn \
        routing-gate-query-smoke routing-gate-safety-smoke wiki-init wiki-update \
        server-dev server-test server-test-pg parity server-image container-server-smoke ingest-fly release

help:
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

venv: ## Create .venv (Python 3.12) and install app + evals + dev + graph-sqlite deps
	$(UV) venv --python 3.12 .venv
	$(UV) pip install --python $(PY) -e ".[evals,dev,graph-sqlite]"

weaviate: ## Start Weaviate (docker compose) and wait until ready
	docker compose up -d
	@until curl -sf http://127.0.0.1:8080/v1/.well-known/ready >/dev/null; do sleep 1; done; echo "weaviate ready"

ingest: ## (Re)ingest checked-in chunks into Weaviate (needs OPENAI_API_KEY in .env)
	$(PY) healthcare_rag/storage/vector_store.py --delete-all \
		--collection Lipitor data/chunks_lipitor.json \
		--collection Metformin data/chunks_metformin.json

ingest-pinecone: ## (Re)ingest the checked-in chunks into Pinecone (needs PINECONE_API_KEY + OPENAI_API_KEY)
	$(PY) healthcare_rag/storage/pinecone_store.py --delete-all \
		--collection Lipitor data/chunks_lipitor.json \
		--collection Metformin data/chunks_metformin.json

index-pageindex: ## Build the cached PageIndex trees for both monographs (isolated env; ~$0.10)
	$(PAGEINDEX_RUN) --pdf docs/lipitor.pdf   --out data/pageindex_tree_lipitor.json   $(PAGEINDEX_ARGS)
	$(PAGEINDEX_RUN) --pdf docs/metformin.pdf --out data/pageindex_tree_metformin.json $(PAGEINDEX_ARGS)

run: ## Interactive CLI
	$(PY) -m healthcare_rag

container-build: ## Build the app image with the pinned Presidio/spaCy model baked in
	docker compose --profile app build healthcare-rag

container-ingest: weaviate ## (Re)ingest checked-in chunks from the app image
	docker compose --profile app run --rm healthcare-rag \
		python -m healthcare_rag.storage.vector_store --delete-all \
		--collection Lipitor data/chunks_lipitor.json \
		--collection Metformin data/chunks_metformin.json

container-run: weaviate ## Run the privacy-safe interactive CLI from the app image
	docker compose --profile app run --rm healthcare-rag

dev: ## Start the local LangGraph Agent Server
	.venv/bin/langgraph dev

test: ## Offline tests (evaluator calibration, deterministic subset)
	$(PY) -m pytest -q

test-judges: ## LLM-judge calibration (calls OpenAI; ~$0.10)
	$(PY) -m pytest -q -m judge

calibrate: ## Print evaluator calibration report (all evaluators)
	$(PY) -m evals.calibrate

dataset-sync: ## Upsert evals/golden_dataset.json to LangSmith
	$(PY) -m evals.dataset

dataset-sync-multiturn: ## Upsert evals/multiturn_dataset.json to LangSmith
	$(PY) -m evals.multiturn_dataset

eval-smoke: ## 3-example smoke eval (fast sanity check)
	$(PY) -m evals.run_baseline --prefix smoke --limit 3

eval: ## Full golden-set eval → LangSmith experiment + evals/results/<name>.md   (PREFIX=name)
	$(PY) -m evals.run_baseline --prefix $(or $(PREFIX),eval)

eval-nojudge: ## Full eval, deterministic metrics only (no LLM judges; cheapest)
	$(PY) -m evals.run_baseline --prefix $(or $(PREFIX),eval) --no-judges

eval-holdout: ## Hold-out split only (guards against tuning to the core set)   (PREFIX=name)
	$(PY) -m evals.run_baseline --prefix $(or $(PREFIX),holdout) --split holdout

eval-ablations: ## Stage ablations: no-validate, no-evaluate, no-decompose (3 experiments, sequential)
	HC_RAG_DISABLE_STAGES=validate  $(PY) -m evals.run_baseline --prefix abl-no-validate  --no-sync $(EVAL_ARGS)
	HC_RAG_DISABLE_STAGES=evaluate  $(PY) -m evals.run_baseline --prefix abl-no-evaluate  --no-sync $(EVAL_ARGS)
	HC_RAG_DISABLE_STAGES=decompose $(PY) -m evals.run_baseline --prefix abl-no-decompose --no-sync $(EVAL_ARGS)

compare: ## Side-by-side of experiment reports: make compare EXPS="a b c"
	$(PY) -m evals.compare $(EXPS) --by-category

eval-multiturn: ## Multi-turn conversation eval → LangSmith experiment + report   (PREFIX=name)
	$(PY) -m evals.run_multiturn --prefix $(or $(PREFIX),multiturn)

eval-multiturn-smoke: ## 2-conversation multi-turn smoke (no LLM judges)
	$(PY) -m evals.run_multiturn --prefix mt-smoke --limit 2 --no-judges

eval-agent: ## Offline in-process coach agent evaluation
	$(PY) evals/run_agent.py --offline

eval-agent-multiturn: ## Offline in-process coach multi-turn evaluation
	$(PY) evals/run_agent_multiturn.py --offline

deployed-smoke: ## Run the ten-check smoke against LANGGRAPH_DEPLOYMENT_URL
	$(UV) run python scripts/deployed_smoke.py --url "$(LANGGRAPH_DEPLOYMENT_URL)"

forget-member: ## Self-erase a member through the deployed coach flow (FORGET_ARGS=--dry-run)
	$(UV) run python scripts/forget_member.py --url "$(LANGGRAPH_DEPLOYMENT_URL)" $(FORGET_ARGS)

routing-gate-query-smoke: ## Validate the paired query-routing gate contract with fixtures
	$(PY) -m evals.routing_gate --lane query --smoke --json

routing-gate-safety-smoke: ## Validate the paired safety-routing gate contract with fixtures
	$(PY) -m evals.routing_gate --lane safety --smoke --json

wiki-init: ## Generate the OpenWiki repo docs (openwiki/) — needs ~/.openwiki/.env
	openwiki code --init -p

wiki-update: ## Refresh OpenWiki docs after code changes
	openwiki code --update -p

journey: ## Rebuild docs/journey.html from docs/journey.json
	$(PY) docs/build_journey_html.py

server-dev: ## Run the OSS Agent Server locally (port 2024, local-dev auth bypass; SERVER_RELOAD=1 for reload)
	SERVER_PORT=2024 SERVER_LOCAL_DEV=1 $(PY) -m server
	# SERVER_RELOAD=1 is not yet wired in server/__main__.py — follow-up to add uvicorn --reload via env if needed

server-test: ## Run server unit tests
	$(PY) -m pytest -q tests/server

server-test-pg: ## Run Postgres-gated server tests via compose (gated lane; ephemeral, re-runnable)
	docker compose -f docker-compose.server.yml up -d postgres
	@for i in $$(seq 1 30); do docker exec healthcare-rag-cors-pg-postgres-1 pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done; echo "postgres ready"
	# test_concurrent_storage_setup_uses_advisory_lock is deselected here — see tests/server/test_registries.py docstring for full explanation; re-verified via scripts/pg_lane_concurrent.py
	@POSTGRES_TEST_DSN=postgresql://postgres:postgres@127.0.0.1:55433/postgres $(PY) -m pytest -q -k "not test_concurrent_storage_setup_uses_advisory_lock" tests/server/test_registries.py tests/server/test_threads_postgres.py tests/server/test_runs_durable.py tests/server/test_crons_postgres.py tests/server/test_storage_postgres.py; RC1=$$?; POSTGRES_TEST_DSN=postgresql://postgres:postgres@127.0.0.1:55433/postgres $(PY) scripts/pg_lane_concurrent.py; RC2=$$?; if [ $$RC1 -ne 0 ] || [ $$RC2 -ne 0 ]; then RC=1; else RC=0; fi; docker rm -f healthcare-rag-cors-pg-postgres-1 >/dev/null 2>&1 || true; docker volume rm -f healthcare-rag-cors-pg_postgres_data >/dev/null 2>&1 || true; docker compose -f docker-compose.server.yml rm -s -f -v postgres >/dev/null 2>&1 || true; exit $$RC

parity: ## Run oracle contract suite (ORACLE=1) — see tests/server/oracle/README.md for one-time pinned-venv setup
	ORACLE=1 $(PY) -m pytest -q tests/server/contract

server-image: ## Build the OSS server image (hc-rag-server:dev)
	docker build -f server/Dockerfile -t hc-rag-server:dev .

container-server-smoke: ## Smoke the compose server stack (/ok probe)
	docker compose -f docker-compose.server.yml up -d && sleep 5 && curl -sf http://127.0.0.1:8000/ok && docker compose -f docker-compose.server.yml down -v

ingest-fly: ## Ingest checked-in chunks into prod Weaviate via Fly machine (see docs/deploy.md)
	# See docs/deploy.md for full context once written
	@echo "fly machines run ingest against prod Weaviate — see docs/deploy.md for the exact command and required env (FLY_API_TOKEN, WEAVIATE_HOST=hc-rag-weaviate-prod.internal)"
	@echo "Example: fly machines run --app hc-rag-server-prod -e WEAVIATE_HOST=hc-rag-weaviate-prod.internal ghcr.io/<repo>/hc-rag-server:latest python -m healthcare_rag.storage.vector_store --delete-all --collection Lipitor data/chunks_lipitor.json --collection Metformin data/chunks_metformin.json"

release: ## Validate TAG=vX.Y.Z and print the exact release push commands (hermetic, no push)
	@if [ -z "$(TAG)" ]; then echo "usage: make release TAG=vX.Y.Z  (e.g. make release TAG=v1.2.3)"; echo "  TAG must match ^v[0-9]+\.[0-9]+\.[0-9]+$$ (e.g. v1.2.3, v0.1.0-rc)"; exit 1; fi
	@if ! echo "$(TAG)" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z._-]+)?$$'; then echo "error: TAG='$(TAG)' must match ^v[0-9]+\.[0-9]+\.[0-9]+$$ (e.g. v1.2.3)"; echo "usage: make release TAG=vX.Y.Z"; exit 1; fi
	@echo "Would run (hermetic — human pushes):"
	@echo "  git tag -s $(TAG) -m \"release $(TAG)\""
	@echo "  git push origin $(TAG)"
