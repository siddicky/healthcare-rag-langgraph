# Retrieval go/no-go — `weaviate+rerank` vs. `weaviate`

- **Verdict: REJECT** (stage 2, `pass=false`, score=-0.051)
- Arms: A = `weaviate` (reference) · candidates `weaviate+rerank`
- Run: 2026-08-20T17:37:27+00:00 → 2026-08-20T18:05:22+00:00 (1675 s wall)
- git: `61da91e` · smoke: `false`

## Stage 1 — retrieval-only gate

_(skipped)_

## Stage 2 — paired full eval

- A: `pi-gate-weaviate-89bc4ed3` → `/Users/siddicky/Projects/nymble/healthcare-rag-langgraph/evals/results/pi-gate-weaviate-89bc4ed3.md`
- B: `pi-gate-weaviate-rerank-606da650` → `/Users/siddicky/Projects/nymble/healthcare-rag-langgraph/evals/results/pi-gate-weaviate-rerank-606da650.md`

| gate | A (`weaviate`) | B (`weaviate+rerank`) | threshold | pass |
|---|---|---|---|---|
| correctness_delta | 0.850 | 0.799 | B - A >= +0.030 | ❌ |
| groundedness | 0.925 | 0.922 | B - A >= +0.000 | ❌ |
| holdout_correctness | 0.828 | 0.737 | B - A >= +0.000 | ❌ |
| cost_ratio | $0.0188 | $0.0194 | B <= 1.25 × A | ✅ |
| latency_p50_ratio | 17.607 | 18.927 | B <= 1.25 × A | ✅ |

### By split

| split | metric | `weaviate` | `weaviate+rerank` | Δ |
|---|---|---|---|---|
| core | n | 90.000 | 90.000 | 0.000 |
| core | correctness | 0.870 | 0.855 | -0.015 |
| holdout | n | 82.000 | 82.000 | 0.000 |
| holdout | correctness | 0.828 | 0.737 | -0.090 |

### Full comparison

### Overall

| metric | pi-gate-weaviate-89bc4ed3 | pi-gate-weaviate-rerank-606da650 |
|---|---|---|
| behavior_match | 0.91 | 0.86 (▼0.05 ❌) |
| safe_redirect | 0.66 | 0.62 (▼0.04 ❌) |
| numeric_advice_leak | 0.02 | 0.04 (▲0.02 ✅) |
| forbidden_content | 0.02 | 0.03 (▲0.01 ❌) |
| false_premise_corrected | 0.94 | 0.88 (▼0.06 ❌) |
| correctness | 0.85 | 0.80 (▼0.05 ❌) |
| groundedness | 0.93 | 0.92 (▼0.00 ❌) |
| hallucinated | 0.44 | 0.50 (▲0.06 ❌) |
| correct_but_ungrounded | 0.30 | 0.32 (▲0.02 ✅) |
| must_mention_recall | 0.60 | 0.57 (▼0.03 ❌) |
| chunk_recall | 0.66 | 0.64 (▼0.02 ❌) |
| page_recall | 0.68 | 0.69 (▲0.01 ✅) |
| right_collection_routed | 0.79 | 0.77 (▼0.02 ❌) |
| answered | 0.99 | 0.98 (▼0.01 ❌) |
| pipeline_error | 0.01 | 0.02 (▲0.01 ❌) |
| heuristic_agrees_with_judge | 0.83 | 0.79 (▼0.04 ❌) |
| latency_s | 18.20 | 18.56 (▲0.36 ❌) |
| time_to_first_answer_s | 10.63 | 10.93 (▲0.30 ❌) |
| total_ktokens | 10.79 | 11.18 (▲0.39 ❌) |
| est_cost_usd | $0.0188 | $0.0194 (▲0.0006 ❌) |
| llm_calls | 7.31 | 7.10 (▼0.21 ✅) |
| n_branches | 1.75 | 1.69 (▼0.06 ✅) |
| latency_p50_s | 17.61 | 18.93 (▲1.32 ❌) |
| latency_p95_s | 47.23 | 44.38 (▼2.86 ✅) |
| est_cost_total_usd | $3.2290 | $3.3389 (▲0.1099 ❌) |
| total_ktokens_sum | 1856.16 | 1922.45 (▲66.29 ❌) |

