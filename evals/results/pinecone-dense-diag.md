# Retrieval go/no-go — `pinecone` vs. `weaviate`

- **Verdict: REJECT** (stage 1, `pass=false`, score=-0.057)
- Arms: A = `weaviate` (reference) · candidates `pinecone`
- Run: 2026-08-20T16:13:13+00:00 → 2026-08-20T16:18:09+00:00 (297 s wall)
- git: `61da91e` · smoke: `false`

## Stage 1 — retrieval-only gate

71 of 86 golden questions scored (8 have no `expected_source_pages`, 7 are multi-turn). `page_recall` / `chunk_recall` use the `evals.evaluators` definitions unchanged.

| arm | n | page_recall | chunk_recall | core page_recall | core chunk_recall | holdout page_recall | holdout chunk_recall | errors | wall (s) |
|---|---|---|---|---|---|---|---|---|---|
| `weaviate` (reference) | 71 | 0.664 | 0.595 | 0.675 | 0.618 | 0.652 | 0.571 | 0 | 140.180 |
| `pinecone` | 71 | 0.607 | 0.530 | 0.695 | 0.584 | 0.517 | 0.474 | 0 | 153.420 |

### Candidates

| candidate | page_recall | Δ vs. reference | mean latency (s) | pass | → stage 2 |
|---|---|---|---|---|---|
| `pinecone` | 0.607 | -0.057 | 2.161 | ❌ |  |

| gate | A (`weaviate`) | B (`pinecone`) | threshold | pass |
|---|---|---|---|---|
| stage1_page_recall (`pinecone`) | 0.664 | 0.607 | page_recall(B) >= page_recall(A) - 1e-09 | ❌ |

## Stage 2 — paired full eval

_(not run)_

## Verdict

Stage 1 rejected `pinecone`: the best of them, `pinecone`, reached mean page_recall 0.607 vs. 0.664 for `weaviate` (Δ -0.057) over 71 eligible golden questions. Retrieval that returns worse pages cannot be recovered downstream, so the paired full eval was not run and no judge budget was spent.

## Thresholds (frozen)

| threshold | value |
|---|---|
| stage1_page_recall_epsilon | 1e-09 |
| min_correctness_delta | 0.03 |
| min_groundedness_delta | 0 |
| min_holdout_correctness_delta | 0 |
| max_cost_ratio | 1.25 |
| max_latency_p50_ratio | 1.25 |

