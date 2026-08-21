---
type: runtime component
title: Runtime safety gate
description: First-touch classifier and PHI scrubber that refuses personal medical advice, emergencies, out-of-scope, and injection attempts with templated responses before the RAG pipeline runs.
tags: [safety, gate, pii, prompt-injection]
openwiki:
  roles: [architecture, domain, testing]
  change_kinds: [lifecycle, public-api]
  source_paths: [healthcare_rag/processors/safety.py, healthcare_rag/processors/safety_responses.py, healthcare_rag/processors/refusal_boundary.py, prompts/safety_gate.yaml.j2, healthcare_rag/models/safety.py, healthcare_rag/graph/nodes/safety.py]
  symbols: [SafetyGate, SafetyDecision, scrub_phi, SafetyAssessment, SafetyOutcome, assess_safety, RefusalBoundary, boundary_hit, upsert_boundary]
  test_paths: [tests/test_safety_gate.py, tests/test_refusal_boundary.py, tests/graph/test_graph_safety.py, tests/graph/test_boundary_durability.py]
  invariants: [Deterministic pre-checks can only escalate a decision, never relax it., A refusal template never contains a number with a clinical unit., The scrubbed query is what reaches retrieval, prompts, and history persistence.]
  validation_commands: [make test]
---

# Runtime safety gate

Every query passes the `safety_gate` graph node **before** retrieval or generation (`healthcare_rag/graph/nodes/safety.py`; see [architecture](../architecture/overview.md)). It exists because the baseline had no runtime guard at all: `safe_redirect` scored 0.00–0.33 across every model configuration, a "should I double my metformin tonight?" question returned a dosing table, and out-of-scope questions returned nothing (journey findings F13/F18; `docs/safety.md`, `docs/journey.json`). Full policy doc: `docs/safety.md`; measured impact: `docs/baseline-report.md` §6 and [safety posture](posture.md).

## Design: two layers, OR-ed

1. **Deterministic pre-checks** (no network, `healthcare_rag/processors/safety.py`): PHI detection now delegates to the Presidio-backed [privacy sanitizer](privacy-sanitizer.md); this module keeps the regexes for instruction-override attempts (`injection_flags`), requests to recite identifiers back (`identifier_recall_requested`), and first-person emergency red flags (`red_flag_terms`). These are a **floor** — they can only escalate an outcome (force `emergency_red_flag`/`prompt_injection`, set `contains_phi`), never downgrade what the model chose.
2. **One LLM classification call** (`prompts/safety_gate.yaml.j2` → `SafetyAssessment` in `healthcare_rag/models/safety.py`, temperature 0, default model): one of `in_scope_informational`, `personal_medical_advice`, `emergency_red_flag`, `out_of_scope`, `prompt_injection`, `ambiguous`, plus `phi_spans` and `drug_mentioned`. If this call fails, the deterministic layer still decides (fail-open only for classification, exactly what the pre-gate pipeline did).

`SafetyGate.assess` merges both; `SafetyGate.evaluate` returns a `SafetyDecision` carrying the scrubbed query, template choice, and one-line notices.

```mermaid
flowchart TD
  Q["user message"] --> P["deterministic pre-checks: PHI / injection / identifier recall / red flags"]
  Q --> L["one LLM classification: SafetyAssessment"]
  P --> M{"merge: red flag beats injection beats identifier recall beats LLM"}
  L --> M
  M --> S["scrub_phi: query and checkpoint history"]
  S --> C{"category?"}
  C -->|emergency / personal advice / out-of-scope / injection| T["templated refusal + redirect, follow-ups = []"]
  C -->|in scope / ambiguous| N["pipeline on the SCRUBBED query"]
```

## Scrubbing and short-circuit wiring

