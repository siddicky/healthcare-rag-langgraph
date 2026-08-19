# Comparison — reference: `synth-luna-terra-0b106b95`

- `synth-luna-terra-0b106b95` — llm=gpt-5.6-luna validator=gpt-5.6-terra n=86 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/596ac3a6-5d51-40f5-a2af-2cfa3a811992
- `safety-luna-terra-e9214cbf` — llm=gpt-5.6-luna validator=gpt-5.6-terra n=86 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/7eb47100-8dd8-4dcf-8346-be9fd24216af

## Overall

| metric | synth-luna-terra-0b106b95 | safety-luna-terra-e9214cbf |
|---|---|---|
| behavior_match | 0.79 | 0.87 (▲0.08 ✅) |
| safe_redirect | 0.16 | 0.64 (▲0.48 ✅) |
| numeric_advice_leak | 0.52 | 0.04 (▼0.48 ❌) |
| forbidden_content | 0.05 | 0.01 (▼0.04 ✅) |
| false_premise_corrected | 1.00 | 0.88 (▼0.12 ❌) |
| correctness | 0.89 | 0.81 (▼0.07 ❌) |
| groundedness | 0.93 | 0.95 (▲0.02 ✅) |
| hallucinated | 0.51 | 0.38 (▼0.14 ✅) |
| correct_but_ungrounded | 0.36 | 0.27 (▼0.08 ❌) |
| must_mention_recall | 0.68 | 0.60 (▼0.08 ❌) |
| chunk_recall | 0.83 | 0.65 (▼0.18 ❌) |
| page_recall | 0.86 | 0.68 (▼0.18 ❌) |
| right_collection_routed | 0.93 | 0.79 (▼0.14 ❌) |
| answered | 0.93 | 0.99 (▲0.06 ✅) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 0.88 | 0.79 (▼0.09 ❌) |
| latency_s | 17.29 | 13.54 (▼3.75 ✅) |
| time_to_first_answer_s | 8.64 | 8.17 (▼0.47 ✅) |
| total_ktokens | 17.25 | 12.69 (▼4.56 ✅) |
| est_cost_usd | $0.0284 | $0.0195 (▼0.0089 ✅) |
| llm_calls | 11.64 | 8.97 (▼2.67 ✅) |
| n_branches | 2.58 | 1.91 (▼0.67 ✅) |
| latency_p50_s | 15.89 | 12.20 (▼3.70 ✅) |
| latency_p95_s | 33.73 | 36.31 (▲2.58 ❌) |
| est_cost_total_usd | $2.4433 | $1.6785 (▼0.7648 ✅) |
| total_ktokens_sum | 1483.72 | 1091.54 (▼392.17 ✅) |

## adversarial_hallucination

| metric | synth-luna-terra-0b106b95 | safety-luna-terra-e9214cbf |
|---|---|---|
| behavior_match | 1.00 | 0.88 (▼0.12 ❌) |
| numeric_advice_leak | – | – |
| forbidden_content | – | – |
| false_premise_corrected | 1.00 | 0.88 (▼0.12 ❌) |
| correctness | 0.91 | 0.85 (▼0.06 ❌) |
| groundedness | 0.99 | 0.99 (▲0.00 ✅) |
| hallucinated | 0.12 | 0.14 (▲0.02 ❌) |
| correct_but_ungrounded | 0.12 | 0.14 (▲0.02 ✅) |
| must_mention_recall | 0.66 | 0.57 (▼0.08 ❌) |
| chunk_recall | 0.81 | 0.77 (▼0.04 ❌) |
| page_recall | 0.85 | 0.77 (▼0.08 ❌) |
| right_collection_routed | 1.00 | 0.88 (▼0.12 ❌) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 0.88 | 0.88 (=) |
| latency_s | 16.43 | 12.40 (▼4.03 ✅) |
| time_to_first_answer_s | 7.11 | 7.40 (▲0.29 ❌) |
| total_ktokens | 13.45 | 13.02 (▼0.43 ✅) |
| est_cost_usd | $0.0244 | $0.0161 (▼0.0083 ✅) |
| llm_calls | 10.88 | 11.25 (▲0.38 ❌) |
| n_branches | 2.00 | 1.88 (▼0.12 ✅) |
| latency_p50_s | 10.56 | 13.86 (▲3.30 ❌) |
| latency_p95_s | 37.33 | 20.33 (▼17.00 ✅) |
| est_cost_total_usd | $0.1956 | $0.1291 (▼0.0665 ✅) |
| total_ktokens_sum | 107.58 | 104.13 (▼3.45 ✅) |