### adversarial_hallucination

| metric | pi-gate-weaviate-89bc4ed3 | pi-gate-weaviate-rerank-606da650 |
|---|---|---|
| behavior_match | 0.94 | 0.81 (▼0.12 ❌) |
| numeric_advice_leak | – | – |
| forbidden_content | – | – |
| false_premise_corrected | 0.94 | 0.88 (▼0.06 ❌) |
| correctness | 0.86 | 0.80 (▼0.06 ❌) |
| groundedness | 0.99 | 0.96 (▼0.04 ❌) |
| hallucinated | 0.07 | 0.29 (▲0.22 ❌) |
| correct_but_ungrounded | 0.00 | 0.14 (▲0.14 ✅) |
| must_mention_recall | 0.52 | 0.40 (▼0.13 ❌) |
| chunk_recall | 0.77 | 0.68 (▼0.09 ❌) |
| page_recall | 0.79 | 0.71 (▼0.08 ❌) |
| right_collection_routed | 0.94 | 0.88 (▼0.06 ❌) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 0.88 | 0.81 (▼0.06 ❌) |
| latency_s | 17.63 | 16.93 (▼0.70 ✅) |
| time_to_first_answer_s | 11.39 | 11.32 (▼0.07 ✅) |
| total_ktokens | 10.86 | 10.52 (▼0.34 ✅) |
| est_cost_usd | $0.0142 | $0.0136 (▼0.0006 ✅) |
| llm_calls | 9.00 | 8.31 (▼0.69 ✅) |
| n_branches | 1.94 | 1.62 (▼0.31 ✅) |
| latency_p50_s | 17.88 | 18.86 (▲0.98 ❌) |
| latency_p95_s | 26.04 | 25.05 (▼0.99 ✅) |
| est_cost_total_usd | $0.2267 | $0.2168 (▼0.0099 ✅) |
| total_ktokens_sum | 173.76 | 168.39 (▼5.37 ✅) |

### ambiguous_followup

| metric | pi-gate-weaviate-89bc4ed3 | pi-gate-weaviate-rerank-606da650 |
|---|---|---|
| behavior_match | 0.71 | 0.71 (=) |
| numeric_advice_leak | – | – |
| forbidden_content | 0.00 | 0.00 (=) |
| false_premise_corrected | – | – |
| correctness | 0.93 | 0.82 (▼0.12 ❌) |
| groundedness | 0.84 | 0.86 (▲0.02 ✅) |
| hallucinated | 0.71 | 0.50 (▼0.21 ✅) |
| correct_but_ungrounded | 0.60 | 0.20 (▼0.40 ❌) |
| must_mention_recall | 0.62 | 0.52 (▼0.10 ❌) |
| chunk_recall | 0.87 | 0.90 (▲0.04 ✅) |
| page_recall | 0.92 | 0.93 (▲0.01 ✅) |
| right_collection_routed | 1.00 | 1.00 (=) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 0.93 | 0.86 (▼0.07 ❌) |
| latency_s | 27.53 | 24.98 (▼2.55 ✅) |
| time_to_first_answer_s | 16.25 | 15.77 (▼0.47 ✅) |
| total_ktokens | 14.62 | 13.92 (▼0.70 ✅) |
| est_cost_usd | $0.0250 | $0.0219 (▼0.0031 ✅) |
| llm_calls | 12.00 | 10.93 (▼1.07 ✅) |
| n_branches | 3.71 | 2.71 (▼1.00 ✅) |
| latency_p50_s | 22.56 | 23.04 (▲0.48 ❌) |
| latency_p95_s | 53.81 | 44.26 (▼9.55 ✅) |
| est_cost_total_usd | $0.3494 | $0.3063 (▼0.0431 ✅) |
| total_ktokens_sum | 204.75 | 194.89 (▼9.86 ✅) |

### cross_drug

