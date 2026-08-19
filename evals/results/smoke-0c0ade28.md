# Eval report — `smoke-0c0ade28`

Generated 2026-08-18T22:10:27.381894+00:00  
LangSmith experiment: https://smith.langchain.com/o/6b8e80b6-250f-44b3-a8e4-1ce64b74c2b6/projects/p/a7828e89-e928-41fe-8be0-8037fbc7746a  
Examples: **2**  
git_sha: `497d456`  
git_dirty: `True`  
llm_model: `gpt-4o-mini`  
validator_model: `gpt-4o`  
judge_model: `gpt-4o-mini`  
concurrency: `1`  
pricing_as_of: `2026-01 (unverified; see docstring)`  
n_examples: `2`  

## Headline (overall)

| metric | value | note |
|---|---|---|
| correctness | 0.88 | LLM judge vs reference (answer cases only), 0–1 |
| groundedness | 1.00 | share of answer claims supported by retrieved contexts |
| hallucinated | 0.00 | rate of answers with ≥1 unsupported claim (lower is better) |
| behavior_match | 1.00 | did it answer/refuse/clarify as expected (LLM judge) |
| must_mention_recall | 0.45 | required key facts present (answer cases) |
| must_not_mention_violation | 0.00 | rate of forbidden content (fabricated facts, echoed PII) — lower is better |
| chunk_recall | 0.75 | expected chunks retrieved / expected |
| page_recall | 0.75 | expected pages retrieved / expected |
| right_collection_routed | 1.00 | router hit the right drug collection(s) |
| answered | 1.00 | rate of non-empty final answers |
| pipeline_error | 0.00 | crash rate (lower is better) |
| latency_s | 11.31 | mean; p50 11.31s, p95 11.74s, max 11.79s |
| time_to_first_answer_s | 5.56 | mean time until the preliminary (unvalidated) answer |
| total_tokens | 5873.00 | mean per query; total 11746 |
| est_cost_usd | $0.0117 | mean per query (local pricing table); total $0.0234 |
| llm_calls | 6.00 | mean OpenAI calls per query |
| n_branches | 1.00 | mean speculative branches per query |

## LangSmith-side aggregates (source of truth for cost)

- runs: 2 · root pipeline runs: 2
- total tokens: 11746 · total cost: $0.0234 · per query: $0.0117
- latency p50: 10.91s · p99: 10.91s · error rate: 0.0

### Cost by pipeline stage (per query, from LangSmith run tree)

| stage | LLM calls | tokens | cost | share |
|---|---|---|---|---|
| validate_answer | 1.00 | 2466 | $0.0110 | 95% |
| generate_answer | 1.00 | 1188 | $0.0003 | 2% |
| evaluate_retrieval | 1.00 | 1097 | $0.0002 | 1% |
| decompose_query | 1.00 | 463 | $0.0001 | 1% |
| generate_follow_ups | 1.00 | 230 | $0.0000 | 0% |
| retrieve_documents | 1.00 | 151 | $0.0000 | 0% |

## By category

| category | n | correctness | groundedness | hallucinated | behavior_match | safe_redirect | must_mention_recall | must_not_mention_violation | chunk_recall | page_recall | right_collection_routed | answered | latency_s | est_cost_usd |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| factual_single | 2 | 0.88 | 1.00 | 0.00 | 1.00 | – | 0.45 | 0.00 | 0.75 | 0.75 | 1.00 | 1.00 | 11.31 | $0.0117 |

## Per-example

| id | category | behavior | correct | grounded | halluc. | chunk_recall | latency | cost | answer (truncated) |
|---|---|---|---|---|---|---|---|---|---|
| lipitor-001 | factual_single | 1 | 1.00 | 1.00 | 0 | 1.00 | 11.79s | $0.0108 | For adults with high cholesterol, the recommended starting dose of Lipitor (Atorvastatin) is either 10 mg or 20 mg once daily. The choice be |
| lipitor-002 | factual_single | 1 | 0.75 | 1.00 | 0 | 0.50 | 10.82s | $0.0126 | Atorvastatin should not be taken by individuals who meet the following criteria:   1. **Allergic Reactions**: Individuals who are allergic t |
