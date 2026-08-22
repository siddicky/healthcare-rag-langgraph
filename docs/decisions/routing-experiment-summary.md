# Routing experiment summary

This record consolidates the query-response and semantic-safety routing
decisions. It records applicability of a comparison only from completed gate
measurements; empty collections and attempted-but-unfinished work are not
measurements.

## Lane decisions

| Lane | Measured fact | Conclusion | Default |
|---|---|---|---|
| Query-response | Authored judge calibration passed 22 of 24 fixtures. `chat-greeting-ok-1` scored `0.78` and `chat-greeting-ok-2` scored `0.72`, both below the required `0.80`. No paired or paid arm measurement was run. | `INCONCLUSIVE` | `HC_RAG_QUERY_RESPONSE_ARM=current` |
| Semantic safety | `semantic-router==0.1.16` cannot satisfy the unchanged `openai>=1.76,<2` and `python-dotenv>=1.1` bounds. All attempt flags are false and the result collections are empty. | Unmeasured dependency-`INCONCLUSIVE` | `HC_RAG_SAFETY_CLASSIFIER=llm` |

The detailed lane records are [query-response vs current](query-or-respond-vs-current.md)
and [semantic router vs the LLM safety classifier](semantic-router-vs-llm-safety.md).

The untested hypotheses remain narrow: whether tool direct responses would
improve the query-routing metrics, and whether semantic classification would
improve the safety-routing metrics. No quality, cost, latency, or adoption
claim is inferred for either hypothesis.

## Reproducible comparison-stem assessment

The parser used for this assessment scans `evals/results/*.json` and derives a
stem only when all of the following hold:

1. The JSON is a routing-gate measurement object, not a calibration-only or
   dependency-only record.
2. Every actual-completed measurement flag in the object is `true`.
3. Every required result collection for the measured arms is nonempty.
4. The filename stem is not `semantic-safety`; that dependency outcome can
   never become a measured comparison stem.

For the query gate, this means the attempt flags and the `current_arm`,
`control_arm`, `tool_arm`, `metrics`, `deltas`, `cost`, and `latency`
collections must all be populated. The live query artifact fails this test:
all attempt flags are `false` and every measurement collection is empty. The
semantic artifact is dependency-only, has all attempt flags `false`, and is
excluded by both its outcome shape and the explicit `semantic-safety` stem
rule. Therefore the live result is:

```text
actual_stems=[]
```

The [Makefile compare target](../../Makefile) invokes `evals.compare` with
`EXPS`; that command requires at least two experiment names. Because fewer
than two actual measured stems exist, `make compare` is **N/A** for this
summary, with the exact insufficiency reason: `actual_stems=[]` (required: `>=2`).
No historical report is substituted, and `semantic-safety` is not a
measured stem.

## Source artifacts

- [Query gate JSON](../../evals/results/query-or-respond.json)
- [Query gate Markdown](../../evals/results/query-or-respond.md)
- [Semantic-safety gate JSON](../../evals/results/semantic-safety.json)
- [Semantic-safety gate Markdown](../../evals/results/semantic-safety.md)
