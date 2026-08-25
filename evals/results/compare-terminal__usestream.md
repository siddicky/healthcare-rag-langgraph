# Comparison — reference: `terminal-refusal-local-single`

- `terminal-refusal-local-single` — llm=gpt-5.6-luna validator=gpt-5.6-terra n=86 · 
- `usestream-2def8623` — llm=gpt-5.6-luna validator=gpt-5.6-terra n=86 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/9210cdec-4814-48ac-8a3d-1a394b9b5961

## Overall

| metric | terminal-refusal-local-single | usestream-2def8623 |
|---|---|---|
| behavior_match | 0.92 | 0.86 (▼0.06 ❌) |
| safe_redirect | 0.64 | 0.64 (=) |
| numeric_advice_leak | 0.04 | 0.04 (=) |
| forbidden_content | 0.01 | 0.01 (=) |
| false_premise_corrected | 1.00 | 0.75 (▼0.25 ❌) |
| correctness | 0.86 | 0.81 (▼0.06 ❌) |
| groundedness | 0.96 | 0.95 (▼0.01 ❌) |
| hallucinated | 0.32 | 0.40 (▲0.08 ❌) |
| correct_but_ungrounded | 0.23 | 0.30 (▲0.07 ✅) |
| must_mention_recall | 0.63 | 0.58 (▼0.04 ❌) |
| chunk_recall | 0.61 | 0.58 (▼0.03 ❌) |
| page_recall | 0.64 | 0.60 (▼0.04 ❌) |
| right_collection_routed | 0.77 | 0.73 (▼0.03 ❌) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 0.86 | 0.80 (▼0.06 ❌) |
| latency_s | 14.91 | 15.01 (▲0.11 ❌) |
| time_to_first_answer_s | 8.79 | 7.58 (▼1.20 ✅) |
| total_ktokens | 9.32 | 9.72 (▲0.40 ❌) |
| est_cost_usd | $0.0146 | $0.0168 (▲0.0022 ❌) |
| llm_calls | 6.78 | 6.38 (▼0.40 ✅) |
| n_branches | 1.63 | 1.48 (▼0.15 ✅) |
| latency_p50_s | 15.09 | 13.36 (▼1.72 ✅) |
| latency_p95_s | 33.37 | 33.15 (▼0.22 ✅) |
| est_cost_total_usd | $1.2519 | $1.4414 (▲0.1895 ❌) |
| total_ktokens_sum | 801.37 | 835.56 (▲34.19 ❌) |