| metric | pi-gate-weaviate-89bc4ed3 | pi-gate-weaviate-rerank-606da650 |
|---|---|---|
| behavior_match | 0.86 | 0.93 (▲0.07 ✅) |
| numeric_advice_leak | – | – |
| forbidden_content | 0.00 | 0.00 (=) |
| false_premise_corrected | – | – |
| correctness | 0.74 | 0.80 (▲0.06 ✅) |
| groundedness | 0.97 | 0.93 (▼0.04 ❌) |
| hallucinated | 0.33 | 0.69 (▲0.36 ❌) |
| correct_but_ungrounded | 0.33 | 0.46 (▲0.13 ✅) |
| must_mention_recall | 0.49 | 0.52 (▲0.04 ✅) |
| chunk_recall | 0.69 | 0.77 (▲0.08 ✅) |
| page_recall | 0.70 | 0.80 (▲0.09 ✅) |
| right_collection_routed | 0.86 | 0.93 (▲0.07 ✅) |
| answered | 0.86 | 0.93 (▲0.07 ✅) |
| pipeline_error | 0.14 | 0.07 (▼0.07 ✅) |
| heuristic_agrees_with_judge | 0.93 | 0.86 (▼0.07 ❌) |
| latency_s | 33.66 | 28.14 (▼5.52 ✅) |
| time_to_first_answer_s | 12.78 | 13.56 (▲0.78 ❌) |
| total_ktokens | 23.53 | 21.42 (▼2.11 ✅) |
| est_cost_usd | $0.0495 | $0.0380 (▼0.0115 ✅) |
| llm_calls | 9.86 | 8.86 (▼1.00 ✅) |
| n_branches | 1.71 | 1.79 (▲0.07 ❌) |
| latency_p50_s | 27.11 | 25.45 (▼1.66 ✅) |
| latency_p95_s | 70.27 | 48.75 (▼21.52 ✅) |
| est_cost_total_usd | $0.6929 | $0.5314 (▼0.1615 ✅) |
| total_ktokens_sum | 329.45 | 299.86 (▼29.59 ✅) |

### factual_multi

| metric | pi-gate-weaviate-89bc4ed3 | pi-gate-weaviate-rerank-606da650 |
|---|---|---|
| behavior_match | 1.00 | 1.00 (=) |
| numeric_advice_leak | – | – |
| forbidden_content | 0.00 | 0.00 (=) |
| false_premise_corrected | – | – |
| correctness | 0.84 | 0.82 (▼0.02 ❌) |
| groundedness | 0.95 | 0.93 (▼0.02 ❌) |
| hallucinated | 0.61 | 0.67 (▲0.06 ❌) |
| correct_but_ungrounded | 0.44 | 0.44 (=) |
| must_mention_recall | 0.54 | 0.57 (▲0.03 ✅) |
| chunk_recall | 0.75 | 0.75 (▲0.01 ✅) |
| page_recall | 0.78 | 0.87 (▲0.08 ✅) |
| right_collection_routed | 1.00 | 1.00 (=) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 0.83 | 0.89 (▲0.06 ✅) |
| latency_s | 29.49 | 29.65 (▲0.16 ❌) |
| time_to_first_answer_s | 13.66 | 13.60 (▼0.06 ✅) |
| total_ktokens | 15.12 | 15.59 (▲0.47 ❌) |
| est_cost_usd | $0.0330 | $0.0343 (▲0.0012 ❌) |
| llm_calls | 9.56 | 9.28 (▼0.28 ✅) |
| n_branches | 2.94 | 3.00 (▲0.06 ❌) |
| latency_p50_s | 28.11 | 30.44 (▲2.33 ❌) |
| latency_p95_s | 46.96 | 44.53 (▼2.43 ✅) |
| est_cost_total_usd | $0.5946 | $0.6165 (▲0.0219 ❌) |
| total_ktokens_sum | 272.19 | 280.66 (▲8.47 ❌) |

### factual_single

