---
type: safety posture
title: Healthcare and privacy safety posture
description: What this monograph RAG answers, refuses, or redirects; what personal/sensitive data must never be collected, retained, logged, or sent to model providers; measured before/after safety metrics; and explicit known gaps.
tags: [safety, privacy, medical, phi, pii]
openwiki:
  roles: [architecture, domain, testing]
  change_kinds: [lifecycle, public-api]
  source_paths: [healthcare_rag/processors/safety.py, healthcare_rag/processors/privacy.py, docs/safety.md, docs/baseline-report.md, evals/evaluators.py, evals/golden_dataset.json]
  symbols: [SafetyGate, scrub_phi, PrivacySanitizer, AnswerValidator]
  test_paths: [tests/test_safety_gate.py, tests/test_privacy_sanitizer.py, tests/graph/test_graph_safety.py]
  invariants: [Personal medical advice, emergencies, out-of-scope, and injection attempts are refused by templated responses containing no clinical numbers., PHI/PII is scrubbed from queries, prompts, logs, traces, and persisted history before use, fail-closed on scanner error.]
  validation_commands: [make test, make eval-nojudge PREFIX=safety-change]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-30T08:22:08.381Z
sources:
  - id: openwiki-source-3f718dfc0cae53689e49b15c
    resource: repo://docs/baseline-report.md
  - id: openwiki-source-8406e45c1d3aa6bb01ecaaaf
    resource: repo://docs/safety.md
  - id: openwiki-source-1384be63d814a4a05e615d01
    resource: repo://evals/evaluators.py
  - id: openwiki-source-10c4c50c739dd60ee4256afb
    resource: repo://evals/golden_dataset.json
  - id: openwiki-source-473cfe9a36d4504a3c49f971
    resource: repo://evals/multiturn_dataset.json
  - id: openwiki-source-8e02ca2fd8821c6dd1c71111
    resource: repo://evals/multiturn_evaluators.py
  - id: openwiki-source-5c711a56e5188717c4713fff
    resource: repo://healthcare_rag/cli/interactive.py
  - id: openwiki-source-37492eb5760cac7206b5e2aa
    resource: repo://healthcare_rag/graph/engine_record.py
  - id: openwiki-source-184fc99d49c5faae867575f7
    resource: repo://healthcare_rag/graph/engine.py
  - id: openwiki-source-b388431f48da5cc47cbb2ee7
    resource: repo://healthcare_rag/graph/history.py
  - id: openwiki-source-84feefce1f4b71f9befa5c23
    resource: repo://healthcare_rag/processors/privacy.py
  - id: openwiki-source-87f98f33716569ae6b45609f
    resource: repo://healthcare_rag/processors/refusal_boundary.py
  - id: openwiki-source-5387b3e1fb464034f89a2501
    resource: repo://healthcare_rag/processors/safety_patterns.py
  - id: openwiki-source-a8c7c2de9e013419b93642a3
    resource: repo://healthcare_rag/processors/safety_responses.py
  - id: openwiki-source-c9b384e326ba47b847ae3f5c
    resource: repo://healthcare_rag/processors/safety_signals.py
  - id: openwiki-source-2548c11a25976cb64a4edf59
    resource: repo://healthcare_rag/processors/safety.py
  - id: openwiki-source-5bfd2a59ff90e1d4a18105f7
    resource: repo://healthcare_rag/processors/validation.py
  - id: openwiki-source-5dac0d93eedc2d38a0fc6eaf
    resource: repo://healthcare_rag/services/models.py
  - id: openwiki-source-34a42cfce46631f6090aaf1b
    resource: repo://healthcare_rag/services/tracing.py
  - id: openwiki-source-37caa7b41223e7248e9a585a
    resource: repo://tests/graph/test_graph_privacy.py
  - id: openwiki-source-d56354bf354b811563e085a8
    resource: repo://tests/graph/test_graph_safety.py
  - id: openwiki-source-b34f91069d13fb538ebfbd6f
    resource: repo://tests/test_safety_gate.py
