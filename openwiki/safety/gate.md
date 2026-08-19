---
type: runtime component
title: Runtime safety gate
description: First-touch classifier and PHI scrubber that refuses personal medical advice, emergencies, out-of-scope, and injection attempts with templated responses before the RAG pipeline runs.
tags: [safety, gate, pii, prompt-injection]
openwiki:
  roles: [architecture, domain, testing]
  change_kinds: [lifecycle, public-api]
  source_paths: [healthcare_rag/processors/safety.py, healthcare_rag/processors/safety_responses.py, prompts/safety_gate.yaml.j2, healthcare_rag/models/safety.py, healthcare_rag/orch/orchestrator.py]
  symbols: [SafetyGate, SafetyDecision, scrub_phi, SafetyAssessment, SafetyOutcome, assess_safety]
  test_paths: [tests/test_safety_gate.py]
  invariants: [Deterministic pre-checks can only escalate a decision, never relax it., A refusal template never contains a number with a clinical unit., The scrubbed query is what reaches retrieval, prompts, and history persistence.]
  validation_commands: [make test]
---

# Runtime safety gate

Every query now passes a runtime gate **before** retrieval or generation (`healthcare_rag/orch/orchestrator.py#L126-L169`). It exists because the baseline had no runtime guard at all: `safe_redirect` scored 0.00–0.33 across every model configuration, a "should I double my metformin tonight?" question returned a dosing table, and out-of-scope questions returned nothing (journey findings F13/F18; `docs/safety.md`, `docs/journey.json`). Full policy doc: `docs/safety.md`; measured impact: `docs/baseline-report.md` §6 and [safety posture](posture.md).

## Design: two layers, OR-ed

1. **Deterministic pre-checks** (no network, `healthcare_rag/processors/safety.py`): regexes for PHI (`_PHI_PATTERNS`: email, health card, MRN, DOB, phone, postal, address, cued names), instruction-override attempts (`injection_flags`), requests to recite identifiers back (`identifier_recall_requested`), and first-person emergency red flags (`red_flag_terms`). These are a **floor** — they can only escalate an outcome (force `emergency_red_flag`/`prompt_injection`, set `contains_phi`), never downgrade what the model chose.
2. **One LLM classification call** (`prompts/safety_gate.yaml.j2` → `SafetyAssessment` in `healthcare_rag/models/safety.py`, temperature 0, default model): one of `in_scope_informational`, `personal_medical_advice`, `emergency_red_flag`, `out_of_scope`, `prompt_injection`, `ambiguous`, plus `phi_spans`, `drug_mentioned`, and a `safe_reformulation`. If this call fails, the deterministic layer still decides (fail-open only for classification, exactly what the pre-gate pipeline did).

`SafetyGate.assess` merges both (`#L560-L592`); `SafetyGate.evaluate` returns a `SafetyDecision` carrying the scrubbed query, template choice, addendum query, and one-line notices (`#L594-L620`, `#L494-L526`).

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart TD
  Q["user message"] --> P["deterministic pre-checks: PHI / injection / identifier recall / red flags"]
  Q --> L["one LLM classification: SafetyAssessment"]
  P --> M{"merge: red flag > injection > identifier recall > LLM"}
  L --> M
  M --> S["scrub_phi: query, history context, stored history"]
  S --> C{"category?"}
  C -->|emergency / personal advice / out-of-scope / injection| T["templated refusal + redirect, follow-ups = []"]
  C -->|in_scope / ambiguous| N["normal pipeline on the SCRUBBED query"]
  T --> A{"safe_reformulation survives dosing + numeric checks?"}
  A -->|yes| AD["append general-information addendum via sub-orchestrator"]
  A -->|no| R["refusal alone"]
