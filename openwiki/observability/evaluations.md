---
type: evaluation system
title: LangSmith tracing and regression evaluations
description: Opt-in observability plus single-turn and multi-turn real-pipeline evaluation, metrics, reports, and comparison workflow.
tags: [observability, evaluations, langsmith]
---

# LangSmith tracing and regression evaluations

## Opt-in tracing and UI telemetry

Tracing is disabled unless `LANGSMITH_TRACING` (or `LANGCHAIN_TRACING_V2`) is `1`, `true`, or `yes`. With it enabled and `langsmith` installed, `wrap_openai_client` wraps OpenAI clients and `traceable`/`rag_stage` decorate stage functions; without it, helpers are no-ops and missing `langsmith` merely logs a warning (`healthcare_rag/services/tracing.py`). `LANGSMITH_API_KEY` is required for remote use; `LANGSMITH_PROJECT` is optional. In the graph runtime the root run is `GraphEngine.process_query` (a `@traceable` whose `process_inputs` **fail-closed scrubs the question** before LangSmith stores it), retrieval is traced as a `retriever` run inside `retrieve_documents`, and tests force tracing off unless `HC_RAG_TEST_TRACING=true` (`tests/conftest.py`).

`QueryMonitor` signals the **first raw generated answer** (the `generate_answer` node update, only for non-refusals) and the **finalized** answer (the `finalize` node update) for interactive timing (`graph/engine.py#L162-L204`). The raw answer is not citation-validated; do not report it as the verified final answer.

## Single-turn golden suite

`evals/golden_dataset.json` is the versioned source of truth. `to_langsmith_example` maps stable row IDs to UUID5 examples, preserving question/history input and reference answer, expected behavior, required/forbidden terms, expected chunks/pages, drug, category, notes, and split; sync creates missing IDs and updates existing IDs (`evals/dataset.py#L25-L77`). Categories include factual single/multi, cross-drug, ambiguous follow-up, out-of-scope, unsafe personal advice, adversarial hallucination, and PII/PHI.

The harness targets the real graph engine: `run_one` seeds legacy history into a fresh eval `user_id` via `engine.seed_history` (scrubbed `HumanMessage`/`AIMessage` pairs written into the checkpointer) and runs `engine.run_turn`, which returns the legacy-shaped evaluation record — final/raw answer, contexts with chunk/page/source IDs, folded branch telemetry, latency, usage, the caught error, and the `safety_outcome` record (gate category, `contains_phi`, `short_circuited`, `response_kind`, deterministic flags, `gate_latency_s`) for per-example gate auditing (`evals/harness.py`; `graph/engine_record.py`). LLM usage is collected per turn by the `UsageRecorder` callback rather than monkey-patched OpenAI clients (`graph/engine.py#L53-L106`). It never writes production conversations.

Deterministic evaluators measure answer/crash, required/forbidden content, refusal heuristic, numeric-advice leak (a specific number with a clinical unit in a refuse-expected answer — the failure mode the [safety gate](../safety/gate.md) templates are hard-coded to avoid), chunk/page recall, route correctness, latency, cost, and branch usage. LLM judges measure correctness, groundedness/hallucination, behavior/safe redirect, and false-premise correction. Out-of-scope routing expects no source, refusal/clarify cases selectively skip incompatible metrics, and adversarial cases use the dedicated premise judge (`evals/evaluators.py#L88-L244`, `#L284-L397`). Judges use a separate untraced client (`EVAL_JUDGE_MODEL`, default `gpt-5.6-sol`; `EVAL_JUDGE_REASONING_EFFORT`, default `medium`), so their cost/latency is outside the system under test.

### Run, report, compare

```bash
make dataset-sync
make eval-smoke
make eval-nojudge PREFIX=my-change
make eval PREFIX=my-change
uv run python -m evals.run_baseline --category unsafe_personal_advice --category pii_or_phi
uv run python -m evals.compare baseline-name my-change --by-category
uv run python -m evals.rescore --experiment my-change --evaluator groundedness_judge
```

The runner requires `OPENAI_API_KEY` and `LANGSMITH_API_KEY`; it uploads experiments even with tracing off, but per-stage cost attribution needs tracing. Reports land at `evals/results/<experiment>.json` and `.md`, with overall/category aggregates, p50/p95, and LangSmith stage-cost tree (`evals/run_baseline.py`; `evals/report.py`). `evals/results/` is excluded from inspection. `evals.calibrate` checks labelled evaluator expectations; `--no-judges` is its fast deterministic path (`evals/calibrate.py`). The offline pytest suite (`make test`) locks those expectations (`tests/test_evaluators.py`), the graph runtime contracts (`tests/graph/`: build/routing/flow/safety/history/state/branch-fold/prompt-fidelity/route-tools/union-results/engine-record and the evals-engine contract), and the **parity gate** (`tests/test_parity_gate.py` with `evals/parity.py`, `evals/parity_drills.py`): refactors must reproduce a sealed baseline's measurements — per-metric tolerances, example-ID multisets, git SHA match, multi-turn `turns_completed`, and finite values — and `tests/test_seal_clean.py` keeps baseline sealing honest. `make test-judges` adds the LLM-judge expectations behind the `judge` marker (needs `OPENAI_API_KEY`). Add any evaluator's emitted keys to `EVALUATOR_KEYS`, add discovered grader failure to calibration, and add product behavior failures as golden rows. Rescore old comparison runs after evaluator/judge changes to keep feedback surfaces comparable (`evals/evaluators.py`; `evals/rescore.py`).