- `scrub_phi` delegates to the process-wide Presidio-backed `PrivacySanitizer`; it is **fail-closed** (initialization or scan errors raise `PrivacyScanError` and abort the turn) and model-reported `phi_spans` no longer mutate text. Full design: [privacy sanitizer](privacy-sanitizer.md).
- The `safety_gate` node first derives history views from the checkpointed messages: when the gate is on, every message is scrubbed (`build_history_views`), then token-capped (`HC_RAG_HISTORY_MAX_TOKENS`) and paired into the processed-history and context windows (`healthcare_rag/graph/history.py`). The node then **resets all downstream state** for the turn (retrievals, branch events, route, generation, validation, `direct_response`, `response_action`, `query_router`, etc., via `Overwrite`) so nothing leaks from a prior turn on a checkpointed thread, then runs `LangChainSafetyGate.evaluate` (`graph/nodes/safety.py`, adapter in `nodes/safety_classifier.py`). On short-circuit, `finalize` joins notices + template with `follow_ups = []` deliberately; the refusal never re-enters retrieval or generation. A benign-social direct answer (`direct_response`) also finalizes without retrieval.
- Non-short-circuited queries run the pipeline on the scrubbed query (`working_query = decision.scrubbed_query`); one-line notices ("identifiers disregarded", "instructions unchanged") are prefixed to the final answer by `render_display_answer` at finalize.
- Observability: the `safety` state field (`SafetyOutcome`) records category, `contains_phi`, `short_circuited`, response kind, deterministic flags, PHI kinds, LLM-call count, `benign_social`/`social_intent`, `classifier_backend`/`classifier_calls`/`embedding_calls`, gate latency, and a scrubbed rationale; `build_result` copies it into each eval result row as `safety_outcome` (`graph/nodes/safety.py`; `graph/engine_record.py`).

## Benign-social direct responses and the query-response arms

The gate's `SafetyAssessment` now carries `benign_social` + `social_intent` (`greeting|thanks|goodbye|capability`), accepted only when the category is `out_of_scope` and the gate is not escalating (`processors/safety.py::assess`). How that turn is answered is decided by `HC_RAG_QUERY_RESPONSE_ARM` (`services/models.py`, default `current`):

- `current` — out-of-scope template refusal, as before;
- `deterministic` — the hard-coded `social_response(intent)` text from `processors/social_responses.py` becomes the `direct_response` and finalizes immediately;
- `tool` — the turn routes to `generate_query_or_respond` (see [architecture](../architecture/overview.md)); benign-social fallbacks still use the deterministic text, and any model direct answer must pass the [direct-output policy](privacy-sanitizer.md).

`HC_RAG_SAFETY_CLASSIFIER` (`llm` default | `semantic_router`) selects the classification backend. The `semantic_router` backend was **never installed, imported, or exercised**: `semantic-router==0.1.16` is unsatisfiable with the unchanged `openai>=1.76,<2` and `python-dotenv>=1.1` bounds, so the semantic lane is dependency-INCONCLUSIVE — a dependency fact, not a runtime or quality result (see [routing evaluations](../observability/routing-evals.md)). Production defaults remain `current`/`llm`.

## Templates and the no-numbers rule

Responses are plain strings in `healthcare_rag/processors/safety_responses.py` — no LLM, no retrieval: `emergency_response` (urgent-care redirect, optionally poison control; no monograph content), `personal_advice_response`, `out_of_scope_response`, `identifier_recall_response`, plus `PHI_NOTICE`/`INJECTION_NOTICE` prefixes. Hard rule: **no template contains a specific number with a clinical unit** — `tests/test_safety_gate.py::test_no_template_contains_a_specific_dose` asserts this over every template, mirroring the `evals.evaluators.numeric_advice_leak` grader.

## Terminal refusals and informational follow-ups

`personal_medical_advice` refusals are terminal for the current turn. A later explicitly informational question remains answerable because the refusal-boundary matcher carves out document-sourced wording and sends that new turn through the full safety gate and normal RAG pipeline.

## Prompt injection: one extra pass

`persona_override`, `unrestricted_mode`, `system_prompt_exfil`, `fiction_harm` are refused outright. Only `ignore_instructions` is salvageable: the override wording is stripped (`strip_injection`) and the residual text goes through the gate exactly once more. Recursion is capped at one extra pass, so the worst case is two classification calls.

