# Comparison — reference: `graph-luna-terra-888a223d`

- `graph-luna-terra-888a223d` — llm=gpt-5.6-luna validator=gpt-5.6-terra n=86 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/c42a7a9b-ed93-439b-8767-1809b122e5cf
- `graph-luna-terra-d6ca6cd9` — llm=gpt-5.6-luna validator=gpt-5.6-terra n=86 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/ada41aa3-ba10-472e-b1e6-6f554a4d3666

## Overall

| metric | graph-luna-terra-888a223d | graph-luna-terra-d6ca6cd9 |
|---|---|---|
| behavior_match | 0.90 | 0.91 (▲0.01 ✅) |
| safe_redirect | 0.64 | 0.64 (=) |
| numeric_advice_leak | 0.04 | 0.04 (=) |
| forbidden_content | 0.01 | 0.01 (=) |
| false_premise_corrected | 1.00 | 1.00 (=) |
| correctness | 0.85 | 0.87 (▲0.01 ✅) |
| groundedness | 0.95 | 0.94 (▼0.01 ❌) |
| hallucinated | 0.42 | 0.42 (=) |
| correct_but_ungrounded | 0.32 | 0.27 (▼0.05 ❌) |
| must_mention_recall | 0.60 | 0.65 (▲0.05 ✅) |
| chunk_recall | 0.65 | 0.65 (▼0.00 ❌) |
| page_recall | 0.68 | 0.68 (▲0.00 ✅) |
| right_collection_routed | 0.79 | 0.79 (=) |
| answered | 1.00 | 1.00 (=) |
| pipeline_error | 0.00 | 0.00 (=) |
| heuristic_agrees_with_judge | 0.81 | 0.83 (▲0.01 ✅) |
| latency_s | 15.28 | 29.11 (▲13.83 ❌) |
| time_to_first_answer_s | 8.65 | 19.90 (▲11.25 ❌) |
| total_ktokens | 10.33 | 10.50 (▲0.17 ❌) |
| est_cost_usd | $0.0170 | $0.0172 (▲0.0002 ❌) |
| llm_calls | 7.26 | 7.33 (▲0.07 ❌) |
| n_branches | 1.74 | 1.91 (▲0.16 ❌) |
| latency_p50_s | 15.35 | 29.27 (▲13.92 ❌) |
| latency_p95_s | 31.61 | 64.40 (▲32.79 ❌) |
| est_cost_total_usd | $1.4577 | $1.4774 (▲0.0197 ❌) |
| total_ktokens_sum | 888.18 | 903.09 (▲14.91 ❌) |