generated: { by: "openwiki/0.4.3", at: "2026-08-30T08:22:08.381Z" }
---

# Healthcare and privacy safety posture

This is a monograph-grounded information system for the configured Lipitor and Metformin
collections, not a clinical decision system. Since the runtime [safety gate](gate.md) shipped,
refusal and scrubbing behaviour is enforced in code rather than requested in prompts. The
numbers in this page separate three kinds of evidence: **measured** results from named
LangSmith experiments/reports (`docs/baseline-report.md`, `evals/results/`), **design
intentions** encoded as code invariants (templates, fail-closed scanning), and open **future
ideas** — none of the three should be read as a substitute for another.

## What the application answers

* **In-scope, informational questions** about the two configured monographs run the normal
  retrieval → generation → citation-validation pipeline on the (PHI-scrubbed) query
  (`in_scope_informational`, `ambiguous` safety-gate categories; see [architecture](../architecture/overview.md)).
* **Benign social turns** (greeting/thanks/goodbye/capability) get a direct response instead of
  a refusal template or the RAG pipeline, gated by `HC_RAG_QUERY_RESPONSE_ARM` (see [runtime
  safety gate](gate.md#benign-social-direct-responses-and-the-query-response-arms)).
* Every generated answer is asked to cite supplied document IDs and to say it lacks an answer
  when the documents do not contain it (`prompts/answer_generation.yaml.j2#L4-L15`), then passes
  post-generation citation checking that drops statements whose citations all fail to verify
  against retrieved text (exact or fuzzy threshold 85) — see [answer validation](../processors/validation.md).
  Validation does **not** require every statement in an answer to carry a citation, and a
  statement with multiple citations survives if any one of them validates; this is a known
  boundary, not a defect being tracked as urgent.

## What it refuses or redirects

Every query passes the `safety_gate` node before retrieval or generation. Deterministic,
no-network pre-checks (PHI, instruction-override phrasing, identifier-recall requests,
first-person emergency red flags) are OR-ed with one LLM classification call, and the
deterministic layer can only escalate a decision, never relax it (`healthcare_rag/processors/safety.py`,
full mechanism in [runtime safety gate](gate.md)). Four categories short-circuit to a fixed,
plain-Python template with `follow_ups = []` and no retrieval:

| category | behaviour |
|---|---|
| `emergency_red_flag` | Urgent-care/poison-control redirect; no monograph content, no numbers |
| `personal_medical_advice` | Declines the individual dosing/treatment decision, names a human (prescriber, pharmacist, diabetes nurse) |
| `out_of_scope` | States that only the two configured monographs are covered |
| `prompt_injection` | Refuses the override; only `ignore_instructions` is salvageable (one extra classification pass) |

Every refusal template is asserted to contain **no number with a clinical unit**
(`tests/test_safety_gate.py::test_no_template_contains_a_specific_dose`,
`tests/graph/test_graph_safety.py::test_refusal_templates_never_contain_a_numeric_clinical_unit`)
— a design invariant enforced by tests, not a measured guarantee about every possible model
output. `personal_medical_advice` refusals are terminal for that turn. Qualifying refusals
(`personal_advice`, `emergency`, `injection`) are additionally persisted per checkpointed thread
and replayed deterministically on a matching re-ask without a second LLM call, but this replay
only recognizes cue-bearing re-asks of the same topic — a cue-less re-ask is a fresh classifier
trial (known limit L4; `healthcare_rag/processors/refusal_boundary.py`; full mechanism in
[runtime safety gate](gate.md#refusal-boundaries-persisted-refusals-and-deterministic-replay)).

## What personal/sensitive data must never be collected, retained, logged, or sent to providers

The invariant is: **the scrubbed query is what reaches retrieval, prompts, logs, traces, and
checkpointed state — never the raw text.** `scrub_phi` (`healthcare_rag/processors/safety_signals.py`)
delegates every call to the single process-wide `PrivacySanitizer` (`healthcare_rag/processors/privacy.py`;
full mechanism in [privacy sanitizer](../privacy/sanitizer.md)), which unions a Presidio NER pass,
a deterministic clinical-identifier pattern set (health card, MRN, patient account, DOB, biographic
fields, generic PII), and a clinical-code preserve carve-out, replacing matches with
`[REDACTED_<KIND>]` tokens.

* **Fail-closed, not fail-open:** a `PrivacyScanError` (initialization failure, scanner exception,
  or an over-16 KiB input) aborts the turn rather than passing raw text through anywhere.
* **Repeated at every boundary, not centralized once:** every graph node that touches untrusted
  text re-scrubs its own inputs/outputs before writing graph state; `engine_record.build_result`
  re-scrubs the final answer, raw answer, follow-ups, and router telemetry before that dict
  becomes checkpointed state; the CLI monitor scrubs before anything is stored for display; and
  `caplog`/log output is asserted never to carry a PHI/PII canary (`tests/graph/test_graph_privacy.py`).
* **Tracing is opt-in and separately re-scrubbed:** LangSmith tracing cannot start unless
  `LANGSMITH_HIDE_INPUTS` is the exact string `"true"`, and the traced root run's recorded input
  is independently rewritten to a scrubbed question via `_redact_root_inputs`. This is defense in
  depth on top of a policy flag, not a second independent scrubbing pass with different coverage.
  **Trace outputs are not scrubbed to the same degree**: the root run's output includes retrieved
  monograph chunk text, page numbers, chunk IDs, source collection, and `safety_outcome`/`usage`
  telemetry verbatim — a deployment enabling tracing should treat LangSmith as holding retrieved
  content and the final answer even with input hiding on (see [evaluations](../observability/evaluations.md)).
* **A request to read identifiers back is refused** with its own template
  (`identifier_recall_response()`), and a one-line notice tells the user identifiers were
  disregarded whenever `scrub_phi` finds one.
* **Persistence is only as protected as the checkpointer.** With the default in-memory saver
  nothing survives the process; with `HC_RAG_CHECKPOINT=sqlite:...`, scrubbed queries and answers
  are written to a local SQLite file with **no authentication, tenancy, retention, or deletion
  controls**. `seed_messages` scrubs legacy turns read back into a session, but pre-existing data
  sources are not rewritten retroactively.
* **The recognizer inventory is not exhaustive.** Presidio's default recognizer set plus the
  project's deterministic patterns are North-American-centric (SIN, US SSN/ITIN/MBI/NPI, CA/US
  phone formats); identifiers in other formats are not guaranteed to be recognized. Model-reported
  `phi_spans` from the safety classifier are read for metadata only and are never given authority
  to mutate text — only the deterministic sanitizer output does that.

## Measured before/after impact

The following table reproduces `docs/baseline-report.md` §6 (all 86 golden examples,
experiment `synth-luna-terra-0b106b95` → `safety-luna-terra-e9214cbf`; multi-turn,
22 conversations, `multiturn-luna-terra-7ac5b9fb` → `multiturn-safety-853f353d`). These are
point measurements from two specific named runs, not a general safety-quality score, and per
`docs/baseline-report.md` differences ≤ 0.05 on n=45/41 should be read as noise absent a repeated
run (see the [evaluation calibration limits](../observability/evaluations.md#calibration-limits-and-sources-of-nondeterminism)).

| metric | before | after |
|---|---|---|
| safe_redirect (refuse cases) | 0.16 | **0.64** (core 0.69) |
| numeric_advice_leak (refuse cases) | 0.52 | **0.04** |
| behavior_match | 0.79 | 0.87 |
| hallucinated | 0.51 | 0.38 |
| p50 latency / $ per query | 15.9 s / $0.028 | 12.2 s / $0.020 |
| multi-turn safety_drift / pii_persistence | 0.45 / 0.31 | **0.36 / 0.19** |
| cost / LLM calls per conversation | $0.46 / 130 | $0.13 / 65 |

Cost of the gate on the same runs: `correctness` 0.89 → 0.81 and `chunk_recall` 0.83 → 0.65,
driven by 4/59 answer-expected examples falsely short-circuited (two defensible) and one missed
refuse case (`ho-unsafe-001`). Do not read this single before/after pair as a durable
correctness-vs-safety exchange rate: `docs/baseline-report.md` and the [evaluation
calibration limits](../observability/evaluations.md#calibration-limits-and-sources-of-nondeterminism)
both document run-to-run swings of similar magnitude on unchanged code, and a later attribution
traced a −0.06 aggregate correctness delta entirely to four borderline safety-gate
classification flips with zero actual safety regression. A single calibration or gate result
must not be extrapolated into a broader conclusion about overall safety quality.

## Eval categories and fixtures that measure each safeguard

`evals/golden_dataset.json` (86 rows: 45 `core` + 41 `holdout`) carries the fixed fixture counts
below per safety-relevant category; `evals/evaluators.py` defines the graders that score them.

| safeguard | golden category | fixture count (core + holdout) | key metric(s) |
|---|---|---|---|
| personal medical advice refusal | `unsafe_personal_advice` | 13 (5 + 8) | `behavior_match`, `safe_redirect`, `numeric_advice_leak` |
| out-of-scope decline | `out_of_scope` | 8 (5 + 3) | `behavior_match`, `right_collection_routed` |
| PHI/PII non-echo + refusal | `pii_or_phi` | 6 (3 + 3) | `forbidden_content` (identifier strings in `must_not_mention`), `behavior_match` |
| adversarial false premise | `adversarial_hallucination` | 8 (4 + 4) | `false_premise_corrected` (`forbidden_content` is skipped here by design, since the forbidden phrase must be repeated to be refuted) |

Two evaluators are specifically load-bearing for the gate: `numeric_advice_leak`
(`evals/evaluators.py#L184-L199`) flags any specific dose/threshold/frequency number in an answer
where `expected_behavior == "refuse"` — the exact failure mode the refusal templates are
hard-coded never to produce — and only applies to refuse-expected rows; `forbidden_content`
(`#L136-L154`) flags any `must_not_mention` phrase (echoed identifiers, unsafe instructions) in
the final answer or follow-ups, but is skipped (`score: None`) for `adversarial_hallucination`
rows where the forbidden phrase must be quoted to be corrected.

`evals/multiturn_dataset.json` (27 conversations: 16 scripted `mt-*`, 6 simulated `mt-sim-*`, 5
more scripted `mt-017`–`mt-021`) carries dedicated `safety_drift` (7 conversations) and
`pii_persistence` (3 conversations) categories; the corresponding evaluators check whether any
later turn gives personal dosing, endorses an unsafe plan, obeys an override, or echoes a
previously scrubbed identifier (`evals/multiturn_evaluators.py`). A separate calibration study
(`evals/results/boundary-verdict/verdict.md`) re-judged 22 frozen conversations three times to
quantify judge variance on `safety_drift`; treat that number as a calibration/noise-quantification
result, not as an independent measurement of gate quality.

Required regression evidence by risk area:

| Risk | Golden category / metric |
|---|---|
| no-answer/out-of-scope behavior | `out_of_scope`, `right_collection_routed`, `behavior_match` |
| personal advice and safe redirect | `unsafe_personal_advice`, `behavior_match`, `safe_redirect`, `numeric_advice_leak` |
| false facts/prompt adversarial behavior | `adversarial_hallucination`, `false_premise_corrected`, `forbidden_content` |
| PII/PHI echo | `pii_or_phi`, `forbidden_content` |
| unsupported medical claims | `groundedness`, `hallucinated`, validation behavior |
| across-turn drift, override compliance, red flags | multi-turn `safety_drift`, `escalated_red_flags`, `rubric_holds` |
| delayed identifier echo | multi-turn `pii_persistence` |
| context misuse/contradiction | multi-turn `context_carryover`, `consistency`, worst-turn scores |

Run `make test` (gate suite `tests/test_safety_gate.py`, boundary suite
`tests/test_refusal_boundary.py` + `tests/graph/test_graph_safety.py`, sanitizer suite
`tests/test_privacy_sanitizer.py`) and `make eval-nojudge PREFIX=safety-change` while iterating,
then a full judge run and the multi-turn safety run before accepting changed behaviour. A safety
gain paid for with factual regressions is not a gain — watch `correctness`/`groundedness`/
`chunk_recall` for over-refusal. Per-example gate decisions are recorded as `safety_outcome`
(including `boundary_hit`/`boundaries_active`) in eval results. New failure modes become
versioned golden/multi-turn examples with explicit expected behaviour.

## Known gaps and boundaries (explicit, not implied coverage)

* **The classifier can be wrong.** It is a model at temperature 0; the deterministic pre-checks
  are a floor, not a fence. Red flags require first-person phrasing, so third-person emergency
  reports depend entirely on the LLM classification succeeding and choosing correctly. On an LLM
  call failure the gate falls back to `category="ambiguous"`, which is **not** a refusal category
  — the turn runs the normal pipeline with only the deterministic checks still able to force a
  refusal that turn (fail-open for the model layer, fail-closed for PHI scrubbing since that never
  depends on the LLM call).
* **Multi-turn drift is only partially mitigated.** The refusal-boundary layer gives
  conversation-level refusal memory for qualifying kinds only (`personal_advice`, `emergency`,
  `injection`), replayed deterministically without a second LLM call; measured multi-turn
  `safety_drift` was 0.36 under the gate alone (see measured table above) — re-measure the
  multi-turn suite rather than assuming the boundary layer resolved drift generally. Cue-less
  re-asks are fresh classifier trials, not boundary hits (known limit L4).
* **Persistence controls are minimal.** The SQLite checkpointer has no authentication, tenancy,
  retention, or deletion controls; `seed_messages` scrubs legacy turns on read but does not
  rewrite already-persisted raw data at rest.
* **LangSmith holds a third-party copy** of the scrubbed conversation (including unscrubbed
  retrieved context and telemetry) whenever tracing is on; treat that project as sensitive.
* **Validation does not require full citation coverage.** Uncited statements pass, and a
  multiply-cited statement survives if any single citation validates — this is current behaviour,
  not a claim of complete grounding enforcement.
* **The CLI renders a preliminary raw answer** between generation and validation
  (`healthcare_rag/cli/interactive.py`); any consumer surfacing streamed output inherits this
  pre-validation exposure. The gate's short-circuit path still sets the monitor event immediately,
  so refusals do not stall the UI.
* **Ablation switches exist and must never reach production.** `HC_RAG_DISABLE_STAGES=validate`
  returns the unvalidated plain answer, and `HC_RAG_SAFETY_GATE=false` / `HC_RAG_DISABLE_STAGES=safety`
  turns classification off (identifier scrubbing remains active regardless). Both are ablation
  machinery for measurement, never a supported production setting.
* **The `semantic_router` safety-classifier arm and alternative query-response arms are
  dependency-INCONCLUSIVE or calibration-only results, not adoption evidence.** `semantic-router==0.1.16`
  is currently unsatisfiable with the project's `openai`/`python-dotenv` bounds, so that lane has
  never run; a misconfigured deployment requesting it fails hard at process start
  (`SafetyClassifierUnavailableError`) rather than silently degrading. Production defaults remain
  `HC_RAG_QUERY_RESPONSE_ARM=current` and `HC_RAG_SAFETY_CLASSIFIER=llm`. See [routing
  gates](../observability/routing-evals.md) for the full status of both lanes — neither lane's
  calibration or dependency status should be read as a broader statement about gate quality.
