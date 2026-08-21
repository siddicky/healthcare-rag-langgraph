# Decision: query-or-respond routing vs current

- **Verdict: INCONCLUSIVE** — the authored calibration gate did not clear the
  query lane, so no paired query-response arm comparison was run.
- Reason: `query judge calibration failed authored threshold`.
- Sealed Todo 11 baseline: commit `a0f5c070cb925d34d733a783364d5168a191d536`.
- Production defaults remain `HC_RAG_QUERY_RESPONSE_ARM=current` and
  `HC_RAG_SAFETY_CLASSIFIER=llm`.

## Calibration gate

The authored query-judge calibration passed 22 of 24 fixtures. Two acceptable
greeting fixtures were below the required acceptable minimum of 0.80:

| fixture | score | required minimum |
|---|---:|---:|
| `chat-greeting-ok-1` | 0.78 | 0.80 |
| `chat-greeting-ok-2` | 0.72 | 0.80 |

The safety calibration passed 12 of 12 fixtures under its exact-binary rule.
The query calibration result therefore remains INCONCLUSIVE, and the Todo 12
conditional branch is to publish this record without paid query runs.

## Measurement status

The adapter, query gate stage 1, and stage 2 were not attempted. Paid
measurements of `current+llm`, `deterministic+llm`, and `tool+llm` were not
attempted; all three arm-measurement flags are false.

Consequently, the candidate-vs-current comparison is **unmeasured**, not zero
and not no-change. The candidate-vs-deterministic-control comparison is also
**unmeasured**, not zero and not no-change. No result is inferred from source
inspection or from a historical report; no historical report is a comparator.

## Evidence and next step

The result artifacts are linked here for the concurrent measurement record:

- [Query-or-respond result JSON](../../evals/results/query-or-respond.json)
- [Query-or-respond result Markdown](../../evals/results/query-or-respond.md)
- [Todo 11 sealed evidence](../../.omo/evidence/task-11-evaluate-query-response-and-semantic-safety-routing.json)
- [Todo 11 final independent verifier](../../.omo/evidence/task-11-final-independent-adversarial-verify.md)

The current runtime remains the selected default. A future query comparison
requires a fresh calibration-cleared gate from the sealed baseline before any
arm comparison can be interpreted.
