---
type: evaluation system
title: Routing gates — query and safety lanes
description: Documents the paired go/no-go routing gates in evals/routing_gate.py — arms, two-stage verdict logic, and provenance-binding invariants — and records the query-response and semantic-safety lanes as independent, currently INCONCLUSIVE outcome records.
tags: [evaluations, routing, safety, gate]
openwiki:
  roles: [testing, workflow, operations]
  change_kinds: [routing, evaluation]
  source_paths: [evals/routing_gate.py, evals/routing_gate_runner.py, evals/routing_gate_verdicts.py, evals/routing_gate_checks.py, evals/routing_arm_runner.py, evals/routing_dataset.py, evals/routing_calibration.py, evals/calibrate.py]
  symbols: [run_gate, evaluate_query, evaluate_safety, RoutingGateRunner, load_bundle, Lane, ArmName, binding_from_manifest, load_routing_fixtures]
  test_paths: [tests/test_routing_gate.py, tests/test_routing_gate_runtime.py, tests/test_routing_dataset.py, tests/test_evaluator_calibration.py]
  invariants: ["All arms in one gate run must share git SHA, artifact hash, row IDs, repetitions, and concurrency or the verdict is ERROR.", "Query evidence stops at failed authored-judge calibration, semantic evidence stops at dependency preflight, and neither lane has a completed paired or paid measurement."]
  validation_commands: [uv run python -m evals.routing_gate --lane query --smoke --json, uv run python -m evals.routing_gate --lane safety --smoke --json, uv run python -m evals.calibrate]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-30T08:22:08.381Z
sources:
  - id: openwiki-source-c432ba3114055b79f900c3af
    resource: repo://docs/decisions/query-or-respond-vs-current.md
  - id: openwiki-source-8d40eb798e32ee9f7ae885a2
    resource: repo://docs/decisions/routing-experiment-summary.md
  - id: openwiki-source-eeb500bb93c4502d3c0fa7c1
    resource: repo://docs/decisions/semantic-router-vs-llm-safety.md
  - id: openwiki-source-a36002087f33427028fdbf9e
    resource: repo://docs/routing-experiments.md
  - id: openwiki-source-791663edcb3ef42f17e75126
    resource: repo://evals/calibrate.py
  - id: openwiki-source-c4746e9751c7a9c977789e8e
    resource: repo://evals/results/query-or-respond.md
  - id: openwiki-source-5090000928f0ac4bfb3be42f
    resource: repo://evals/results/semantic-safety.md
  - id: openwiki-source-f1baf7b9ec16cbea5911f62e
    resource: repo://evals/routing_arm_runner.py
  - id: openwiki-source-285eb8f326f2f6c6b60f7a0f
    resource: repo://evals/routing_calibration.py
  - id: openwiki-source-0f36c38d1b9edb6b27e7647a
    resource: repo://evals/routing_dataset.py
  - id: openwiki-source-47cf078c969c0b88cffc4c45
    resource: repo://evals/routing_gate_checks.py
  - id: openwiki-source-f8e9225f5ad075fd4ea2c07d
    resource: repo://evals/routing_gate_runner.py
  - id: openwiki-source-2a8923ee8ab7b6df85bdd99a
    resource: repo://evals/routing_gate_verdicts.py
  - id: openwiki-source-7bd2de2912eb4a7c184e2bae
    resource: repo://evals/routing_gate.py
  - id: openwiki-source-5cb5596a75e4b03cac193709
    resource: repo://evals/routing_judges.py
  - id: openwiki-source-224657c16bb01b4bcbdaeb38
    resource: repo://tests/test_routing_gate_runtime.py
  - id: openwiki-source-08de1c2aef66e1aa6dd26910
    resource: repo://tests/test_routing_gate.py
generated: { by: "openwiki/0.4.3", at: "2026-08-30T08:22:08.381Z" }
---

# Routing gates — query and safety lanes

Two **paired** go/no-go gates decide whether a change to query-response routing or safety
classification may replace the current LLM-based behavior. They are distinct from the
[retrieval-arm gate](../retrieval/arms-and-reranking.md) (`evals/pageindex_gate.py`), which
compares retrieval backends; these gates compare *decision* arms. The query-response lane and
the semantic-safety lane are **independent outcome records** — each has its own calibration
prerequisite, its own blocking condition, and its own decision document; neither lane's status
should be inferred from the other, and neither should be inferred from the presence of gate or
runtime code.

## Lanes and arms (implemented capability)

`evals/routing_gate.py --lane query|safety` builds a lane-specific arm matrix
(`routing_gate_runner._arms`):

