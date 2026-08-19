# Common tasks. All python runs through the uv-managed .venv.
PY := .venv/bin/python
UV := uv

.PHONY: journey help venv weaviate ingest run test test-judges calibrate eval-smoke eval eval-nojudge \
        eval-multiturn eval-multiturn-smoke dataset-sync dataset-sync-multiturn wiki-init wiki-update

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

run: ## Interactive CLI
	$(PY) -m healthcare_rag

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

wiki-init: ## Generate the OpenWiki repo docs (openwiki/) — needs ~/.openwiki/.env
	openwiki code --init -p

wiki-update: ## Refresh OpenWiki docs after code changes
	openwiki code --update -p

journey: ## Rebuild docs/journey.html from docs/journey.json
	$(PY) docs/build_journey_html.py
