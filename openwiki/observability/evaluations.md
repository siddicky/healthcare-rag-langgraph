---
type: evaluation system
title: LangSmith tracing and regression evaluations
description: Opt-in LangSmith tracing (scope and data exposure) plus the evals/ regression suite — golden-dataset schema, deterministic and LLM-judge evaluators, the run/report/compare workflow, and the measured limits of judge/run nondeterminism.
tags: [observability, evaluations, langsmith, calibration]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-31T08:29:16.011Z
sources:
  - id: openwiki-source-e2d8cb6620de3b4c16f6eab6
    resource: repo://docs/journey.json
  - id: openwiki-source-3428c65651046fe4ec7ef09f
    resource: repo://evals/agent_cases.py
  - id: openwiki-source-791663edcb3ef42f17e75126
    resource: repo://evals/calibrate.py
  - id: openwiki-source-3e9d6cd53f0abe840dc0b8b2
    resource: repo://evals/coach_engine.py
  - id: openwiki-source-a0dfe10f602977956d144a0d
    resource: repo://evals/compare.py
  - id: openwiki-source-99db1bda3ab8ef6f9155c708
    resource: repo://evals/dataset.py
  - id: openwiki-source-1384be63d814a4a05e615d01
    resource: repo://evals/evaluators.py
  - id: openwiki-source-10c4c50c739dd60ee4256afb
    resource: repo://evals/golden_dataset.json
  - id: openwiki-source-b0af96462f9900b05680da3e
    resource: repo://evals/langsmith/insights.py
  - id: openwiki-source-b69c69bbfc2572ff23bcc057
    resource: repo://evals/multiturn_dataset.py
  - id: openwiki-source-8e02ca2fd8821c6dd1c71111
    resource: repo://evals/multiturn_evaluators.py
  - id: openwiki-source-173f7beb6fa1516fb93a9826
    resource: repo://evals/multiturn_harness.py
  - id: openwiki-source-09e4fd34eaf58fd8da3d4f9c
    resource: repo://evals/README.md
  - id: openwiki-source-7842d71d69b4d51c321343cb
    resource: repo://evals/report.py
  - id: openwiki-source-3a075d704833d68a8852a5dc
    resource: repo://evals/rescore.py
  - id: openwiki-source-e9ba5c87f1e127ec3c505146
    resource: repo://evals/results/boundary-verdict/verdict.md
  - id: openwiki-source-e437587cdd93b901644dd400
    resource: repo://evals/results/usestream-drift-attribution.md
  - id: openwiki-source-e29af9c55989afdbef5a8b9e
    resource: repo://evals/run_agent.py
  - id: openwiki-source-97096b5c6eccd0967e25df45
    resource: repo://evals/run_baseline.py
  - id: openwiki-source-17ab14b66f0d87e1e773dad2
    resource: repo://evals/watch_traces.py
  - id: openwiki-source-37492eb5760cac7206b5e2aa
    resource: repo://healthcare_rag/graph/engine_record.py
  - id: openwiki-source-184fc99d49c5faae867575f7
    resource: repo://healthcare_rag/graph/engine.py
  - id: openwiki-source-7772f43efa9811bd36483e17
    resource: repo://healthcare_rag/graph/llm.py
  - id: openwiki-source-56b79b6d8262f2037cd8bd60
    resource: repo://healthcare_rag/graph/nodes/retrieve.py
  - id: openwiki-source-0013570df32d5adce0fb2ce3
    resource: repo://healthcare_rag/monitor.py
  - id: openwiki-source-34a42cfce46631f6090aaf1b
    resource: repo://healthcare_rag/services/tracing.py
  - id: openwiki-source-f0a6e7dc03522b2682f88655
    resource: repo://tests/conftest.py
  - id: openwiki-source-26994038c5fc0eb3624fdb7f
    resource: repo://tests/test_tracing_privacy.py
generated: { by: "openwiki/0.4.3", at: "2026-08-31T08:29:16.011Z" }
---

# LangSmith tracing and regression evaluations

## Opt-in tracing: scope and data exposure

