---
type: safety posture
title: Current medical and privacy safety posture
description: What the runtime safety gate and validation enforce, what remains unenforced, measured before/after impact, and required regression checks for this monograph RAG.
tags: [safety, privacy, medical]
openwiki:
  roles: [architecture, domain, testing]
  change_kinds: [lifecycle, public-api]
  source_paths: [healthcare_rag/processors/safety.py, docs/safety.md, docs/baseline-report.md]
  symbols: [SafetyGate, scrub_phi, AnswerValidator]
  test_paths: [tests/test_safety_gate.py]
  invariants: [Personal medical advice, emergencies, out-of-scope, and injection attempts are refused by templated responses containing no clinical numbers., PHI is scrubbed from queries, prompts, and persisted history before use.]
  validation_commands: [make test, make eval-nojudge PREFIX=safety-change]
---

# Current medical and privacy safety posture

This is a monograph-grounded information system for the configured Lipitor and Metformin collections, not a clinical decision system. Since the runtime [safety gate](gate.md) shipped, refusal and scrubbing behaviour is enforced in code rather than requested in prompts; treat remaining expectations in evals as measured behaviour, not guarantees.

## What is implemented

* **Runtime safety gate before every query** ([safety gate](gate.md)): deterministic injection/red-flag pre-checks OR-ed with one LLM classification; personal-advice, emergency, out-of-scope, and injection messages short-circuit to templated refuse-and-redirect responses containing **no numbers with clinical units** (`healthcare_rag/processors/safety.py`, `safety_responses.py`, `docs/safety.md`). Qualifying refusals are persisted per thread and replayed deterministically on matching re-asks, so sustained pressure on an already-refused question no longer re-opens a fresh classifier trial (`processors/refusal_boundary.py`).
* **PHI scrubbing on input:** identifiers are replaced with `[REDACTED_<KIND>]` in the query and in checkpointed history messages before any prompt sees them, and history is seeded scrubbed. Scrubbing is the fail-closed [PrivacySanitizer](../privacy/sanitizer.md) (Presidio plus deterministic clinical patterns) behind `scrub_phi`; a sanitizer failure fails the turn rather than passing raw text through. A request to read identifiers back is refused with its own template. One-line notices tell the user identifiers were disregarded.
* **Source-oriented answer generation:** the answer template asks for each claim to cite supplied document IDs and to say it lacks an answer when documents do not contain it (`prompts/answer_generation.yaml.j2#L4-L15`).
* **Post-generation citation checking:** structuring resolves `doc_N` to retrieved UUIDs, verifies quote evidence against retrieved text (exact or fuzzy threshold 85), and drops statements whose citations all fail. Full details: [answer validation](../processors/validation.md).
* **Scoped persistence isolation in evals:** each eval turn uses a fresh random `eval_*` user id on an isolated engine, so production threads are never touched (`evals/harness.py`). This is an evaluation feature, not application privacy protection.

### Measured impact (all 86 golden examples, `synth-luna-terra-0b106b95` → `safety-luna-terra-e9214cbf`; multi-turn `multiturn-luna-terra-7ac5b9fb` → `multiturn-safety-853f353d`)

| metric | before | after |
|---|---|---|
| safe_redirect (refuse cases) | 0.16 | **0.64** (core 0.69) |
| numeric_advice_leak (refuse cases) | 0.52 | **0.04** |
| behavior_match | 0.79 | 0.87 |
| hallucinated | 0.51 | 0.38 |
| p50 latency / $ per query | 15.9 s / $0.028 | 12.2 s / $0.020 |
| multi-turn safety_drift / pii_persistence | 0.45 / 0.31 | **0.36 / 0.19** |
| cost / LLM calls per conversation | $0.46 / 130 | $0.13 / 65 |

Costs of the gate: correctness 0.89 → 0.81 and chunk_recall 0.83 → 0.65, driven by 4/59 answer-expected examples falsely short-circuited (two defensible) and one missed refuse case (ho-unsafe-001). Full tables: `docs/baseline-report.md` §6.

## What is still not enforced

* **The classifier can be wrong.** It is a model at temperature 0; the deterministic pre-checks are a floor, not a fence — scrubbing now comes from the Presidio-backed [PrivacySanitizer](../privacy/sanitizer.md), but it still recognizes a fixed entity inventory (North-American-centric), and model `phi_spans` are ignored by design. Red flags require a first-person report, so third-person emergencies depend on the model. Refusal-boundary replay can only emit current-version allowed templates, so persistence cannot introduce new text, but cue-less re-asks are fresh classifier trials (known limit L4, `processors/refusal_boundary.py`).
* **Multi-turn drift:** the refusal boundary layer now gives conversation-level refusal memory for qualifying kinds; multi-turn `safety_drift` measured 0.36 under the gate alone, so re-measure the multi-turn suite rather than assuming the boundary fixed it.
* **Persistence is only as protected as the checkpointer.** With the default in-memory saver nothing survives the process; with `HC_RAG_CHECKPOINT=sqlite:...` conversation state (scrubbed queries, answers) is written to a local SQLite file with no authentication, tenancy, retention, or deletion controls (`graph/engine.py`, `graph/history.py`). `seed_messages` scrubs legacy turns, but pre-existing data sources are not rewritten.
* **LangSmith holds a third-party copy** of the (scrubbed) conversation when tracing is on; treat the project as sensitive.
* Validation still does **not** require every statement to be cited: uncited statements pass, and a cited statement survives when one citation validates.
* The CLI still renders a preliminary raw answer between generation and validation (`healthcare_rag/cli/interactive.py`); any consumer surfacing streamed output inherits this. The gate's short-circuit path sets the monitor event immediately, so refusals do not stall the UI.
* With `HC_RAG_DISABLE_STAGES=validate` the graph returns the unvalidated plain answer — that is ablation machinery, never a production setting.

## Required safety regression evidence

For changes to the gate, prompts, models, validation, routing, history, or CLI presentation, use [evaluations](../observability/evaluations.md) and specifically inspect:

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

Run `make test` (the gate suite is `tests/test_safety_gate.py`, the boundary suite `tests/test_refusal_boundary.py` + `tests/graph/test_graph_safety.py`) and `make eval-nojudge PREFIX=safety-change` while iterating, then a full judge run and the multi-turn safety run before accepting changed behaviour. A safety gain paid for with factual regressions is not a gain — watch `correctness`/`groundedness`/`chunk_recall` for over-refusal. Per-example gate decisions are recorded as `safety_outcome` (including `boundary_hit`/`boundaries_active`) in eval results. New failure modes become versioned golden/multi-turn examples with explicit expected behaviour.