## ambiguous_followup

| metric | synth-luna-terra-0b106b95 | safety-luna-terra-e9214cbf |
|---|---|---|
| behavior_match | 0.71 | 0.57 (▼0.14 ❌) |
| numeric_advice_leak | – | – |
| forbidden_content | 0.00 | 0.00 (=) |
| false_premise_corrected | – | – |
| correctness | 0.78 | 0.70 (▼0.08 ❌) |
| groundedness | 0.98 | 0.85 (▼0.12 ❌) |
| hallucinated | 0.17 | 0.67 (▲0.50 ❌) |
| correct_but_ungrounded | 0.00 | 0.50 (▲0.50 ✅) |
| must_mention_recall | 0.68 | 0.43 (▼0.25 ❌) |
| chunk_recall | 0.76 | 0.90 (▲0.14 ✅) |
| page_recall | 0.76 | 0.95 (▲0.19 ✅) |
| right_collection_routed | 0.86 | 1.00 (▲0.14 ✅) |
| answered | 0.86 | 0.86 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 1.00 | 0.71 (▼0.29 ❌) |
| latency_s | 25.17 | 17.46 (▼7.71 ✅) |
| time_to_first_answer_s | 9.20 | 10.66 (▲1.46 ❌) |
| total_ktokens | 16.95 | 15.48 (▼1.47 ✅) |
| est_cost_usd | $0.0401 | $0.0230 (▼0.0171 ✅) |
| llm_calls | 11.57 | 14.29 (▲2.71 ❌) |
| n_branches | 2.86 | 3.57 (▲0.71 ❌) |
| latency_p50_s | 15.62 | 11.69 (▼3.93 ✅) |
| latency_p95_s | 67.43 | 36.34 (▼31.09 ✅) |
| est_cost_total_usd | $0.2808 | $0.1608 (▼0.1200 ✅) |
| total_ktokens_sum | 118.61 | 108.36 (▼10.26 ✅) |

## cross_drug

| metric | synth-luna-terra-0b106b95 | safety-luna-terra-e9214cbf |
|---|---|---|
| behavior_match | 1.00 | 0.86 (▼0.14 ❌) |
| numeric_advice_leak | – | – |
| forbidden_content | 0.00 | 0.00 (=) |
| false_premise_corrected | – | – |
| correctness | 0.90 | 0.79 (▼0.11 ❌) |
| groundedness | 0.93 | 0.95 (▲0.02 ✅) |
| hallucinated | 0.71 | 0.57 (▼0.14 ✅) |
| correct_but_ungrounded | 0.71 | 0.43 (▼0.29 ❌) |
| must_mention_recall | 0.69 | 0.67 (▼0.02 ❌) |
| chunk_recall | 0.88 | 0.81 (▼0.07 ❌) |
| page_recall | 0.83 | 0.88 (▲0.04 ✅) |
| right_collection_routed | 1.00 | 1.00 (=) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 1.00 | 0.86 (▼0.14 ❌) |
| latency_s | 19.10 | 23.12 (▲4.02 ❌) |
| time_to_first_answer_s | 9.42 | 16.77 (▲7.35 ❌) |
| total_ktokens | 26.34 | 27.56 (▲1.23 ❌) |
| est_cost_usd | $0.0378 | $0.0412 (▲0.0034 ❌) |
| llm_calls | 11.86 | 14.14 (▲2.29 ❌) |
| n_branches | 2.57 | 2.71 (▲0.14 ❌) |
| latency_p50_s | 20.94 | 22.49 (▲1.55 ❌) |
| latency_p95_s | 27.33 | 43.36 (▲16.03 ❌) |
| est_cost_total_usd | $0.2648 | $0.2887 (▲0.0239 ❌) |
| total_ktokens_sum | 184.35 | 192.93 (▲8.58 ❌) |

## factual_multi

