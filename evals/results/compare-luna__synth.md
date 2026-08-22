# Comparison — reference: `luna-terra-full-93db3592`

- `luna-terra-full-93db3592` — llm=gpt-5.6-luna validator=gpt-5.6-terra n=86 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/0fef0fc2-c05d-4757-85d2-52b07bd7d892
- `synth-luna-terra-0b106b95` — llm=gpt-5.6-luna validator=gpt-5.6-terra n=86 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/596ac3a6-5d51-40f5-a2af-2cfa3a811992

## Overall

| metric | luna-terra-full-93db3592 | synth-luna-terra-0b106b95 |
|---|---|---|
| behavior_match | 0.83 | 0.79 (▼0.03 ❌) |
| safe_redirect | 0.16 | 0.16 (=) |
| numeric_advice_leak | – | 0.52 |
| forbidden_content | 0.04 | 0.05 (▲0.01 ❌) |
| false_premise_corrected | 1.00 | 1.00 (=) |
| correctness | 0.81 | 0.89 (▲0.07 ✅) |
| groundedness | 0.95 | 0.93 (▼0.02 ❌) |
| hallucinated | 0.42 | 0.51 (▲0.09 ❌) |
| correct_but_ungrounded | – | 0.36 |
| must_mention_recall | 0.57 | 0.68 (▲0.11 ✅) |
| chunk_recall | 0.79 | 0.83 (▲0.05 ✅) |
| page_recall | 0.82 | 0.86 (▲0.04 ✅) |
| right_collection_routed | 0.91 | 0.93 (▲0.02 ✅) |
| answered | 0.93 | 0.93 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | – | 0.88 |
| latency_s | 23.72 | 17.29 (▼6.43 ✅) |
| time_to_first_answer_s | 7.36 | 8.64 (▲1.28 ❌) |
| total_ktokens | 33.98 | 17.25 (▼16.73 ✅) |
| est_cost_usd | $0.0702 | $0.0284 (▼0.0418 ✅) |
| llm_calls | 16.97 | 11.64 (▼5.33 ✅) |
| n_branches | 2.90 | 2.58 (▼0.31 ✅) |
| latency_p50_s | 17.13 | 15.89 (▼1.23 ✅) |
| latency_p95_s | 60.52 | 33.73 (▼26.80 ✅) |
| est_cost_total_usd | $6.0390 | $2.4433 (▼3.5957 ✅) |
| total_ktokens_sum | 2922.16 | 1483.72 (▼1438.44 ✅) |

## adversarial_hallucination

| metric | luna-terra-full-93db3592 | synth-luna-terra-0b106b95 |
|---|---|---|
| behavior_match | 1.00 | 1.00 (=) |
| numeric_advice_leak | – | – |
| forbidden_content | – | – |
| false_premise_corrected | 1.00 | 1.00 (=) |
| correctness | 0.87 | 0.91 (▲0.04 ✅) |
| groundedness | 0.96 | 0.99 (▲0.03 ✅) |
| hallucinated | 0.12 | 0.12 (=) |
| correct_but_ungrounded | – | 0.12 |
| must_mention_recall | 0.53 | 0.66 (▲0.12 ✅) |
| chunk_recall | 0.74 | 0.81 (▲0.07 ✅) |
| page_recall | 0.81 | 0.85 (▲0.04 ✅) |
| right_collection_routed | 1.00 | 1.00 (=) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | – | 0.88 |
| latency_s | 15.46 | 16.43 (▲0.98 ❌) |
| time_to_first_answer_s | 6.52 | 7.11 (▲0.58 ❌) |
| total_ktokens | 22.54 | 13.45 (▼9.09 ✅) |
| est_cost_usd | $0.0356 | $0.0244 (▼0.0112 ✅) |
| llm_calls | 14.12 | 10.88 (▼3.25 ✅) |
| n_branches | 2.50 | 2.00 (▼0.50 ✅) |
| latency_p50_s | 17.48 | 10.56 (▼6.92 ✅) |
| latency_p95_s | 22.30 | 37.33 (▲15.03 ❌) |
| est_cost_total_usd | $0.2849 | $0.1956 (▼0.0893 ✅) |
| total_ktokens_sum | 180.29 | 107.58 (▼72.71 ✅) |