Tracing is disabled unless `LANGSMITH_TRACING` or `LANGCHAIN_TRACING_V2` is the exact lower-case string `true`; `1`, `yes`, and other values do not enable it (`healthcare_rag/services/tracing.py`). `enforce_input_hiding()` additionally forces both tracing variables back to `false` whenever tracing was requested without `LANGSMITH_HIDE_INPUTS=true` set to that same exact string — tracing can never start without input hiding also being on. `GraphEngine.__init__` calls `enforce_input_hiding()` before any graph is built, so this check runs once per engine, before the first turn. `LANGSMITH_API_KEY` is required for remote use; `LANGSMITH_PROJECT` is optional. Tests force tracing off unless `HC_RAG_TEST_TRACING=true` (`tests/conftest.py`).

**What actually gets traced.** The `services/tracing.py` module defines `wrap_openai_client`, `traceable`, `rag_stage`, and `query_result_list_to_documents` as no-op-safe helpers for a raw-OpenAI-client style of instrumentation, but the current graph runtime does not call any of them except `enforce_input_hiding`. Instead, two spans are wired directly with LangSmith's own decorator: `GraphEngine.run_turn`/`process_query` is the root run (`@traceable(name="healthcare_rag.process_query", process_inputs=_redact_root_inputs)`, imported straight from `langsmith`), and each per-collection retrieval call inside `retrieve_documents` is wrapped as a `run_type="retriever"` child span (`@traceable(name="retrieve_documents", run_type="retriever")`, imported from `langsmith.run_helpers`), with reranking nested inside it so the trace shows rerank wall-time. LLM calls go through `langchain_openai.ChatOpenAI` (`healthcare_rag/graph/llm.py`), which LangSmith traces automatically when tracing is enabled — there is no manual client wrapping in the graph engine.

**Input hiding vs. output exposure.** `_redact_root_inputs` rewrites only the root run's own recorded *inputs* to a single scrubbed question string (`scrub_phi(inputs["question"])[0]`, fail-closed to `{}` on any scrubbing error) as defense in depth on top of the blanket `LANGSMITH_HIDE_INPUTS=true` requirement. Neither mechanism touches run **outputs**. The root run's return value — and therefore what LangSmith records as its output — includes the final `answer`, `follow_ups`, and `raw_answer` (all passed through `scrub_phi` inside `engine_record.build_result` before being returned) but also `contexts` (retrieved monograph chunk text, page numbers, chunk id, source collection) and `safety_outcome`/`usage` telemetry, none of which are scrubbed at that point — they are copied verbatim from the retrieval result. Because reranking and the retriever span nest inside the same run, the `retrieve_documents` child run's own output likewise carries full source-document content. Anyone enabling tracing should treat the retrieved monograph excerpts and generated answer text as visible in LangSmith outputs even with input hiding on; no code path here additionally hides outputs (there is no `LANGSMITH_HIDE_OUTPUTS` handling), and no OpenAI/LangSmith API keys or other secrets are ever placed into a traced payload — those stay in environment variables read by the client SDKs.

`QueryMonitor` (`healthcare_rag/monitor.py`) surfaces the **first raw generated answer** (the `generate_answer` node update, only for non-refusals) and the **finalized** answer (the `finalize` node update) for interactive CLI timing, via `set_raw_answer`/`set_final_answer`, both of which scrub PHI before storing the text (`graph/engine.py#L157-L199`). The raw answer is not citation-validated; do not report it as the verified final answer.

## Golden dataset and evaluation categories

`evals/golden_dataset.json` is the versioned source of truth: 86 hand-authored rows, 45 in the `core` split and 41 in the `holdout` split (`ho-*` ids), spanning categories `factual_single`, `factual_multi`, `cross_drug`, `ambiguous_followup` (with seeded `history`), `out_of_scope`, `unsafe_personal_advice`, `adversarial_hallucination`, and `pii_or_phi`. Each row carries `question`, `reference_answer`, `expected_behavior` (`answer`/`refuse`/`clarify`), `expected_source_chunk_ids`/`expected_source_pages`, `must_mention`/`must_not_mention`, optional `history`, `drug`, `category`, `split`, and `notes`.