| metric | synth-luna-terra-0b106b95 | safety-luna-terra-e9214cbf |
|---|---|---|
| behavior_match | 1.00 | 1.00 (=) |
| numeric_advice_leak | – | – |
| forbidden_content | 0.00 | 0.00 (=) |
| false_premise_corrected | – | – |
| correctness | 0.84 | 0.83 (▼0.01 ❌) |
| groundedness | 0.94 | 0.98 (▲0.04 ✅) |
| hallucinated | 0.78 | 0.22 (▼0.56 ✅) |
| correct_but_ungrounded | 0.67 | 0.22 (▼0.44 ❌) |
| must_mention_recall | 0.55 | 0.53 (▼0.02 ❌) |
| chunk_recall | 0.76 | 0.78 (▲0.02 ✅) |
| page_recall | 0.79 | 0.79 (=) |
| right_collection_routed | 1.00 | 1.00 (=) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 0.89 | 0.89 (=) |
| latency_s | 20.97 | 24.53 (▲3.56 ❌) |
| time_to_first_answer_s | 8.79 | 10.59 (▲1.80 ❌) |
| total_ktokens | 15.19 | 18.00 (▲2.81 ❌) |
| est_cost_usd | $0.0330 | $0.0366 (▲0.0036 ❌) |
| llm_calls | 9.78 | 12.22 (▲2.44 ❌) |
| n_branches | 2.89 | 3.00 (▲0.11 ❌) |
| latency_p50_s | 20.20 | 20.27 (▲0.08 ❌) |
| latency_p95_s | 35.13 | 37.40 (▲2.27 ❌) |
| est_cost_total_usd | $0.2966 | $0.3293 (▲0.0327 ❌) |
| total_ktokens_sum | 136.73 | 162.00 (▲25.27 ❌) |

## factual_single

| metric | synth-luna-terra-0b106b95 | safety-luna-terra-e9214cbf |
|---|---|---|
| behavior_match | 1.00 | 0.93 (▼0.07 ❌) |
| numeric_advice_leak | – | – |
| forbidden_content | 0.04 | 0.04 (=) |
| false_premise_corrected | – | – |
| correctness | 0.91 | 0.84 (▼0.07 ❌) |
| groundedness | 0.96 | 0.97 (▲0.02 ✅) |
| hallucinated | 0.29 | 0.26 (▼0.03 ✅) |
| correct_but_ungrounded | 0.25 | 0.26 (▲0.01 ✅) |
| must_mention_recall | 0.72 | 0.67 (▼0.05 ❌) |
| chunk_recall | 0.84 | 0.82 (▼0.02 ❌) |
| page_recall | 0.89 | 0.85 (▼0.04 ❌) |
| right_collection_routed | 1.00 | 0.96 (▼0.04 ❌) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 0.93 | 0.89 (▼0.04 ❌) |
| latency_s | 13.00 | 14.28 (▲1.27 ❌) |
| time_to_first_answer_s | 6.48 | 7.67 (▲1.19 ❌) |
| total_ktokens | 13.42 | 13.84 (▲0.42 ❌) |
| est_cost_usd | $0.0198 | $0.0199 (▲0.0001 ❌) |
| llm_calls | 9.68 | 10.21 (▲0.54 ❌) |
| n_branches | 2.00 | 2.07 (▲0.07 ❌) |
| latency_p50_s | 12.71 | 13.44 (▲0.73 ❌) |
| latency_p95_s | 21.61 | 22.24 (▲0.63 ❌) |
| est_cost_total_usd | $0.5538 | $0.5569 (▲0.0031 ❌) |
| total_ktokens_sum | 375.85 | 387.53 (▲11.68 ❌) |

## out_of_scope

| metric | synth-luna-terra-0b106b95 | safety-luna-terra-e9214cbf |
|---|---|---|
| behavior_match | 0.62 | 1.00 (▲0.38 ✅) |
| safe_redirect | 0.12 | 0.75 (▲0.62 ✅) |
| numeric_advice_leak | 0.25 | 0.00 (▼0.25 ❌) |
| forbidden_content | 0.25 | 0.00 (▼0.25 ✅) |
| false_premise_corrected | – | – |
| correctness | – | – |
| groundedness | 0.79 | 0.80 (▲0.01 ✅) |
| hallucinated | 1.00 | 1.00 (=) |
| correct_but_ungrounded | – | – |
| must_mention_recall | – | – |
| chunk_recall | – | – |
| page_recall | – | – |
| right_collection_routed | 0.50 | 0.88 (▲0.38 ✅) |
| answered | 0.50 | 1.00 (▲0.50 ✅) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 0.75 | 0.25 (▼0.50 ❌) |
| latency_s | 15.96 | 5.46 (▼10.50 ✅) |
| time_to_first_answer_s | 14.89 | 5.46 (▼9.44 ✅) |
| total_ktokens | 19.80 | 4.04 (▼15.76 ✅) |
| est_cost_usd | $0.0304 | $0.0075 (▼0.0229 ✅) |
| llm_calls | 11.88 | 3.00 (▼8.88 ✅) |
| n_branches | 3.00 | 0.62 (▼2.38 ✅) |
| latency_p50_s | 10.47 | 1.20 (▼9.27 ✅) |
| latency_p95_s | 38.09 | 23.07 (▼15.02 ✅) |
| est_cost_total_usd | $0.2431 | $0.0602 (▼0.1828 ✅) |
| total_ktokens_sum | 158.37 | 32.31 (▼126.06 ✅) |

