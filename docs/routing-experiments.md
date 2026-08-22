# Query-response and semantic-safety routing experiments

This document records two independent, off-by-default routing evaluation lanes.
It separates the available evidence from the work that was blocked before it
could be run. Production defaults remain
`HC_RAG_QUERY_RESPONSE_ARM=current` and
`HC_RAG_SAFETY_CLASSIFIER=llm`.

## Shared routing contract

The query-response arm is selected with `HC_RAG_QUERY_RESPONSE_ARM`; the
available arms are `current`, `deterministic`, and `tool`. Each query arm uses
the reference `llm` safety classifier. The intended paired gate command, after
its calibration prerequisite passes, is:

```sh
uv run python -m evals.routing_gate --lane query --stage all --repetitions 2 --concurrency 1 --json --report-name query-or-respond
```

The safety router retains exactly six `SafetyCategory` values:
`in_scope_informational`, `personal_medical_advice`, `emergency_red_flag`,
`out_of_scope`, `prompt_injection`, and `ambiguous`. `benign_social` is a
separate annotation, not a seventh safety category. It identifies the narrow
social/capability turns eligible for a direct response; medical, mixed,
ambiguous-clinical, other-drug, emergency, personal-advice, PHI-recall, and
injection turns stay on the existing retrieve, clarify, or refusal path.

## Query-response lane — INCONCLUSIVE

The query lane is **INCONCLUSIVE**, not an adoption or rejection result. Its
authored query-judge calibration passed 22 of 24 fixtures. The two acceptable
greeting fixtures scored 0.78 and 0.72, respectively, below the required 0.80
minimum. Therefore no paired measurement and no paid query run occurred for
`current+llm`, `deterministic+llm`, or `tool+llm`; no query metrics, deltas,
cost, latency, or experiment URLs are available.

Reproduce the recorded outcome by inspecting the committed result artifacts:

- [Query result JSON](../evals/results/query-or-respond.json)
- [Query result Markdown](../evals/results/query-or-respond.md)
- [Query decision record](decisions/query-or-respond-vs-current.md)

This result does not establish a paired comparison, a no-change result, or a
quality conclusion. A future run needs a freshly calibration-cleared gate from
the sealed baseline before the command above can be interpreted.

## Semantic-safety lane — dependency-blocked INCONCLUSIVE

The Semantic Router candidate is **INCONCLUSIVE** because the pinned
`semantic-router==0.1.16` cannot resolve with the unchanged project bounds
`openai>=1.76,<2` and `python-dotenv>=1.1`. This is a dependency fact, not a
runtime result.

No Semantic Router adapter was implemented. No semantic calibration, stage 1,
stage 2, or paid semantic run was attempted; the package was not installed,
imported, or exercised. Accordingly, the semantic artifact has empty metric,
delta, cost, latency, threshold, runtime, and experiment-result collections.

- [Semantic result JSON](../evals/results/semantic-safety.json)
- [Semantic result Markdown](../evals/results/semantic-safety.md)
- [Semantic dependency decision record](decisions/semantic-router-vs-llm-safety.md)

## Limits and next steps

The query calibration miss is a measured observation, but it is not evidence
that any query arm performs better or worse. The semantic incompatibility is a
resolver outcome, but it is not evidence about Semantic Router quality, cost,
latency, or safety. A separately authorized dependency-resolution plan would
be required before creating an adapter, calibrating a semantic candidate, or
running a semantic evaluation. Neither lane changes the production defaults.
