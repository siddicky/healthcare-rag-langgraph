# Decision: Semantic Router safety classifier vs current LLM classifier

- **Verdict: INCONCLUSIVE** — the semantic-safety lane is blocked by a missing
  compatible dependency under the unchanged project bounds.
- **Reason: `missing dependency`.** The pinned
  `semantic-router==0.1.16` is unsatisfiable with unchanged `openai<2` and
  `python-dotenv>=1.1`. Its `litellm>=1.83.7` dependency has no
  compatible stable resolution: LiteLLM 1.83.7–1.83.13 pin
  `python-dotenv==1.0.1`, while LiteLLM 1.83.14 and later require OpenAI 2.x.
- **Production defaults remain:** `HC_RAG_QUERY_RESPONSE_ARM=current` and
  `HC_RAG_SAFETY_CLASSIFIER=llm`.

## Measurement status

Todo 10 records the adapter branch as dependency-blocked, and Todo 13 records
the calibration branch as dependency-blocked. No candidate installation,
import, instantiation, or configuration occurred. Stage 1, stage 2, and paid
measurement were not attempted. The semantic-router-vs-llm
comparison is therefore **unmeasured**; no result is inferred from source
inspection or historical material.

## Evidence

- [Semantic-safety result JSON](../../evals/results/semantic-safety.json)
- [Semantic-safety result Markdown](../../evals/results/semantic-safety.md)
- [Todo 9 repeated-failure diagnosis](../../.omo/evidence/task-9-repeated-failure-diagnosis.md)
- [Todo 9 dependency blocker log](../../.omo/evidence/task-9-dependency-blocker.log)
- [Todo 9 repeated-resolution verification log](../../.omo/evidence/task-9-stop-verification-2.log)
- [Todo 9 third dependency verification log](../../.omo/evidence/task-9-third-dependency-verification.log)
- [Todo 10 dependency-blocked adapter record](../../.omo/evidence/task-10-evaluate-query-response-and-semantic-safety-routing.log)
- [Todo 13 dependency-blocked calibration record](../../.omo/evidence/task-13-evaluate-query-response-and-semantic-safety-routing.json)

The dependency evidence is the basis for this bounded decision. Resolving the
dependency conflict under a separately authorized plan is required before any
future semantic candidate evaluation can be interpreted.