| metric | pi-gate-weaviate-89bc4ed3 | pi-gate-weaviate-rerank-606da650 |
|---|---|---|
| behavior_match | 0.96 | 0.91 (▼0.05 ❌) |
| numeric_advice_leak | – | – |
| forbidden_content | 0.05 | 0.07 (▲0.02 ❌) |
| false_premise_corrected | – | – |
| correctness | 0.89 | 0.83 (▼0.05 ❌) |
| groundedness | 0.96 | 0.95 (▼0.01 ❌) |
| hallucinated | 0.33 | 0.35 (▲0.01 ❌) |
| correct_but_ungrounded | 0.28 | 0.31 (▲0.03 ✅) |
| must_mention_recall | 0.70 | 0.68 (▼0.02 ❌) |
| chunk_recall | 0.85 | 0.80 (▼0.05 ❌) |
| page_recall | 0.87 | 0.87 (=) |
| right_collection_routed | 0.96 | 0.91 (▼0.05 ❌) |
| answered | 1.00 | 0.98 (▼0.02 ❌) |
| pipeline_error | 0.00 | 0.02 (▲0.02 ❌) |
| heuristic_agrees_with_judge | 0.95 | 0.88 (▼0.07 ❌) |
| latency_s | 18.76 | 20.56 (▲1.81 ❌) |
| time_to_first_answer_s | 10.91 | 11.53 (▲0.62 ❌) |
| total_ktokens | 11.23 | 12.47 (▲1.24 ❌) |
| est_cost_usd | $0.0171 | $0.0203 (▲0.0032 ❌) |
| llm_calls | 8.71 | 8.66 (▼0.05 ✅) |
| n_branches | 1.86 | 2.05 (▲0.20 ❌) |
| latency_p50_s | 18.56 | 19.83 (▲1.27 ❌) |
| latency_p95_s | 29.23 | 32.80 (▲3.56 ❌) |
| est_cost_total_usd | $0.9590 | $1.1370 (▲0.1780 ❌) |
| total_ktokens_sum | 628.91 | 698.13 (▲69.22 ❌) |

### out_of_scope

| metric | pi-gate-weaviate-89bc4ed3 | pi-gate-weaviate-rerank-606da650 |
|---|---|---|
| behavior_match | 1.00 | 1.00 (=) |
| safe_redirect | 0.75 | 0.75 (=) |
| numeric_advice_leak | 0.00 | 0.00 (=) |
| forbidden_content | 0.00 | 0.00 (=) |
| false_premise_corrected | – | – |
| correctness | – | – |
| groundedness | 0.69 | 0.84 (▲0.15 ✅) |
| hallucinated | 1.00 | 1.00 (=) |
| correct_but_ungrounded | – | – |
| must_mention_recall | – | – |
| chunk_recall | – | – |
| page_recall | – | – |
| right_collection_routed | 0.88 | 0.88 (=) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 0.19 | 0.19 (=) |
| latency_s | 8.97 | 7.75 (▼1.22 ✅) |
| time_to_first_answer_s | 8.97 | 7.75 (▼1.22 ✅) |
| total_ktokens | 5.13 | 4.46 (▼0.67 ✅) |
| est_cost_usd | $0.0114 | $0.0084 (▼0.0030 ✅) |
| llm_calls | 2.38 | 2.38 (=) |
| n_branches | 0.62 | 0.62 (=) |
| latency_p50_s | 1.52 | 1.61 (▲0.09 ❌) |
| latency_p95_s | 51.52 | 44.50 (▼7.02 ✅) |
| est_cost_total_usd | $0.1821 | $0.1337 (▼0.0484 ✅) |
| total_ktokens_sum | 82.14 | 71.39 (▼10.75 ✅) |

### pii_or_phi

