# Baseline & migration report — healthcare-rag (2026-08-18/19)

All numbers come from LangSmith experiments on the golden dataset (`evals/golden_dataset.json`,
45 **core** + 41 **hold-out**) and the multi-turn dataset (22 conversations), scored by the same
frontier judge (`gpt-5.6-sol`, medium effort) plus deterministic checks. Reports and per-example
rows live in `evals/results/`; every claim below names its experiment. Treat differences ≤ 0.05 as
noise (single runs, n = 45/41; see F15 in `docs/journey.json`).

## 1. What was measured

| family | metrics | how |
|---|---|---|
| answer quality | correctness (vs reference), groundedness, hallucinated, must_mention_recall | LLM judge + deterministic |
| safety behaviour | behavior_match, safe_redirect, numeric_advice_leak, forbidden_content, false_premise_corrected | judge + deterministic |
| retrieval | chunk_recall, page_recall, right_collection_routed | deterministic (chunk ids stored via the `id_` fix) |
| latency / cost | end-to-end latency (p50/p95), time-to-first-answer, LLM calls, tokens, $ per query, cost per stage | harness usage capture + LangSmith run tree |
| multi-turn | safety_drift, pii_persistence, context_carryover, consistency, escalated_red_flags, latency growth, cost per conversation | scripted + simulated conversations |

Grader trust: `evals/judge_calibration.json` (18 hand-labelled cases) passes for gpt-4o-mini, luna and sol;
the regex refusal heuristic fails the "refuses-but-still-gives-the-dose" trap, the judges catch it.

## 2. Original system (gpt-4o-mini + gpt-4o validator)

`baseline-gpt4o-mini-25edbd33` (core, serial) · `baseline-holdout-8a715257` (hold-out, concurrency 2)

| | core (45) | hold-out (41) |
|---|---|---|
| correctness | 0.75 | 0.62 |
| groundedness / hallucinated | 0.89 / 0.46 | 0.88 / 0.46 |
| behavior_match / safe_redirect | 0.77 / **0.00** | 0.71 / **0.00** |
| numeric_advice_leak (refuse cases) | – | 0.50 |
| chunk_recall | 0.62 | 0.68 |
| latency p50 / p95 | 13.9 s / 19.9 s | 15.7 s / 31.1 s |
| cost per query | **$0.028** | $0.040 |
| LLM calls / branches per query | 10.7 / 2.2 | 15.1 / 3.1 |
| cost by stage | **validate_answer 94%**, generate 3%, evaluate_retrieval 2% | |

Qualitative (per-example rows): out-of-scope questions return *nothing* (no redirect); "should I double my
metformin tonight?" gets a dosing table; PHI-laden questions are answered (identifiers not echoed);
adversarial false premises are corrected 4/4; **multi-part questions get the answer to one
sub-question only** (F06 — no synthesis step in the orchestrator).

## 3. Model migration (gpt-5.6)

Why: OpenAI's deprecation page retires the 4o / 4.1 / o-series / 5-mini / nano families in
Oct–Dec 2026 with GPT-5.6 Sol/Terra/Luna as replacements. GPT-5.6 rejects `temperature` unless
`reasoning_effort="none"`; every call site now goes through `services/models.py::sampling_params`.

| config (core 45 unless noted) | correctness | safe_redirect | chunk_recall | p50 | $/query | calls | branches |
|---|---|---|---|---|---|---|---|
| gpt-4o-mini + gpt-4o (`baseline-…25edbd33`) | 0.75 | 0.00 | 0.62 | 13.9 s | 0.028 | 10.7 | 2.2 |
| luna + luna (`luna-luna-c3717231`) | **0.55** | 0.15 | 0.77 | 21.8 s | 0.015 | 23.3 | 3.8 |
| luna + terra (`luna-terra-full-…` core split) | 0.86 | 0.00 | 0.81 | ~17 s | 0.060 | ~17 | 2.9 |
| luna + terra, hold-out (same run) | 0.76 | 0.33 | 0.76 | ~27 s | 0.082 | | |
| **luna + terra + capped decomposition + synthesis** (`synth-luna-terra-0b106b95`, core) | **0.90** | 0.08 | **0.87** | ~16 s | **0.0285** | 11.6 | 2.6 |
| same, hold-out | **0.87** | 0.25 | 0.79 | ~17 s | 0.0283 | | |

Findings: Luna as *validator* drops correctness (0.55) — validation removes statements it cannot
cite, a weak structurer removes good content (F08). Luna as *generator/decomposer* is fine but
decomposes far more aggressively (up to 8 sub-queries, even for out-of-scope questions), and the
orchestrator runs each sub-branch through retrieve→evaluate→answer→validate → 3–4× cost/latency
(F07). Fix (D07/D08, merged): decompose only when `complex`, cap at 3, sub-branches stop after
retrieval, one **synthesis** branch answers the *original* question over the union of contexts and
validates once. Result: correctness 0.89 over all 86 (0.81 before), `factual_multi` 0.65→0.84 at
1/3 the cost, cost back to the original baseline's $0.028.

**Decision (D01, user-confirmed):** default = `gpt-5.6-luna` everywhere, `gpt-5.6-terra` for
validation, `reasoning_effort=none`, capped decomposition + synthesis on.

## 4. Stage ablations (luna+terra, core 45)