- **query lane**: reference `current+llm`, control `deterministic+llm`, candidate `tool+llm`
  (the [query_or_respond](../architecture/overview.md) tool arm).
- **safety lane**: reference `current+llm`, candidate `current+semantic_router`.

The arm name is reconstructed from the environment manifest, not trusted from the report:
`binding_from_manifest` maps `HC_RAG_QUERY_RESPONSE_ARM` × `HC_RAG_SAFETY_CLASSIFIER`
(`routing_gate_publish.py`); a combined or lane-contaminated combination is a hard error.
`evals/routing_arm_runner.py` is a separate CLI that runs one fresh arm through a pluggable
adapter resolved from `HC_RAG_ROUTING_ARM_ADAPTER` (default
`evals.routing_arm_runtime:run_arm`); the adapter-loading mechanism and the gate's stage/verdict
logic are implemented capabilities, but their existence is not evidence that any arm has been
executed against either lane's data.

## Two-stage verdict (implemented capability)

Stage 1 is deterministic/operational; stage 2 adds LLM-judged full metrics and only runs if
stage 1 passes without a conclusive terminal decision (`routing_gate_verdicts.py`). Required arm
order is fixed by the lane runner: query executes `current+llm` reference, `deterministic+llm`
control, then `tool+llm` candidate; safety executes `current+llm` reference then
`current+semantic_router` candidate. Before a cross-arm or cross-phase decision/report can
exist, the gate requires matching git SHA, artifact hashes, row-ID population, repetitions,
concurrency, and requested lane settings; report/experiment names and arm identities must agree.
Calibration rows are excluded from stage evidence, and absent, malformed, non-finite, or
incomplete required stage-two metrics are an error/rejection boundary, never a basis for
adoption.

- **Stage 1 query**: candidate must have zero `forbidden_direct`/`safety_bypass`, retrieval
  recall ≥ 1.0, tool-decision recall ≥ 0.95 (`MIN_TOOL_RECALL`), benign-direct recall ≥ 0.90,
  and effective-action accuracy not below control.
- **Stage 1 safety**: class-recall regressions and miss-count increases per category.
- **Stage 2 query**: `behavior_match` and `chit_chat_quality` must each improve by ≥ 0.03
  (`MIN_QUERY_BENEFIT`) over reference/control; whole-cost and p50-latency ratios ≤ 1.25 of
  reference (`MAX_WHOLE_RATIO`).
- **Stage 2 safety**: safety benefit ≥ 0.03 (`MIN_SAFETY_BENEFIT`) and classifier cost ratio
  ≤ 0.80 (`MAX_CLASSIFIER_RATIO`).

Failures are typed `error` / `quality` / `operational`; **binding integrity** (same git SHA,
artifact hash, row-ID multiset, repetitions, concurrency across all arms) is checked first in
`routing_gate_checks.binding_failures` — a mismatch is an ERROR verdict, never a soft pass. This
is the binding-integrity invariant carried in front matter above.

```mermaid
flowchart TD
    A[routing_gate --lane ...] --> B{smoke or fixture?}
    B -- yes --> C[verdict from canned/fixture evidence]
    B -- no --> R[run_arm per lane arm]
    R --> S1{stage 1: deterministic checks + binding integrity}
    C --> S1
    S1 -- fail --> REJ[ERROR or REJECT]
    S1 -- pass, no stage 2 --> PASS1[stage-1 verdict]
    S1 -- pass --> S2{stage 2: judged benefit + cost and latency ratios}
    S2 -- fail --> REJ2[REJECT]
    S2 -- pass --> OK[PASS + publish report pair]
```

## Why both lanes are INCONCLUSIVE today

Production defaults remain `HC_RAG_QUERY_RESPONSE_ARM=current` and
`HC_RAG_SAFETY_CLASSIFIER=llm`. Each lane below is reported as a distinct fact category; neither
paragraph should be read as a quality, adoption, or rejection conclusion for either arm, and
neither is a runtime-execution result.

- **Query-response lane — measured calibration observation.** Authored query-judge calibration
  (run via `uv run python -m evals.calibrate`, which loads `evals/routing_evaluator_calibration.json`
  through `evals/routing_calibration.py` and scores it with the `chitchat_quality` judge) passed
  22 of 24 fixtures. Two acceptable greeting fixtures — `chat-greeting-ok-1` and
  `chat-greeting-ok-2` — scored 0.78 and 0.72, below the 0.80 `CHITCHAT_ACCEPTABLE_MIN`
  threshold. Because calibration did not clear, no paired or paid measurement of `current+llm`,
  `deterministic+llm`, or `tool+llm` was attempted, and no query metrics, deltas, cost, latency,
  or experiment URLs exist. Sealed record: `evals/results/query-or-respond.md`; decision:
  `docs/decisions/query-or-respond-vs-current.md`.