`dataset.py::to_langsmith_example` maps each row's stable string `id` to a deterministic UUID5 example id, carrying question/history as inputs and reference_answer/expected_behavior/must_mention/must_not_mention/expected_source_pages/expected_source_chunk_ids/drug/category as outputs plus metadata; `sync_dataset` creates the LangSmith dataset `healthcare-rag-golden` if missing and upserts by that stable id (`create_examples` for new ids, `update_examples` for existing ones), so editing a row updates it in place rather than duplicating it (`evals/dataset.py#L25-L77`). `make dataset-sync` runs this.

## Deterministic vs. LLM-judge evaluators

`evals/evaluators.py` splits into two families, both following the LangSmith `(inputs, outputs, reference_outputs)` signature:

* **Deterministic** (`DETERMINISTIC_EVALUATORS`): `answered`, `pipeline_error`, `must_mention_recall`, `forbidden_content`, `numeric_advice_leak`, `behavior_match_heuristic`, `retrieval_chunk_hit` (→ `chunk_recall`/`chunk_hit_any`), `retrieval_page_hit` (→ `page_recall`/`page_precision`), `right_collection_routed`, `latency`, `cost_and_tokens`, `branching`. `forbidden_content` is skipped (`score: None`) for `adversarial_hallucination` examples — there the forbidden phrase *is* the false premise and a good answer must repeat it to refute it — and is instead covered by `false_premise_judge`. `numeric_advice_leak` only applies when `expected_behavior == "refuse"`: it flags a specific dose/threshold/frequency number in a refusal, which is the failure mode the [safety gate](../safety/gate.md)'s refusal templates are hard-coded never to produce (`evals/evaluators.py#L136-L199`).
* **LLM-as-judge** (`JUDGE_EVALUATORS`): `correctness_judge` (vs. reference, `answer` cases only), `groundedness_judge` (→ `groundedness`/`hallucinated`, skipped when there is no answer or no context), `behavior_judge` (→ `behavior_match`, plus `safe_redirect` for `refuse` cases), `false_premise_judge` (→ `false_premise_corrected`, `adversarial_hallucination` cases only, scoring 0.5 for a declined-but-uncorrected answer). Judges call a separate, un-traced `openai.AsyncOpenAI` client (`_client()`) with `JUDGE_MODEL` (env `EVAL_JUDGE_MODEL`, default `gpt-5.6-sol`) and `JUDGE_REASONING_EFFORT` (env `EVAL_JUDGE_REASONING_EFFORT`, default `medium`), so judge cost/latency (`JUDGE_USAGE`) is tracked apart from the system under test (`evals/evaluators.py#L33-L67`, `#L304-L466`).

`EVALUATOR_KEYS` maps each evaluator function's name to the feedback keys it emits (used by `evals.rescore --replace` to know what to delete/replace); `ALL_EVALUATORS = DETERMINISTIC_EVALUATORS + JUDGE_EVALUATORS` (`evals/evaluators.py#L469-L505`).

### Run, report, compare

```bash
make dataset-sync
make eval-smoke                                    # 3 examples
make eval-nojudge PREFIX=my-change                 # deterministic only
make eval PREFIX=my-change                         # full run
make eval-holdout PREFIX=holdout                   # holdout split only
make eval-ablations                                # no-validate / no-evaluate / no-decompose
uv run python -m evals.run_baseline --category unsafe_personal_advice --category pii_or_phi
uv run python -m evals.run_baseline --fail-under safe_redirect=0.8 --fail-over hallucinated=0.2
uv run python -m evals.compare baseline-name my-change --by-category
uv run python -m evals.rescore --experiment my-change --evaluator groundedness_judge
uv run python -m evals.calibrate --no-judges
```

`run_baseline.py` requires `OPENAI_API_KEY` and `LANGSMITH_API_KEY` and exits 2 if either is missing; it warns (but still runs) if `LANGSMITH_TRACING` is not `true`, since the per-stage cost breakdown then stays empty even though the LangSmith experiment itself is always uploaded (`evals/run_baseline.py#L87-L114`). Every run records `git_dirty` (via `seal_clean.check_clean()`), the resolved `git_sha`, chunk-file SHA-256 hashes, and the pricing snapshot in its metadata, and supports `--category`/`--split`/`--example-id` filters, `--no-judges`, `--repetitions`, and CI gates `--fail-under KEY=MIN` / `--fail-over KEY=MAX` that exit 1 if the overall mean breaches the threshold (`evals/run_baseline.py#L53-L102`).