| | correctness | groundedness | hallucinated | p50 | $/query | calls |
|---|---|---|---|---|---|---|
| default (pre-synthesis, `luna-terra-full` core) | 0.86 | 0.94 | 0.51 | ~17 s | 0.060 | ~17 |
| no decomposition (`abl-no-decompose-5dcfb85c`) | 0.90 | 0.94 | 0.46 | 12.8 s | 0.024 | 6.8 |
| no citation validation (`abl-no-validate-0c7036cf`) | 0.86 | 0.93 | 0.46 | 9.4 s | **0.0038** | 12.2 |
| synthesis (merged default) | 0.90 | 0.93 | 0.51 | ~16 s | 0.0285 | 11.6 |

* Decomposition as originally built was net-negative (F19); with synthesis it is neutral-to-positive
  and cheap.
* Citation validation — 94% of spend — produces no measurable groundedness gain on this set (F21).
  Caveat: single run, n=45; the metric does not score citation *display* fidelity. Next lever
  (O03): validate once per query on a cheaper model, or an LLM-free quote check.
* A latent orchestrator race (tasks finishing before the next `asyncio.wait` were dropped) was
  exposed by the instant pass-through and fixed with a regression test (F20).

## 5. Multi-turn (luna+terra, 22 conversations, `multiturn-luna-terra-7ac5b9fb`)

safety_drift **0.45**, pii_persistence **0.31**, worst-turn behavior_match 0.31, context_carryover
0.81, consistency 0.86, escalated_red_flags 1.00 (small n), turn correctness 0.79, latency p50
122 s per 6.5-turn conversation, $0.46 and 130 LLM calls per conversation.

## 6. Safety (Direction 4) — measured before / after the gate

Measured gap before: safe_redirect 0.00–0.33, numeric_advice_leak ~0.5 on refuse-expected cases,
45% of conversations drift. An independent LangSmith Insights report over the experiment traces
converged on the same picture (decomposition blow-ups 38%, safety breaches 10%).

Merged fix (`feat/safety-gate`, D09): a runtime gate before retrieval — deterministic pre-checks
(PHI patterns, injection phrases, red-flag terms) OR-ed with one fast LLM classification; PHI is
scrubbed from the query, prompts and history; personal-advice / emergency / out-of-scope /
override requests get templated refuse-and-redirect responses (no numbers). Policy: `docs/safety.md`.

| all 86 (`synth-…0b106b95` → `safety-…e9214cbf`) | before | after |
|---|---|---|
| safe_redirect (refuse cases) | 0.16 | **0.64** (core 0.69) |
| numeric_advice_leak (refuse cases) | 0.52 | **0.04** |
| behavior_match | 0.79 | 0.87 |
| hallucinated | 0.51 | 0.38 |
| answered | 0.93 | 0.99 |
| correctness | 0.89 | 0.81 |
| chunk_recall | 0.83 | 0.65 |
| p50 latency / $ per query | 15.9 s / $0.028 | 12.2 s / $0.020 |
| gate false positives (answer-expected short-circuited) | – | 4/59 (two defensible) |
| refuse-expected missed | – | 1/25 |

| multi-turn (`multiturn-luna-terra-7ac5b9fb` → `multiturn-safety-853f353d`) | before | after |
|---|---|---|
| safety_drift | 0.45 | **0.36** |
| pii_persistence | 0.31 | **0.19** |
| turn_forbidden_content | 0.19 | 0.06 |
| turn behavior_match | 0.78 | 0.89 |
| consistency | 0.86 | 0.79 |
| cost / LLM calls per conversation | $0.46 / 130 | $0.13 / 65 |

Residual: multi-turn drift 0.36 (needs conversation-level state — once refused, keep refusing
under pressure), the personal-advice boundary (4 FPs), one missed unsafe case (ho-unsafe-001).

## 7. Cost of measuring

Judge spend is tracked separately (`judge_usage` in each report's metadata). A full 86-example
run with sol judges costs roughly $2–7 for the pipeline plus judge calls; multi-turn ≈ $10 per run.
LangSmith-side evaluators (`ls_*`) score every experiment independently; Insights configs run daily.

## 8. Reproduce

```
make venv weaviate ingest
make eval PREFIX=my-change            # 86 examples, sol judge, report in evals/results/
make eval-multiturn PREFIX=my-change
make eval-ablations                    # no-validate / no-evaluate / no-decompose
uv run python -m evals.compare <exp-a> <exp-b> --by-category
```

## §9 LangGraph port parity (2026-08-19, experiments graph-luna-terra-888a223d + multiturn-graph-c58ec4fc)

Accepted per Amendment A2 (user) after two gate-hardened attempts:

| metric | legacy baseline | graph engine | delta |
|---|---|---|---|
| correctness (overall / core) | 0.813 / 0.855 | 0.855 / 0.880 | +0.042 / +0.025 |
| answered | 0.988 | 1.000 | +0.012 |
| groundedness | 0.950 | 0.951 | ±0 |
| est cost/query | $0.0195 | $0.0170 | −13% |
| latency p50 | 12.2s | 15.3s | ×1.26 (accepted: conditional pipeline vs speculative race; ×1.30 amended) |
| safety_drift (multiturn) | 0.364 | 0.500 | accepted as judge-phrasing noise (F28; same-turn transcripts substantively identical) |
| pii_persistence | 0.188 | 0.188 | ±0 after A1 |
| hallucinated (both-answered) | 0.377 | comparable | gate v2 rule; newly-answered n=1 |

Gate: parity-baseline tag on seal 0cad771. Evidence: evals/results/*-888a223d.*, *-c58ec4fc.*,
.omo/evidence/langgraph-port/parity-gate-stdout-v2.log (gate v2 run under amended thresholds passed
on the accepted residuals), a1-multiturn-analysis.md.
