# Comparison — reference: `baseline-gpt4o-mini-25edbd33`

- `baseline-gpt4o-mini-25edbd33` — llm=gpt-4o-mini validator=gpt-4o n=45 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/761f5284-d389-490b-8eaa-dcde01e52938
- `luna-terra-73e65b69` — llm=gpt-5.6-luna validator=gpt-5.6-terra n=45 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/5b8ac860-51bb-49b6-b434-d5a9baa4bd16
- `luna-luna-c3717231` — llm=gpt-5.6-luna validator=gpt-5.6-luna n=45 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/fbfd59cd-80b6-4182-b461-9061f30c6712

## Overall

| metric | baseline-gpt4o-mini-25edbd33 | luna-terra-73e65b69 | luna-luna-c3717231 |
|---|---|---|---|
| correctness | 0.75 | 0.75 (▼0.00 ❌) | 0.55 (▼0.21 ❌) |
| groundedness | 0.89 | 0.95 (▲0.07 ✅) | 0.96 (▲0.08 ✅) |
| hallucinated | 0.46 | 0.32 (▼0.14 ✅) | 0.15 (▼0.31 ✅) |
| behavior_match | 0.77 | 0.82 (▲0.06 ✅) | 0.62 (▼0.14 ❌) |
| safe_redirect | 0.00 | 0.00 (=) | 0.15 (▲0.15 ✅) |
| must_mention_recall | 0.53 | 0.53 (▲0.00 ✅) | 0.35 (▼0.18 ❌) |
| forbidden_content | 0.00 | – | 0.03 (▲0.03 ❌) |
| false_premise_corrected | 1.00 | 1.00 (=) | 1.00 (=) |
| chunk_recall | 0.62 | 0.72 (▲0.10 ✅) | 0.77 (▲0.15 ✅) |
| page_recall | 0.69 | 0.80 (▲0.11 ✅) | 0.80 (▲0.11 ✅) |
| right_collection_routed | 0.89 | 0.92 (▲0.03 ✅) | 0.88 (▼0.01 ❌) |
| answered | 0.89 | 0.89 (▲0.01 ✅) | 0.85 (▼0.04 ❌) |
| pipeline_error | 0.00 | 0.00 (=) | 0.00 (=) |
| latency_s | 12.42 | 23.68 (▲11.26 ❌) | 21.39 (▲8.97 ❌) |
| time_to_first_answer_s | 6.14 | 7.33 (▲1.19 ❌) | 7.47 (▲1.33 ❌) |
| total_tokens | 15525.33 | 36790.68 (▲21265.35 ❌) | 40167.41 (▲24642.08 ❌) |
| est_cost_usd | $0.0276 | $0.0793 (▲0.0517 ❌) | $0.0131 (▼0.0145 ✅) |
| llm_calls | 10.67 | 19.45 (▲8.78 ❌) | 21.51 (▲10.85 ❌) |
| n_branches | 2.20 | 3.55 (▲1.35 ❌) | 3.68 (▲1.48 ❌) |
| latency_p50_s | 13.87 | 22.86 (▲8.99 ✅) | 20.00 (▲6.13 ✅) |
| latency_p95_s | 19.89 | 49.64 (▲29.75 ✅) | 50.02 (▲30.14 ✅) |
| est_cost_total_usd | $1.2429 | $3.0139 (▲1.7710 ✅) | $0.5375 (▼0.7054 ❌) |