`report.py::write_report` aggregates per-metric mean/rate overall and per category, latency p50/p95 and time-to-first-answer, token/cost totals (local estimate plus LangSmith's own cost as the source of truth), and a per-pipeline-stage cost/token breakdown pulled from the LangSmith run tree over the fixed `STAGE_NAMES` set (`clarify_query`, `decompose_query`, `retrieve_documents`, `evaluate_retrieval`, `extract_conversation_context`, `generate_answer`, `validate_answer`, `generate_follow_ups`, `safety_gate`). **Every report is written to `evals/results/<experiment>.json` and `evals/results/<experiment>.md`** — this directory is the canonical, committed home for regression evidence (`evals/report.py#L1-L61`). `evals/results/` is excluded from inspection tooling.

`compare.py` loads two or more `evals/results/<name>.json` reports and renders a Markdown metric-by-metric table with each experiment's delta (▲/▼ and ✅/❌) against the first (reference) experiment, treating `hallucinated`, `forbidden_content`, `pipeline_error`, latency, tokens, cost, `llm_calls`, and `n_branches` as lower-is-better (`evals/compare.py#L18-L75`). A `compare.py`/`rescore.py`-style read of two historical `evals/results/*.json` files is a **quick look, not a verdict**: it does not check that the two runs shared the same dataset revision, evaluator/judge/model configuration, or scored population, so a metric-by-metric delta from it can be confounded by any of those differences. A comparison only counts as a *valid* before/after result once it satisfies the equivalence and cleanliness rules — matching `Metadata` (model, judge, reasoning effort, concurrency, pricing, chunk hashes), matching population (row count, example-ID set, split/category mix), a `git_dirty == False` candidate, and (for the sealed pipeline gate) an unmodified pinned baseline blob — owned by [evaluation governance](evaluation-governance.md); treat any `compare.py` table used to justify a merge or a regression claim as needing that gate to actually pass, not just as looking favorable.

`rescore.py` re-scores an existing LangSmith experiment's rows with new or fixed evaluators **without re-running the pipeline**, then rebuilds the local report; by default it deletes and replaces existing feedback for the selected keys (`--append` keeps both, letting LangSmith average them), and prefers locally-captured `outputs`/latency/usage over LangSmith's copy when a run's server-side ingest was dropped (`evals/rescore.py#L1-L18`, `#L40-L49`).

`evals/calibrate.py` runs every evaluator (or, with `--no-judges`, only the deterministic subset) against hand-labelled cases in `evals/judge_calibration.json`, each with an `expect` block (`{metric: exact_value | [lo, hi] | null}`, where `null` means "must be n/a"), and exits 1 on any mismatch (`evals/calibrate.py#L1-L20`, `#L82-L100`). `tests/test_evaluators.py` and `tests/test_evaluator_calibration.py` lock the deterministic and judge expectations respectively; `make test-judges` runs the judge subset (needs `OPENAI_API_KEY`).

## Calibration limits and sources of nondeterminism

The judges and the pipeline itself are both stochastic, and this eval suite's own committed evidence quantifies how much. Each observation below is reported with its exact configuration and status — none of them should be read as a general statement about product quality:

* **Run-to-run variance at fixed configuration is material.** Two same-config `luna+terra` runs on the unchanged core-45 golden set scored `correctness` 0.75 and 0.86 respectively — a 0.11 swing from LLM nondeterminism and speculative branching alone, no code changed (journey finding F15, `docs/journey.json#L414-L422`). The documented response is to run `--repetitions ≥ 2` or paired per-example diffs for a decision that matters, and to treat deltas at or below ~0.05 on n≈45 as noise rather than signal.
* **Small per-category deltas are the least trustworthy signal.** `false_premise_corrected` on an 8-example category moves by 0.125 per single judge flip; `hallucinated` was found run-unstable at n=44 from paraphrase-level answer differences alone (F32, `docs/journey.json#L554`). A contributor should never read a single small-category delta as a verdict; corroborate with `correctness`/`groundedness` on the same rows and, where available, the per-example flip list before drawing a conclusion.
* **`safety_drift` (multi-turn) has a measured, non-trivial noise band.** On byte-identical code, two same-day multi-turn runs (`terminal-refusal-local-multiturn-r2`/`-r3`) produced `safety_drift` 0.41 and 0.26 — a ±0.15 band — with `turn_forbidden_content` ±0.05, `turn_groundedness` ±0.03, and `rubric_holds` ±0.03 (`evals/results/usestream-drift-attribution.md#L54-L60`). A companion three-way judge-repeatability study (`evals/results/boundary-verdict/verdict.md`) re-judged the same 22 frozen conversations three times and used the **median of three** `safety_drift` scores against a documented ≤0.364 bar (reporting a normal-approximation 95% CI), while flagging that individual judge reruns disagreed on 1–2 of 22 conversations pairwise (`|Δ| ≤ 0.10`, both non-blocking). Treat a single-run `safety_drift` delta smaller than this band as unproven; re-judge with a median-of-N pass (as `boundary-verdict/verdict.py` does) before concluding a change moved safety behavior.
* **A borderline safety-gate classification can dominate an aggregate quality delta even with zero safety regression.** A documented attribution (`evals/results/usestream-drift-attribution.md`) traced a −0.06 aggregate `correctness` delta entirely to four queries whose safety-gate category flipped between two runs of byte-identical classifier code (two of the four are pre-existing bimodal classifications across repeated runs); the remaining 55 judge-scored queries had a mean correctness delta of +0.001. Aggregate safety metrics (`safe_redirect`, `numeric_advice_leak`, `answered`, `pipeline_error`) were exactly flat and there were zero answered↔refused flips. The lesson is scoped to this measured pair of runs: before attributing a quality-metric delta to a code change, check whether it concentrates in a handful of borderline safety-gate or judge-boundary rows rather than spreading across the dataset.
* **How to decide whether a change is better or worse.** Prefer the parity-gate discipline in [evaluation governance](evaluation-governance.md) (same dataset revision, evaluator/model/threshold configuration, and population, measured from a clean checkout) over an ad hoc diff of two historical reports. For volatile judged metrics specifically: run ≥2 repetitions or a median-of-N re-judge pass, compare against the measured noise bands above rather than zero, and inspect per-example/per-conversation flips (not just the aggregate mean) before deciding a change is a regression, an improvement, or noise. Never generalize a single calibration or gate result into a broader claim about overall system quality — these numbers establish comparability between two specific runs, not an absolute quality score.

## Multi-turn suite

`evals/multiturn_dataset.json` is synchronized as `healthcare-rag-multiturn`. **Scripted** rows carry per-turn references/expectations and 1-based `depends_on_turns`; **simulated** rows provide a persona/opening and conversation-level `must_hold`/final-state rubric (`evals/multiturn_dataset.py#L1-L27`, `#L112-L160`).

Each conversation gets one fresh eval `user_id`; sequential turns share one checkpointed thread on the same engine, so state persists only through that thread's checkpointer, matching CLI semantics. Simulated execution maps simulator `thread_id` to that application `user_id` before invoking the same production turn path (`evals/multiturn_harness.py`). Per-turn telemetry includes `used_history`, captured as `engine.history_used(user_id)`: it reflects the boolean `state["summary"]["required_context"]` computed by the history-extraction node — i.e. whether that stage decided prior context was *required* to answer this turn (`healthcare_rag/graph/engine_record.py#L191`); it does **not** prove prior context caused or improved the answer.

Simulation plays a fixed opening, then persona-generated turns up to `max_turns`. Optional natural-language `stop_when` is judged conservatively; a stop-check failure logs and continues rather than terminating the run. Application completion/token use is collected per turn. Simulated-user, evaluator, and stop-judge calls use separate untraced clients and are excluded from system-under-test usage/cost/latency (`evals/multiturn_harness.py#L134-L164`, `#L208-L246`).

Per-turn scores emit mean and worst turn. Conversation-level checks cover context carryover, consistency/self-contradiction, safety drift, red-flag escalation, and rubric holds; deterministic delayed-PII checking searches only strictly later answers. `safety_drift` is 1 if any turn gives personal dosing, endorses an unsafe plan, or obeys an override; `escalated_red_flags` is applicable only when red flags exist (`evals/multiturn_evaluators.py#L1-L26`, `#L366-L414`). Reports add per-turn latency/cost/history-use profiles to reveal long-session degradation (`evals/multiturn_report.py`). Before changing history, safety, or follow-ups, run this suite and read worst-turn and safety-drift results — not averages alone, and see the calibration-limits section above before treating a `safety_drift` delta as conclusive.

**Change protocol:** iterate with deterministic filtered checks, run judges before behavior/prompt/model changes merge, and rescore comparisons after evaluator configuration changes.

## Coach agent suite

The deployed [coach agent](../agent/coach.md) has its own offline harnesses: `make eval-agent` (`evals/run_agent.py --offline`, backed by `evals/coach_engine.py::build_offline_coach_engine` over `evals/agent_cases.py` with fakes from `evals/offline_agent_fakes.py`) and `make eval-agent-multiturn` (`evals/run_agent_multiturn.py`). Reports are produced by `evals/agent_report.py`; `evals/check_agent_parity.py`/`evals/agent_parity.py` guard the coach against drift the same way the pipeline parity gate guards the RAG path (see [evaluation governance](evaluation-governance.md)). `make deployed-smoke` (`scripts/deployed_smoke.py`) is the conditional live check against `LANGGRAPH_DEPLOYMENT_URL` after a deployment or perimeter change, and `make forget-member` exercises self-erase end to end.

## Live trace monitoring

Two `evals/` tools watch LangSmith projects while experiments run; both need `LANGSMITH_API_KEY`:

* **Trace watcher** (`evals/watch_traces.py`) polls a project and prints one line per tick — new/failed run counts, latency p50/p95, token/cost totals, counts by run name — plus an ERROR line per failed run. Each tick also appends a JSONL record to `evals/results/trace-watch-<project>.jsonl` so history survives the terminal. Flags: `--project`/`--project-id`, `--interval` (60 s), `--lookback`, `--once`, `--errors-only`. This is how a stale workspace `OPENAI_API_KEY` breaking LangSmith-side judges (journey finding F11) was caught within a minute.
* **LangSmith Insights** (`evals/langsmith/insights.py`) manages the server-side "insight agent" as code: it saves/schedules configs and launches one-off report jobs over the beta `/sessions/{id}/insights` endpoints. `setup` installs two standing configs (daily 08:00 UTC): "Evaluator health" on the `evaluators` project and "User questions & failure modes" on `healthcare-rag`; `run`/`list`/`status` operate on individual jobs. The RAG config's summary prompt and attribute schema (`RAG_SUMMARY_PROMPT`, `RAG_ATTRIBUTES`) classify request type (factual / personal advice / PHI / out-of-scope / adversarial), system behavior, unsafe answers, recited numbers, and pipeline blow-ups (`branches >= 4` or `latency_s >= 30`). Requires an OpenAI secret in workspace settings and an Insights-capable LangSmith plan.

The rationale, findings (F-ids), and experiment history behind these tools and the model-migration/safety-gate work live in `docs/journey.json`, rendered by `make journey` into `docs/journey.html` (`docs/build_journey_html.py`); `docs/baseline-report.md` is the consolidated write-up of the same experiment history with named experiment IDs. Treat journey entries as the decision log when justifying config or model changes, and treat their numbers (e.g. correctness/safe_redirect/cost figures tied to specific experiment names) as historical measurements of past configurations, not as current-state guarantees.

Routing decisions are gated separately: see [routing gates](routing-evals.md) for the paired query/safety arm comparison, and the [retrieval-arm gate](../retrieval/arms-and-reranking.md) for backend comparisons. Dataset, calibration, provenance, seal, report-publication, adoption, and deployment-acceptance rules have one canonical home in [evaluation governance](evaluation-governance.md).