## ambiguous_followup

| metric | luna-terra-full-93db3592 | synth-luna-terra-0b106b95 |
|---|---|---|
| behavior_match | 0.71 | 0.71 (=) |
| numeric_advice_leak | – | – |
| forbidden_content | 0.00 | 0.00 (=) |
| false_premise_corrected | – | – |
| correctness | 0.83 | 0.78 (▼0.05 ❌) |
| groundedness | 0.96 | 0.98 (▲0.01 ✅) |
| hallucinated | 0.29 | 0.17 (▼0.12 ✅) |
| correct_but_ungrounded | – | 0.00 |
| must_mention_recall | 0.55 | 0.68 (▲0.13 ✅) |
| chunk_recall | 0.76 | 0.76 (=) |
| page_recall | 0.80 | 0.76 (▼0.04 ❌) |
| right_collection_routed | 0.86 | 0.86 (=) |
| answered | 1.00 | 0.86 (▼0.14 ❌) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | – | 1.00 |
| latency_s | 28.26 | 25.17 (▼3.09 ✅) |
| time_to_first_answer_s | 7.40 | 9.20 (▲1.80 ❌) |
| total_ktokens | 37.44 | 16.95 (▼20.49 ✅) |
| est_cost_usd | $0.0787 | $0.0401 (▼0.0386 ✅) |
| llm_calls | 22.14 | 11.57 (▼10.57 ✅) |
| n_branches | 3.86 | 2.86 (▼1.00 ✅) |
| latency_p50_s | 30.14 | 15.62 (▼14.51 ✅) |
| latency_p95_s | 47.58 | 67.43 (▲19.85 ❌) |
| est_cost_total_usd | $0.5508 | $0.2808 (▼0.2700 ✅) |
| total_ktokens_sum | 262.08 | 118.61 (▼143.46 ✅) |

## cross_drug

| metric | luna-terra-full-93db3592 | synth-luna-terra-0b106b95 |
|---|---|---|
| behavior_match | 1.00 | 1.00 (=) |
| numeric_advice_leak | – | – |
| forbidden_content | 0.00 | 0.00 (=) |
| false_premise_corrected | – | – |
| correctness | 0.85 | 0.90 (▲0.05 ✅) |
| groundedness | 0.97 | 0.93 (▼0.03 ❌) |
| hallucinated | 0.29 | 0.71 (▲0.43 ❌) |
| correct_but_ungrounded | – | 0.71 |
| must_mention_recall | 0.51 | 0.69 (▲0.18 ✅) |
| chunk_recall | 0.80 | 0.88 (▲0.09 ✅) |
| page_recall | 0.87 | 0.83 (▼0.03 ❌) |
| right_collection_routed | 1.00 | 1.00 (=) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | – | 1.00 |
| latency_s | 17.34 | 19.10 (▲1.76 ❌) |
| time_to_first_answer_s | 8.02 | 9.42 (▲1.40 ❌) |
| total_ktokens | 26.48 | 26.34 (▼0.14 ✅) |
| est_cost_usd | $0.0406 | $0.0378 (▼0.0028 ✅) |
| llm_calls | 11.29 | 11.86 (▲0.57 ❌) |
| n_branches | 1.86 | 2.57 (▲0.71 ❌) |
| latency_p50_s | 15.73 | 20.94 (▲5.21 ❌) |
| latency_p95_s | 27.20 | 27.33 (▲0.12 ❌) |
| est_cost_total_usd | $0.2845 | $0.2648 (▼0.0197 ✅) |
| total_ktokens_sum | 185.35 | 184.35 (▼1.00 ✅) |

## factual_multi

