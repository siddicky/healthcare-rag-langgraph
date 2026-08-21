# Semantic safety dependency outcome

## Outcome

- Verdict: `INCONCLUSIVE`
- Reason: `missing dependency`
- Pinned package: `semantic-router==0.1.16`
- Unchanged project bounds: `openai>=1.76,<2`; `python-dotenv>=1.1`
- Query-response default: `HC_RAG_QUERY_RESPONSE_ARM=current`
- Safety-classifier default: `HC_RAG_SAFETY_CLASSIFIER=llm`

The semantic candidate was not installed, imported, or exercised. The adapter was not implemented; calibration, stage 1, stage 2, and paid measurement were not attempted.

## Empty result collections

Metrics, deltas, cost, latency, experiment URLs, thresholds, runtime hashes, runtime results, reference results, and candidate results are empty.

## Evidence

- [Todo 9 dependency diagnosis](../../.omo/evidence/task-9-repeated-failure-diagnosis.md)
- [Todo 9 resolver cycle one](../../.omo/evidence/task-9-dependency-blocker.log)
- [Todo 9 resolver cycle two](../../.omo/evidence/task-9-stop-verification-2.log)
- [Todo 9 resolver cycle three](../../.omo/evidence/task-9-third-dependency-verification.log)
- [Todo 10 handled branch](../../.omo/evidence/task-10-evaluate-query-response-and-semantic-safety-routing.log)
- [Todo 13 handled branch](../../.omo/evidence/task-13-evaluate-query-response-and-semantic-safety-routing.json)