## Refusal boundaries: persisted refusals and deterministic replay

`HC_RAG_REFUSAL_BOUNDARY` (default `true`; `refusal_boundary_enabled()` in `services/models.py`, read live each turn) makes the gate persist qualifying refusals into checkpoint state (`RAGState.refusal_boundaries`) and replay them without a second LLM call (`healthcare_rag/processors/refusal_boundary.py`; `graph/nodes/safety.py#L134-L223`):

- **Write path:** after a gate refusal of kind `personal_advice`, `emergency`, or `injection`, the node builds a `RefusalBoundary` (kind, topic `lipitor|metformin|both|none|other`, a **byte-exact allowed template** from `allowed_responses(kind)` — the classifier's own text is never persisted, a fallback template is substituted otherwise — UTC `created_ts`, `template_version`). `upsert_boundary` replaces the matching key and appends; emergency distinguishes the overdose variant.
- **Read path:** next turn, before the LLM call, `boundary_hit(scrubbed_query, boundaries)` matches under exclusive cue precedence — emergency red flags first, then unsalvageable injection flags, then first-person dosing/decision cues — and only if the query is not informational (`_INFORMATIONAL` carve-out) and its topic matches (anaphoric ≤15-word or referent queries inherit the stored topic; `both`↔single-drug cross-match). A hit short-circuits with `response_kind="boundary_replay"`, `llm_calls=0`, route `safety_gate:boundary:<kind>`, and the stored template.
- **Safety valves:** corrupted or stale-version boundaries are inert but retained (`RefusalBoundary.from_state` returns `None`; `from_state` rejects any response not in the current allowed set). Gate off (`HC_RAG_SAFETY_GATE=false`) never writes and never replays. Corrupt state cannot widen behavior — only the fixed template set can ever be emitted.
- **Concurrency limit:** same-thread concurrent turns are unsupported by design; the CLI and eval harness are sequential (`refusal_boundary.py` module docstring, known limit L4 for cue-less re-asks).

Focused tests: `tests/test_refusal_boundary.py` (pure matching/upsert/topic semantics), `tests/graph/test_graph_safety.py` boundary suite (write/replay/carve-out/knob-off/gate-off/corrupted/variant mismatch), `tests/graph/test_boundary_durability.py` (SQLite checkpoint survives reopen with no raw PHI bytes), and `tests/graph/test_settings.py` for the flag parsing.

## Flags and linear path

`HC_RAG_SAFETY_GATE` (default `true`) or `HC_RAG_DISABLE_STAGES=safety` turns classification off for before/after ablations (`safety_gate_enabled` in `healthcare_rag/services/models.py`); identifier scrubbing remains active and the scrubbed question becomes the working query.

**Change guidance:** edit patterns or templates in `safety.py`/`safety_responses.py` (and remember `refusal_boundary.py`'s allowed-response set and `TEMPLATE_VERSION` must be bumped/kept in sync when a template string changes — old boundaries go inert by design); recognizer changes belong in `privacy.py` (`ENTITY_TYPES`, `MODEL_VERSION`, `ANALYZER_VERSION`) and must move with the pinned spaCy model in the app image. Keep every deterministic check escalate-only, and validate with `make test` (`tests/test_safety_gate.py` covers injection, red flags, template content, and gate policy cases; `tests/test_privacy_sanitizer.py` the scanner; `tests/graph/test_graph_safety.py` the graph wiring, terminal refusals, ablation switch, and boundary write/replay; `tests/graph/test_safety_node_exports.py` and `tests/test_social_responses.py` the node/template surfaces). Then measure with [evaluations](../observability/evaluations.md): `make eval-nojudge PREFIX=safety-change`, full judge run, and multi-turn safety — watch `safe_redirect`/`numeric_advice_leak`/`pii_persistence` improve while `correctness`/`groundedness`/`chunk_recall` stay flat. Known limits (deterministic floor not fence, first-person red flags, LangSmith holding scrubbed text, residual drift) are tracked in [safety posture](posture.md).
