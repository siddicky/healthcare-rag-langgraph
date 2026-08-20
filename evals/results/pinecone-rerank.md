# Retrieval go/no-go — `pinecone` · `weaviate+rerank` · `pinecone+rerank` vs. `weaviate`

- **Verdict: INCONCLUSIVE** (stage 1, `pass=false`, score=0.050)
- Arms: A = `weaviate` (reference) · candidates `pinecone`, `weaviate+rerank`, `pinecone+rerank` · selected for stage 2: `weaviate+rerank`
- Run: 2026-08-20T15:59:59+00:00 → 2026-08-20T16:11:37+00:00 (699 s wall)
- git: `61da91e` · smoke: `false`

## Stage 1 — retrieval-only gate

71 of 86 golden questions scored (8 have no `expected_source_pages`, 7 are multi-turn). `page_recall` / `chunk_recall` use the `evals.evaluators` definitions unchanged.

| arm | n | page_recall | chunk_recall | core page_recall | core chunk_recall | holdout page_recall | holdout chunk_recall | errors | wall (s) |
|---|---|---|---|---|---|---|---|---|---|
| `weaviate` (reference) | 71 | 0.648 | 0.587 | 0.666 | 0.611 | 0.629 | 0.561 | 0 | 164.520 |
| `pinecone` | 71 | 0.463 | 0.417 | 0.450 | 0.405 | 0.476 | 0.428 | 0 | 163.070 |
| `weaviate+rerank` | 71 | 0.698 | 0.610 | 0.736 | 0.637 | 0.660 | 0.582 | 0 | 160.550 |
| `pinecone+rerank` | 71 | 0.504 | 0.451 | 0.530 | 0.474 | 0.476 | 0.427 | 0 | 207.240 |

### Candidates

| candidate | page_recall | Δ vs. reference | mean latency (s) | pass | → stage 2 |
|---|---|---|---|---|---|
| `pinecone` | 0.463 | -0.185 | 2.297 | ❌ |  |
| `weaviate+rerank` | 0.698 | 0.050 | 2.261 | ✅ | ✅ |
| `pinecone+rerank` | 0.504 | -0.144 | 2.919 | ❌ |  |

| gate | A (`weaviate`) | B (`weaviate+rerank`) | threshold | pass |
|---|---|---|---|---|
| stage1_page_recall (`pinecone`) | 0.648 | 0.463 | page_recall(B) >= page_recall(A) - 1e-09 | ❌ |
| stage1_page_recall (`weaviate+rerank`) | 0.648 | 0.698 | page_recall(B) >= page_recall(A) - 1e-09 | ✅ |
| stage1_page_recall (`pinecone+rerank`) | 0.648 | 0.504 | page_recall(B) >= page_recall(A) - 1e-09 | ❌ |

## Stage 2 — paired full eval

_(not run)_

## Verdict

Inconclusive: the gate did not reach a stage-2 decision (stage 1 only, or a metric was missing). Nothing here supports adopting or rejecting the arm.

## Thresholds (frozen)

| threshold | value |
|---|---|
| stage1_page_recall_epsilon | 1e-09 |
| min_correctness_delta | 0.03 |
| min_groundedness_delta | 0 |
| min_holdout_correctness_delta | 0 |
| max_cost_ratio | 1.25 |
| max_latency_p50_ratio | 1.25 |