| metric | pi-gate-weaviate-89bc4ed3 | pi-gate-weaviate-rerank-606da650 |
|---|---|---|
| behavior_match | 0.83 | 0.67 (▼0.17 ❌) |
| safe_redirect | 1.00 | 1.00 (=) |
| numeric_advice_leak | 0.00 | 0.00 (=) |
| forbidden_content | 0.00 | 0.00 (=) |
| false_premise_corrected | – | – |
| correctness | 0.52 | 0.17 (▼0.34 ❌) |
| groundedness | 0.58 | 0.67 (▲0.09 ✅) |
| hallucinated | 1.00 | 1.00 (=) |
| correct_but_ungrounded | 0.00 | – |
| must_mention_recall | 0.25 | 0.00 (▼0.25 ❌) |
| chunk_recall | 0.28 | 0.17 (▼0.11 ❌) |
| page_recall | 0.33 | 0.17 (▼0.17 ❌) |
| right_collection_routed | 0.33 | 0.17 (▼0.17 ❌) |
| answered | 1.00 | 0.83 (▼0.17 ❌) |
| pipeline_error | 0.00 | 0.17 (▲0.17 ❌) |
| heuristic_agrees_with_judge | 0.83 | 0.83 (=) |
| latency_s | 6.68 | 12.65 (▲5.98 ❌) |
| time_to_first_answer_s | 5.18 | 6.31 (▲1.13 ❌) |
| total_ktokens | 5.25 | 6.25 (▲0.99 ❌) |
| est_cost_usd | $0.0060 | $0.0156 (▲0.0096 ❌) |
| llm_calls | 3.33 | 3.17 (▼0.17 ✅) |
| n_branches | 1.00 | 0.17 (▼0.83 ✅) |
| latency_p50_s | 1.83 | 2.04 (▲0.20 ❌) |
| latency_p95_s | 20.45 | 51.65 (▲31.20 ❌) |
| est_cost_total_usd | $0.0721 | $0.1871 (▲0.1149 ❌) |
| total_ktokens_sum | 63.04 | 74.96 (▲11.92 ❌) |

### unsafe_personal_advice

| metric | pi-gate-weaviate-89bc4ed3 | pi-gate-weaviate-rerank-606da650 |
|---|---|---|
| behavior_match | 0.81 | 0.73 (▼0.08 ❌) |
| safe_redirect | 0.50 | 0.42 (▼0.08 ❌) |
| numeric_advice_leak | 0.04 | 0.08 (▲0.04 ✅) |
| forbidden_content | 0.00 | 0.00 (=) |
| false_premise_corrected | – | – |
| correctness | – | – |
| groundedness | 0.76 | 0.81 (▲0.05 ✅) |
| hallucinated | 1.00 | 1.00 (=) |
| correct_but_ungrounded | – | – |
| must_mention_recall | – | – |
| chunk_recall | 0.14 | 0.19 (▲0.05 ✅) |
| page_recall | 0.15 | 0.22 (▲0.07 ✅) |
| right_collection_routed | 0.19 | 0.27 (▲0.08 ✅) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 0.85 | 0.81 (▼0.04 ❌) |
| latency_s | 7.16 | 8.32 (▲1.16 ❌) |
| time_to_first_answer_s | 6.81 | 7.60 (▲0.79 ❌) |
| total_ktokens | 3.92 | 5.16 (▲1.24 ❌) |
| est_cost_usd | $0.0059 | $0.0081 (▲0.0022 ❌) |
| llm_calls | 2.69 | 3.23 (▲0.54 ❌) |
| n_branches | 0.58 | 0.81 (▲0.23 ❌) |
| latency_p50_s | 1.87 | 2.10 (▲0.23 ❌) |
| latency_p95_s | 23.55 | 30.21 (▲6.66 ❌) |
| est_cost_total_usd | $0.1522 | $0.2101 (▲0.0578 ❌) |
| total_ktokens_sum | 101.93 | 134.17 (▲32.24 ❌) |

## Verdict

Stage 2 rejected `weaviate+rerank` on quality: correctness_delta, groundedness, holdout_correctness failed. Δcorrectness = -0.051 against a required +0.03. Quality failures are not tuned away.

## Thresholds (frozen)

| threshold | value |
|---|---|
| stage1_page_recall_epsilon | 1e-09 |
| min_correctness_delta | 0.03 |
| min_groundedness_delta | 0 |
| min_holdout_correctness_delta | 0 |
| max_cost_ratio | 1.25 |
| max_latency_p50_ratio | 1.25 |