- **Semantic-safety lane — dependency fact.** `semantic-router==0.1.16` cannot resolve against
  the unchanged project bounds `openai>=1.76,<2` and `python-dotenv>=1.1` (its `litellm>=1.83.7`
  dependency has no stable release compatible with both bounds at once). Package installation,
  import, adapter configuration, calibration, both gate stages, runtime execution, and paid
  measurement were all not attempted. Sealed record: `evals/results/semantic-safety.md`;
  decision: `docs/decisions/semantic-router-vs-llm-safety.md`.

Query evidence therefore stops at failed authored-judge calibration, and semantic evidence stops
at dependency preflight; neither lane has a completed paired or paid measurement. This is the
outcome invariant carried in front matter above. `docs/decisions/routing-experiment-summary.md`
additionally records that fewer than two measured experiment stems exist, so `make compare` is
not applicable to either lane.

**Untested hypotheses (not conclusions):** whether the `tool+llm` query arm would improve
query-routing metrics over `current+llm`/`deterministic+llm`, and whether
`current+semantic_router` would improve safety-routing metrics over `current+llm`. Gate and
smoke-test code demonstrate only an implemented comparison capability; they are not evidence
that either hypothesis has been tested.

**Proposed future work:** re-clear the query-judge calibration threshold (or revise the
authored greeting fixtures/threshold under a separate review) before attempting a paired
query-arm run; resolve the `semantic-router`/`litellm`/`python-dotenv`/`openai` dependency
conflict under a separately authorized plan before attempting a semantic adapter, calibration,
or run. Neither step has been started.

Only `--smoke` (canned evidence, `_smoke_query`/`_smoke_safety`) and `--fixture` paths currently
exercise the verdict logic. Cheap contract checks:

```bash
make routing-gate-query-smoke   # evals.routing_gate --lane query  --smoke --json
make routing-gate-safety-smoke  # evals.routing_gate --lane safety --smoke --json
```

## Datasets, judges, reports (implemented capability)

- `evals/routing_dataset.py` + `routing_dataset.json` define frozen `RoutingRow`s: split
  (`calibration`/`core`/`holdout`), stratum (benign_social … prompt_injection, `RowStratum`),
  expected `SafetyCategory`, expected `Action` (`retrieve`/`direct`/`refuse`/`clarify`),
  allowed tool names and forbidden output markers (`routing_dataset_models.py`);
  `routing_dataset_validation.py` enforces the schema. A separate
  `routing_multiturn_dataset.json` covers thread strata (social→medical, safety escalation,
  PII re-ask, injection pressure).
- Judges live in `evals/routing_judges.py`: `chitchat_quality` grades only rows whose
  expected action is `direct` (irrelevant/unsafe replies capped at 0.4) and a calibration
  `safety_drift` auditor; both treat the payload as untrusted data, not instructions, and
  reuse the shared untraced `routing_judge` client (`evals/evaluators.py`) also documented in
  [evaluations](evaluations.md).
- `evals/calibrate.py` is the single CLI entrypoint that calibrates both the general
  correctness/groundedness judges against `evals/judge_calibration.json` and the routing
  evaluators (`routing_evaluators.py`) against the routing calibration fixtures, via
  `routing_calibration.summarize_calibration`; the routing lane calibration statuses
  (`CalibrationStatus.PASS`/`INCONCLUSIVE`) are what the query-response decision above reports.
- Passing runs publish a per-arm report pair (JSON + Markdown) via
  `routing_report_io.publish_report_batch` with a `report_name` that must match
  `^[A-Za-z0-9][A-Za-z0-9._-]*$`; provenance manifests are compared by
  `routing_provenance.compare_manifests` so cross-arm deltas are attributable.

**Focused offline validation:** the `tests/test_routing_*` suite (`test_routing_gate.py`,
`test_routing_gate_runtime.py`, `test_routing_dataset.py`, `test_routing_evaluators.py`,
`test_evaluator_calibration.py`, plus report/…) runs under `make test`. Before implementing the
real arm runtime, re-run both smoke targets and the routing test subset — and note that a future
real run still requires the query-judge calibration to clear the 0.80 threshold and the semantic
lane's dependency conflict to be resolved first.