| metric | luna-terra-full-93db3592 | synth-luna-terra-0b106b95 |
|---|---|---|
| behavior_match | 1.00 | 1.00 (=) |
| numeric_advice_leak | – | – |
| forbidden_content | 0.00 | 0.00 (=) |
| false_premise_corrected | – | – |
| correctness | 0.65 | 0.84 (▲0.19 ✅) |
| groundedness | 0.95 | 0.94 (▼0.01 ❌) |
| hallucinated | 0.67 | 0.78 (▲0.11 ❌) |
| correct_but_ungrounded | – | 0.67 |
| must_mention_recall | 0.40 | 0.55 (▲0.15 ✅) |
| chunk_recall | 0.61 | 0.76 (▲0.15 ✅) |
| page_recall | 0.67 | 0.79 (▲0.12 ✅) |
| right_collection_routed | 1.00 | 1.00 (=) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | – | 0.89 |
| latency_s | 28.99 | 20.97 (▼8.03 ✅) |
| time_to_first_answer_s | 7.09 | 8.79 (▲1.70 ❌) |
| total_ktokens | 32.20 | 15.19 (▼17.01 ✅) |
| est_cost_usd | $0.0869 | $0.0330 (▼0.0540 ✅) |
| llm_calls | 15.00 | 9.78 (▼5.22 ✅) |
| n_branches | 2.67 | 2.89 (▲0.22 ❌) |
| latency_p50_s | 23.43 | 20.20 (▼3.23 ✅) |
| latency_p95_s | 59.46 | 35.13 (▼24.34 ✅) |
| est_cost_total_usd | $0.7821 | $0.2966 (▼0.4856 ✅) |
| total_ktokens_sum | 289.78 | 136.73 (▼153.05 ✅) |

## factual_single

| metric | luna-terra-full-93db3592 | synth-luna-terra-0b106b95 |
|---|---|---|
| behavior_match | 1.00 | 1.00 (=) |
| numeric_advice_leak | – | – |
| forbidden_content | 0.07 | 0.04 (▼0.04 ✅) |
| false_premise_corrected | – | – |
| correctness | 0.85 | 0.91 (▲0.06 ✅) |
| groundedness | 0.98 | 0.96 (▼0.02 ❌) |
| hallucinated | 0.25 | 0.29 (▲0.04 ❌) |
| correct_but_ungrounded | – | 0.25 |
| must_mention_recall | 0.64 | 0.72 (▲0.08 ✅) |
| chunk_recall | 0.86 | 0.84 (▼0.02 ❌) |
| page_recall | 0.86 | 0.89 (▲0.03 ✅) |
| right_collection_routed | 0.96 | 1.00 (▲0.04 ✅) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | – | 0.93 |
| latency_s | 18.53 | 13.00 (▼5.53 ✅) |
| time_to_first_answer_s | 6.50 | 6.48 (▼0.03 ✅) |
| total_ktokens | 25.40 | 13.42 (▼11.98 ✅) |
| est_cost_usd | $0.0476 | $0.0198 (▼0.0278 ✅) |
| llm_calls | 13.93 | 9.68 (▼4.25 ✅) |
| n_branches | 2.46 | 2.00 (▼0.46 ✅) |
| latency_p50_s | 14.64 | 12.71 (▼1.93 ✅) |
| latency_p95_s | 46.04 | 21.61 (▼24.43 ✅) |
| est_cost_total_usd | $1.3315 | $0.5538 (▼0.7777 ✅) |
| total_ktokens_sum | 711.23 | 375.85 (▼335.39 ✅) |

## out_of_scope

| metric | luna-terra-full-93db3592 | synth-luna-terra-0b106b95 |
|---|---|---|
| behavior_match | 0.88 | 0.62 (▼0.25 ❌) |
| safe_redirect | 0.12 | 0.12 (=) |
| numeric_advice_leak | – | 0.25 |
| forbidden_content | 0.00 | 0.25 (▲0.25 ❌) |
| false_premise_corrected | – | – |
| correctness | – | – |
| groundedness | 0.95 | 0.79 (▼0.16 ❌) |
| hallucinated | 0.33 | 1.00 (▲0.67 ❌) |
| correct_but_ungrounded | – | – |
| must_mention_recall | – | – |
| chunk_recall | – | – |
| page_recall | – | – |
| right_collection_routed | 0.50 | 0.50 (=) |
| answered | 0.38 | 0.50 (▲0.12 ✅) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | – | 0.75 |
| latency_s | 18.50 | 15.96 (▼2.54 ✅) |
| time_to_first_answer_s | 9.33 | 14.89 (▲5.56 ❌) |
| total_ktokens | 40.77 | 19.80 (▼20.98 ✅) |
| est_cost_usd | $0.0716 | $0.0304 (▼0.0412 ✅) |
| llm_calls | 18.00 | 11.88 (▼6.12 ✅) |
| n_branches | 3.12 | 3.00 (▼0.12 ✅) |
| latency_p50_s | 5.95 | 10.47 (▲4.52 ❌) |
| latency_p95_s | 59.04 | 38.09 (▼20.95 ✅) |
| est_cost_total_usd | $0.5728 | $0.2431 (▼0.3297 ✅) |
| total_ktokens_sum | 326.18 | 158.37 (▼167.81 ✅) |

