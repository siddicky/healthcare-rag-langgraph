# PageIndex vs. Weaviate — retrieval go/no-go

- **Verdict: REJECT** (stage 1, `pass=false`, score=-0.071)
- Arms: A = `weaviate` (reference) · B = `pageindex` (candidate)
- Run: 2026-08-20T12:24:34+00:00 → 2026-08-20T12:30:15+00:00 (341 s wall)
- git: `6bfb4ca` · smoke: `false`

## Stage 1 — retrieval-only gate

71 of 86 golden questions scored (8 have no `expected_source_pages`, 7 are multi-turn). `page_recall` / `chunk_recall` use the `evals.evaluators` definitions unchanged.

| arm | n | page_recall | chunk_recall | core page_recall | core chunk_recall | holdout page_recall | holdout chunk_recall | errors | wall (s) |
|---|---|---|---|---|---|---|---|---|---|
| `weaviate` | 71 | 0.681 | 0.618 | 0.685 | 0.627 | 0.676 | 0.609 | 0 | 117.990 |
| `pageindex` | 71 | 0.609 | 0.601 | 0.637 | 0.636 | 0.581 | 0.565 | 0 | 219.850 |

| gate | A (`weaviate`) | B (`pageindex`) | threshold | pass |
|---|---|---|---|---|
| stage1_page_recall | 0.681 | 0.609 | page_recall(B) >= page_recall(A) - 1e-09 | ❌ |

## Stage 2 — paired full eval

_(not run)_

## Verdict

Stage 1 rejected `pageindex`: mean page_recall 0.609 vs. 0.681 for `weaviate` (Δ -0.071) over 71 eligible golden questions. Retrieval that returns worse pages cannot be recovered downstream, so the paired full eval was not run and no judge budget was spent.

## Thresholds (frozen)

| threshold | value |
|---|---|
| stage1_page_recall_epsilon | 1e-09 |
| min_correctness_delta | 0.03 |
| min_groundedness_delta | 0 |
| min_holdout_correctness_delta | 0 |
| max_cost_ratio | 1.25 |
| max_latency_p50_ratio | 1.25 |

