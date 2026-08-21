---
type: security component
title: Presidio privacy sanitizer and direct-output policy
description: Fail-closed PHI scanning with Presidio that backs scrub_phi everywhere, the deterministic clinical-code patterns, the input-size limit, and the policy that gates tool-arm direct answers.
tags: [safety, privacy, pii, presidio]
openwiki:
  roles: [architecture, domain, testing]
  change_kinds: [lifecycle, public-api]
  source_paths: [healthcare_rag/processors/privacy.py, healthcare_rag/processors/privacy_patterns.py, healthcare_rag/processors/direct_output_policy.py, healthcare_rag/graph/resources.py]
  symbols: [PrivacySanitizer, PrivacyScanError, scrub_phi, evaluate_generated_output, union_spans, MAX_INPUT_BYTES, Readiness]
  test_paths: [tests/test_privacy_sanitizer.py, tests/graph/test_graph_privacy.py, tests/graph/test_graph_privacy_persistence.py, tests/graph/test_direct_output_policy.py, tests/graph/test_validation_privacy.py]
  invariants: [The sanitizer fails closed: an initialization or scan error raises PrivacyScanError and aborts the turn instead of passing raw text through.,Model-reported phi_spans never mutate text; only the local sanitizer has text-mutation authority.,Any direct (non-retrieval) model answer must pass evaluate_generated_output; clinical instructions, dose numbers, injection wording, or identifier hits yield an empty denial.]
  validation_commands: [make test]
---

# Presidio privacy sanitizer and direct-output policy

`scrub_phi` in `healthcare_rag/processors/safety.py` no longer runs a local regex list: it delegates to the process-wide `PrivacySanitizer` (`healthcare_rag/processors/privacy.py`) owned by `Resources.privacy` (`graph/resources.py`). Every scrub site — the [safety gate](gate.md), retrieval queries, generated answers, validation output, finalize persistence — goes through this one scanner.

## Scanner design (fail-closed)

* `PrivacySanitizer.initialize()` builds a Presidio `AnalyzerEngine`: spaCy `en_core_web_sm` NLP engine (`MODEL_VERSION` pinned), `LemmaContextAwareEnhancer`, `SpacyNlpEngine` with NER labels filtered by `_SPACY_IGNORED_LABELS`, and a registry of predefined recognizers covering `ENTITY_TYPES` (SIN, credit card, crypto, IBAN, IP, MAC, medical license, phone, person, URL, US bank/ITIN/license/MBI/NPI/passport/SSN) at `DEFAULT_SCORE_THRESHOLD = 0.40`. Readiness is a `Condition`-guarded state machine (`UNINITIALIZED → INITIALIZING → READY | FAILED`); concurrent initializers wait, and `GraphEngine._initialize` calls `privacy.initialize()` at engine startup so a failed init fails the turn, not silently disables scrubbing. Any initialization or scan failure becomes `PrivacyScanError` (stable codes like `PRIVACY_NOT_READY`, `PRIVACY_SCAN_FAILED`, `PRIVACY_INPUT_TOO_LARGE`) — `GraphEngine._run` records the code as the turn's error.
* Deterministic hits from `processors/privacy_patterns.py` (`deterministic_hits`, clinical-code intervals via `clinical_code_intervals`) are unioned with analyzer results (`union_spans`; mixed-kind overlaps become `IDENTIFIER`), preserving clinical codes that Presidio would otherwise redact. Redactions replace text with `[REDACTED_<KIND>]` tokens, as before.
* `MAX_INPUT_BYTES = 16 KiB` caps every scanned text — both in `PrivacySanitizer.scan` and in the router paths (`graph/llm.py::_scrub_router_text`) — oversized input raises rather than truncates.
* The old `scrub_phi(text, extra_spans)` second argument is retained but **ignored**: model-reported `phi_spans` never receive text-mutation authority. `contains_phi` still reports kinds found.

## Direct-output policy (`processors/direct_output_policy.py`)

The `tool` query-response arm (see [architecture](../architecture/overview.md)) can let the model answer without retrieval; before any such text is shown, `evaluate_generated_output` gates it. NFKC-normalized, casefolded text is denied with reason:

* `privacy_error` — over `MAX_INPUT_BYTES` or any scrub hit;
* `unsafe_direct_content` — `injection_flags` matches;
* `clinical_direct_content` — a `NUMERIC_DOSE` number-with-unit match, a `_CLINICAL_UNITS` token, or the `_CLINICAL_ACTIONS ∩ _CLINICAL_TARGETS` instruction pattern (advise/take/double… × atorvastatin/metformin/dose/doctor…).

Only a clean pass returns `(scrubbed_content, None)`; denials return empty content plus the reason, which the gateway maps to a `fallback_reason` (`QueryOrRespondDecision`). History entering the tool router is also projected through `_project_history` (human/ai string content only, each message scrubbed and size-checked).

## Deployment note

Presidio needs the pinned spaCy model (`MODEL_NAME`/`MODEL_VERSION`/`ANALYZER_VERSION` constants are the compatibility contract). `make container-build` bakes the model into the app image and `make container-run` runs the privacy-safe CLI from it — see [runbook](../operations/runbook.md).

**Focused validation:** `make test` (`tests/test_privacy_sanitizer.py` for span union/readiness/error codes, `tests/graph/test_graph_privacy.py` + `test_graph_privacy_persistence.py` for graph-level fail-closed behaviour, `tests/graph/test_direct_output_policy.py` for denial reasons, `tests/graph/test_query_or_respond_privacy.py` for the router boundary, `tests/graph/test_validation_privacy.py` for validated-answer scrubbing).