## pii_or_phi

| metric | synth-luna-terra-0b106b95 | safety-luna-terra-e9214cbf |
|---|---|---|
| behavior_match | 0.50 | 0.83 (▲0.33 ✅) |
| safe_redirect | 0.25 | 1.00 (▲0.75 ✅) |
| numeric_advice_leak | 1.00 | 0.00 (▼1.00 ❌) |
| forbidden_content | 0.00 | 0.00 (=) |
| false_premise_corrected | – | – |
| correctness | 0.85 | 0.53 (▼0.32 ❌) |
| groundedness | 0.89 | 0.77 (▼0.12 ❌) |
| hallucinated | 0.83 | 1.00 (▲0.17 ❌) |
| correct_but_ungrounded | 1.00 | 0.00 (▼1.00 ❌) |
| must_mention_recall | 0.75 | 0.25 (▼0.50 ❌) |
| chunk_recall | 0.89 | 0.06 (▼0.83 ❌) |
| page_recall | 0.94 | 0.11 (▼0.83 ❌) |
| right_collection_routed | 1.00 | 0.17 (▼0.83 ❌) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 1.00 | 0.83 (▼0.17 ❌) |
| latency_s | 18.01 | 3.97 (▼14.04 ✅) |
| time_to_first_answer_s | 9.87 | 2.79 (▼7.07 ✅) |
| total_ktokens | 18.79 | 3.79 (▼15.00 ✅) |
| est_cost_usd | $0.0270 | $0.0034 (▼0.0236 ✅) |
| llm_calls | 15.33 | 2.83 (▼12.50 ✅) |
| n_branches | 4.17 | 0.83 (▼3.33 ✅) |
| latency_p50_s | 16.74 | 1.51 (▼15.22 ✅) |
| latency_p95_s | 23.94 | 12.66 (▼11.28 ✅) |
| est_cost_total_usd | $0.1621 | $0.0205 (▼0.1416 ✅) |
| total_ktokens_sum | 112.76 | 22.77 (▼89.99 ✅) |

## unsafe_personal_advice

| metric | synth-luna-terra-0b106b95 | safety-luna-terra-e9214cbf |
|---|---|---|
| behavior_match | 0.23 | 0.77 (▲0.54 ✅) |
| safe_redirect | 0.15 | 0.46 (▲0.31 ✅) |
| numeric_advice_leak | 0.54 | 0.08 (▼0.46 ❌) |
| forbidden_content | 0.08 | 0.00 (▼0.08 ✅) |
| false_premise_corrected | – | – |
| correctness | – | – |
| groundedness | 0.83 | 0.85 (▲0.02 ✅) |
| hallucinated | 0.83 | 1.00 (▲0.17 ❌) |
| correct_but_ungrounded | – | – |
| must_mention_recall | – | – |
| chunk_recall | 0.86 | 0.18 (▼0.68 ❌) |
| page_recall | 0.88 | 0.19 (▼0.69 ❌) |
| right_collection_routed | 0.92 | 0.23 (▼0.69 ❌) |
| answered | 0.92 | 1.00 (▲0.08 ✅) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 0.69 | 0.77 (▲0.08 ✅) |
| latency_s | 19.76 | 7.15 (▼12.60 ✅) |
| time_to_first_answer_s | 11.13 | 6.42 (▼4.71 ✅) |
| total_ktokens | 22.27 | 6.27 (▼16.00 ✅) |
| est_cost_usd | $0.0344 | $0.0102 (▼0.0241 ✅) |
| llm_calls | 15.69 | 3.46 (▼12.23 ✅) |
| n_branches | 2.85 | 0.77 (▼2.08 ✅) |
| latency_p50_s | 20.15 | 1.40 (▼18.75 ✅) |
| latency_p95_s | 26.34 | 29.59 (▲3.25 ❌) |
| est_cost_total_usd | $0.4466 | $0.1330 (▼0.3136 ✅) |
| total_ktokens_sum | 289.45 | 81.51 (▼207.94 ✅) |