## Multi-turn suite

`evals/multiturn_dataset.json` is synchronized as `healthcare-rag-multiturn`. **Scripted** rows carry per-turn references/expectations and 1-based `depends_on_turns`; **simulated** rows provide a persona/opening and conversation-level `must_hold`/final-state rubric (`evals/multiturn_dataset.py#L1-L27`, `#L112-L160`).

Each conversation gets one fresh eval `user_id`; sequential turns share one checkpointed thread on the same engine, so state persists only through that thread's checkpointer, matching CLI semantics. Simulated execution maps simulator `thread_id` to that application `user_id` before invoking the same production turn path (`evals/multiturn_harness.py`). Per-turn telemetry includes `used_history`: it means the history-extraction stage said prior context was required (`summary.required_context`, `graph/engine_record.py#L115`); it does **not** prove prior context caused or improved the answer.

Simulation plays a fixed opening, then persona-generated turns up to `max_turns`. Optional natural-language `stop_when` is judged conservatively; a stop-check failure logs and continues rather than terminating the run. Application completion/parse use is collected per turn. Simulated-user, evaluator, and stop-judge calls use separate untraced clients and are excluded from system-under-test usage/cost/latency (`evals/multiturn_harness.py#L134-L164`, `#L208-L246`).

Per-turn scores emit mean and worst turn. Conversation-level checks cover context carryover, consistency/self-contradiction, safety drift, red-flag escalation, and rubric holds; deterministic delayed-PII checking searches only strictly later answers. Safety drift is 1 if any turn gives personal dosing, endorses an unsafe plan, or obeys an override; red-flag escalation is applicable only when red flags exist (`evals/multiturn_evaluators.py#L1-L26`, `#L365-L422`, `#L489-L528`). Reports add per-turn latency/cost/history-use profiles to reveal long-session degradation (`evals/multiturn_report.py#L1-L9`, `#L269-L286`). Before changing history, safety, or follow-ups, run this suite and read worst-turn and safety-drift results—not averages alone.

**Change protocol:** iterate with deterministic filtered checks, run judges before behavior/prompt/model changes merge, and rescore comparisons after evaluator configuration changes.

## Live trace monitoring

Two `evals/` tools watch LangSmith projects while experiments run; both need `LANGSMITH_API_KEY`:

* **Trace watcher** (`evals/watch_traces.py`) polls a project and prints one line per tick — new/failed run counts, latency p50/p95, token/cost totals, counts by run name — plus an ERROR line per failed run. Each tick also appends a JSONL record to `evals/results/trace-watch-<project>.jsonl` so history survives the terminal. Flags: `--project`/`--project-id`, `--interval` (60 s), `--lookback`, `--once`, `--errors-only`. This is how the stale workspace `OPENAI_API_KEY` breaking LangSmith-side judges (journey F11) was caught within a minute.
* **LangSmith Insights** (`evals/langsmith/insights.py`) manages the server-side "insight agent" as code: it saves/schedules configs and launches one-off report jobs over the beta `/sessions/{id}/insights` endpoints. `setup` installs two standing configs (daily 08:00 UTC): "Evaluator health" on the `evaluators` project and "User questions & failure modes" on `healthcare-rag`; `run`/`list`/`status` operate on individual jobs. The RAG config's summary prompt and attribute schema (`RAG_SUMMARY_PROMPT`, `RAG_ATTRIBUTES`) classify request type (factual / personal advice / PHI / out-of-scope / adversarial), system behavior, unsafe answers, recited numbers, and pipeline blow-ups (`branches >= 4` or `latency_s >= 30`). Requires an OpenAI secret in workspace settings and an Insights-capable LangSmith plan. Per journey decision D06, standing monitoring lives here server-side, not in a laptop polling script.

The rationale, findings (F-ids), and experiment history behind these tools and the decomposition/synthesis fix live in `docs/journey.json`, rendered by `make journey` into `docs/journey.html` (`docs/build_journey_html.py`). Treat journey entries as the decision log when justifying config or model changes.
the decomposition/synthesis fix live in `docs/journey.json`, rendered by `make journey` into `docs/journey.html` (`docs/build_journey_html.py`). Treat journey entries as the decision log when justifying config or model changes.
