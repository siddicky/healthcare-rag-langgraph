# Comparison — reference: `baseline-gpt4o-mini-25edbd33`

- `baseline-gpt4o-mini-25edbd33` — llm=gpt-4o-mini validator=gpt-4o n=45 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/761f5284-d389-490b-8eaa-dcde01e52938
- `luna-terra-full-93db3592` — llm=gpt-5.6-luna validator=gpt-5.6-terra n=86 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/0fef0fc2-c05d-4757-85d2-52b07bd7d892
- `synth-luna-terra-0b106b95` — llm=gpt-5.6-luna validator=gpt-5.6-terra n=86 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/596ac3a6-5d51-40f5-a2af-2cfa3a811992
- `abl-no-decompose-5dcfb85c` — llm=gpt-5.6-luna validator=gpt-5.6-terra n=45 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/36284edf-99c1-4efa-853b-8d09ce05763a
- `abl-no-validate-0c7036cf` — llm=gpt-5.6-luna validator=gpt-5.6-terra n=45 · https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/9aaef21c-943c-4071-903a-39b054873cca

## Overall

| metric | baseline-gpt4o-mini-25edbd33 | luna-terra-full-93db3592 | synth-luna-terra-0b106b95 | abl-no-decompose-5dcfb85c | abl-no-validate-0c7036cf |
|---|---|---|---|---|---|
| behavior_match | 0.77 | 0.83 (▲0.06 ✅) | 0.79 (▲0.02 ✅) | 0.82 (▲0.06 ✅) | 0.78 (▲0.01 ✅) |
| safe_redirect | 0.00 | 0.16 (▲0.16 ✅) | 0.16 (▲0.16 ✅) | 0.00 (=) | 0.00 (=) |
| numeric_advice_leak | – | – | 0.52 | 0.38 | 0.54 |
| forbidden_content | 0.00 | 0.04 (▲0.04 ❌) | 0.05 (▲0.05 ❌) | 0.00 (=) | 0.00 (=) |
| false_premise_corrected | 1.00 | 1.00 (=) | 1.00 (=) | 1.00 (=) | 1.00 (=) |
| correctness | 0.75 | 0.81 (▲0.06 ✅) | 0.89 (▲0.13 ✅) | 0.90 (▲0.15 ✅) | 0.86 (▲0.10 ✅) |
| groundedness | 0.89 | 0.95 (▲0.06 ✅) | 0.93 (▲0.04 ✅) | 0.94 (▲0.05 ✅) | 0.93 (▲0.05 ✅) |
| hallucinated | 0.46 | 0.42 (▼0.04 ✅) | 0.51 (▲0.05 ❌) | 0.46 (▼0.00 ✅) | 0.46 (▲0.00 ❌) |
| correct_but_ungrounded | – | – | 0.36 | 0.32 | 0.16 |
| must_mention_recall | 0.53 | 0.57 (▲0.04 ✅) | 0.68 (▲0.15 ✅) | 0.54 (▲0.01 ✅) | 0.53 (▼0.00 ❌) |
| chunk_recall | 0.62 | 0.79 (▲0.17 ✅) | 0.83 (▲0.21 ✅) | 0.82 (▲0.20 ✅) | 0.80 (▲0.18 ✅) |
| page_recall | 0.69 | 0.82 (▲0.13 ✅) | 0.86 (▲0.17 ✅) | 0.87 (▲0.18 ✅) | 0.83 (▲0.14 ✅) |
| right_collection_routed | 0.89 | 0.91 (▲0.02 ✅) | 0.93 (▲0.04 ✅) | 0.98 (▲0.09 ✅) | 0.91 (▲0.02 ✅) |
| answered | 0.89 | 0.93 (▲0.04 ✅) | 0.93 (▲0.04 ✅) | 0.87 (▼0.02 ❌) | 0.91 (▲0.02 ✅) |
| pipeline_error | 0.00 | 0.00 (=) | 0.00 (=) | 0.00 (=) | 0.00 (=) |
| heuristic_agrees_with_judge | – | – | 0.88 | 0.89 | 0.91 |
| latency_s | 12.42 | 23.72 (▲11.30 ❌) | 17.29 (▲4.87 ❌) | 15.06 (▲2.64 ❌) | 9.89 (▼2.54 ✅) |
| time_to_first_answer_s | 6.14 | 7.36 (▲1.22 ❌) | 8.64 (▲2.51 ❌) | 7.17 (▲1.04 ❌) | 6.95 (▲0.81 ❌) |
| total_ktokens | – | 33.98 | 17.25 | 12.32 | 14.01 |
| est_cost_usd | $0.0276 | $0.0702 (▲0.0426 ❌) | $0.0284 (▲0.0008 ❌) | $0.0243 (▼0.0033 ✅) | $0.0038 (▼0.0238 ✅) |
| llm_calls | 10.67 | 16.97 (▲6.30 ❌) | 11.64 (▲0.97 ❌) | 6.82 (▼3.84 ✅) | 12.20 (▲1.53 ❌) |
| n_branches | 2.20 | 2.90 (▲0.70 ❌) | 2.58 (▲0.38 ❌) | 1.09 (▼1.11 ✅) | 2.36 (▲0.16 ❌) |
| latency_p50_s | 13.87 | 17.13 (▲3.26 ❌) | 15.89 (▲2.03 ❌) | 12.78 (▼1.09 ✅) | 9.38 (▼4.48 ✅) |
| latency_p95_s | 19.89 | 60.52 (▲40.64 ❌) | 33.73 (▲13.84 ❌) | 31.10 (▲11.21 ❌) | 17.64 (▼2.25 ✅) |
| est_cost_total_usd | $1.2429 | $6.0390 (▲4.7961 ❌) | $2.4433 (▲1.2004 ❌) | $1.0934 (▼0.1495 ✅) | $0.1700 (▼1.0729 ✅) |
| total_ktokens_sum | – | 2922.16 | 1483.72 | 554.20 | 630.47 |