```

## Scrubbing and short-circuit wiring

- `scrub_phi(text, extra_spans)` replaces identifiers with `[REDACTED_<KIND>]` tokens, merging model-reported `phi_spans` (tried raw, HTML-escaped, and unescaped, because Jinja autoescaping shows the model `O&#39;Brien`) with regex hits, keeping the longest of overlapping spans (`#L149-L218`). A denylist stops drug/patient words from ever being redacted.
- The orchestrator scrubs history context and each history entry before it reaches any prompt, then runs the traced `safety_gate` stage (`healthcare_rag/orch/tasks.py#L192-L241`). On short-circuit, `_respond_from_policy` renders the template, persists the **scrubbed** query, sets the monitor's raw-answer event so a UI does not stall, and returns `follow_ups = []` deliberately — suggesting follow-ups under a refusal undoes the refusal (`healthcare_rag/orch/orchestrator.py#L215-L250`).
- Non-short-circuited queries run the pipeline on the scrubbed query; one-line notices ("identifiers disregarded", "instructions unchanged") are prefixed to the final answer via `SafetyDecision.prefix_notices` (`#L187-L189`).
- Observability: `orchestrator.safety_outcome` (`SafetyOutcome`) records category, flags, response kind, gate latency, addendum appended; the eval harness copies it into each result row as `safety_outcome` (`evals/harness.py#L238-L257`).

## Templates and the no-numbers rule

Responses are plain strings in `healthcare_rag/processors/safety_responses.py` — no LLM, no retrieval: `emergency_response` (urgent-care redirect, optionally poison control; no monograph content), `personal_advice_response`, `out_of_scope_response`, `identifier_recall_response`, plus `PHI_NOTICE`/`INJECTION_NOTICE` prefixes. Hard rule: **no template contains a specific number with a clinical unit** — `tests/test_safety_gate.py::test_no_template_contains_a_specific_dose` asserts this over every template, mirroring the `evals.evaluators.numeric_advice_leak` grader.

## General-information addendum

For `personal_medical_advice`, the classifier's `safe_reformulation` may be answered and appended under `ADDENDUM_HEADING`, subject to two gates (`healthcare_rag/processors/safety.py#L459-L487`): the reformulation must not itself be a dosing question (`DOSING_QUESTION`), and the generated answer must not contain a dose-shaped number (`addendum_is_safe` / `NUMERIC_DOSE`). It runs in a throw-away sub-orchestrator with `skip_safety_gate = True` and an empty user id, so it never hits the user's history; branches are copied back for tracing (`orchestrator.py#L252-L270`).

## Prompt injection: one extra pass

`persona_override`, `unrestricted_mode`, `system_prompt_exfil`, `fiction_harm` are refused outright. Only `ignore_instructions` is salvageable: the override wording is stripped (`strip_injection`) and the residual text goes through the gate exactly once more. Recursion is capped at one extra pass, so the worst case is two classification calls.

## Flags and linear path

`HC_RAG_SAFETY_GATE` (default `true`) or `HC_RAG_DISABLE_STAGES=safety` turns the gate off for before/after ablations (`safety_gate_enabled` in `healthcare_rag/services/models.py#L173-L179`). `MedicalRAG.process_query_simple` runs the same gate and templates with the same scrubbing, but never attaches the addendum (`healthcare_rag/pipeline/medical_rag.py#L140-L156`).

**Change guidance:** edit patterns or templates in `safety.py`/`safety_responses.py`, keep every deterministic check escalate-only, and validate with `make test` (`tests/test_safety_gate.py` covers PHI detection/idempotence, injection, red flags, template content, gate policy cases, orchestrator short-circuit/scrubbed-history/addendum drop, and the ablation switch). Then measure with [evaluations](../observability/evaluations.md): `make eval-nojudge PREFIX=safety-change`, full judge run, and multi-turn safety — watch `safe_redirect`/`numeric_advice_leak`/`pii_persistence` improve while `correctness`/`groundedness`/`chunk_recall` stay flat. Known limits (regex floor not fence, first-person red flags, LangSmith holding scrubbed text, residual drift) are tracked in [safety posture](posture.md).