## pii_or_phi

| metric | luna-terra-full-93db3592 | synth-luna-terra-0b106b95 |
|---|---|---|
| behavior_match | 0.33 | 0.50 (▲0.17 ✅) |
| safe_redirect | 0.00 | 0.25 (▲0.25 ✅) |
| numeric_advice_leak | – | 1.00 |
| forbidden_content | 0.17 | 0.00 (▼0.17 ✅) |
| false_premise_corrected | – | – |
| correctness | 0.57 | 0.85 (▲0.28 ✅) |
| groundedness | 0.92 | 0.89 (▼0.03 ❌) |
| hallucinated | 0.67 | 0.83 (▲0.17 ❌) |
| correct_but_ungrounded | – | 1.00 |
| must_mention_recall | 0.75 | 0.75 (=) |
| chunk_recall | 0.89 | 0.89 (=) |
| page_recall | 0.89 | 0.94 (▲0.06 ✅) |
| right_collection_routed | 1.00 | 1.00 (=) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | – | 1.00 |
| latency_s | 22.99 | 18.01 (▼4.99 ✅) |
| time_to_first_answer_s | 6.89 | 9.87 (▲2.97 ❌) |
| total_ktokens | 36.22 | 18.79 (▼17.42 ✅) |
| est_cost_usd | $0.0695 | $0.0270 (▼0.0425 ✅) |
| llm_calls | 19.83 | 15.33 (▼4.50 ✅) |
| n_branches | 3.83 | 4.17 (▲0.33 ❌) |
| latency_p50_s | 24.26 | 16.74 (▼7.52 ✅) |
| latency_p95_s | 33.08 | 23.94 (▼9.14 ✅) |
| est_cost_total_usd | $0.4172 | $0.1621 (▼0.2551 ✅) |
| total_ktokens_sum | 217.31 | 112.76 (▼104.55 ✅) |

## unsafe_personal_advice

| metric | luna-terra-full-93db3592 | synth-luna-terra-0b106b95 |
|---|---|---|
| behavior_match | 0.38 | 0.23 (▼0.15 ❌) |
| safe_redirect | 0.23 | 0.15 (▼0.08 ❌) |
| numeric_advice_leak | – | 0.54 |
| forbidden_content | 0.00 | 0.08 (▲0.08 ❌) |
| false_premise_corrected | – | – |
| correctness | – | – |
| groundedness | 0.87 | 0.83 (▼0.03 ❌) |
| hallucinated | 0.92 | 0.83 (▼0.08 ✅) |
| correct_but_ungrounded | – | – |
| must_mention_recall | – | – |
| chunk_recall | 0.73 | 0.86 (▲0.12 ✅) |
| page_recall | 0.81 | 0.88 (▲0.08 ✅) |
| right_collection_routed | 0.85 | 0.92 (▲0.08 ✅) |
| answered | 0.92 | 0.92 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | – | 0.69 |
| latency_s | 40.88 | 19.76 (▼21.12 ✅) |
| time_to_first_answer_s | 9.44 | 11.13 (▲1.69 ❌) |
| total_ktokens | 57.69 | 22.27 (▼35.42 ✅) |
| est_cost_usd | $0.1396 | $0.0344 (▼0.1053 ✅) |
| llm_calls | 24.92 | 15.69 (▼9.23 ✅) |
| n_branches | 3.69 | 2.85 (▼0.85 ✅) |
| latency_p50_s | 29.22 | 20.15 (▼9.07 ✅) |
| latency_p95_s | 105.01 | 26.34 (▼78.67 ✅) |
| est_cost_total_usd | $1.8152 | $0.4466 (▼1.3686 ✅) |
| total_ktokens_sum | 749.93 | 289.45 (▼460.48 ✅) |
