---
type: validation component
title: Answer structuring and citation verification
description: How raw answers become structured cited statements, how leaked prompt scaffolding and unsupported citations are removed, and the exact fallback outcomes.
tags: [validation, citations, safety]
openwiki:
  roles: [domain, testing]
  change_kinds: [safety, public-api]
  source_paths: [healthcare_rag/processors/validation.py, healthcare_rag/processors/validation_citations.py, healthcare_rag/processors/validation_source.py, healthcare_rag/processors/validation_rendering.py, healthcare_rag/models/answers.py]
  symbols: [AnswerValidator, structure_and_validate_async, resolve_citation_ids, validate_citations_and_build_answer, find_source_citations, reconstruct_source_answer, SourceCitations, UnsafeSourceScaffold, format_statement, join_statements, FALLBACK_MESSAGE]
  test_paths: [tests/test_answer_validation.py, tests/test_validation_scaffold_prefix.py]
  invariants: [A generated answer whose text begins with leaked prompt/system scaffolding is rejected wholesale and replaced with FALLBACK_MESSAGE, never partially rendered., A statement with citations survives only if at least one citation passes fuzzy/exact quote verification against the retrieved documents; a statement with no citations is kept as-is., validate_answer must never fail open: any exception in structure_and_validate_async is treated by the graph node as (None, None).]
  validation_commands: [make test]
---

# Answer structuring and citation verification

`AnswerValidator.structure_and_validate_async` (`healthcare_rag/processors/validation.py`) takes the generated plain answer plus its exact retrieval bundle and temporary prompt-ID map. It is a stronger model tier than generation; its structured output is `CitedAnswerResult(statements=[StatementWithCitations])`, where each citation has `doc_id`, `source_name`, and `quote` (`healthcare_rag/models/answers.py`). The structuring prompt asks for verbatim answer segments and citations based on `[doc_N]` markers (`prompts/answer_structuring.yaml.j2`).

`validation.py` is a thin orchestrator over three collaborator modules:

- **`validation_source.py`** — `find_source_citations` scans the **raw plain answer text** (not the LLM's structured output) for `[doc_N]` citation groups and classifies it as `SourceCitations` (markers found), `None` (no markers), or `UnsafeSourceScaffold` when the answer text itself starts with leaked prompt/system scaffolding (`"Documents for citation context:"`, `"Document ID:"`, or a `"SYSTEM"` line). `reconstruct_source_answer` rebuilds statements by slicing the plain answer around each citation group and attaching the matching structured citations — it only succeeds if the structured answer's citation IDs exactly match the IDs found in the raw text, which detects prompt/response tampering between generation and structuring.
- **`validation_citations.py`** — `resolve_citation_ids` replaces each temporary `doc_N` with its original document UUID using `prompt_id_map`; `validate_citations_and_build_answer` runs per-statement fuzzy/exact quote verification (`_FuzzyProcess` wraps `fuzzywuzzy.process.extractOne`) and renders the final answer string.
- **`validation_rendering.py`** — pure text helpers: `format_statement` (strip old `[doc_N]` markers, append sorted/deduplicated valid ones), `convert_linebreaks`, `join_statements`, and the `FALLBACK_MESSAGE` constant shared by every failure path.

```mermaid
flowchart TD
  P["plain answer plus formatted docs"] --> S["LLM structure -> CitedAnswerResult"]
  S --> F1{"find_source_citations(plain_answer)"}
  F1 -->|"UnsafeSourceScaffold"| FB["FALLBACK_MESSAGE (whole answer rejected)"]
  F1 -->|"no markers"| FB
  F1 -->|"SourceCitations"| R["reconstruct_source_answer: raw text markers must match structured citation IDs"]
  R -->|"mismatch"| FB
  R -->|"match"| M["resolve_citation_ids: doc_N -> Weaviate UUID"]
  M --> C["find retrieved document"]
  C --> Q["exact quote or fuzzy score >= 85"]
  Q -->|"one valid citation"| K["keep statement, append valid doc_N markers"]
  Q -->|"all citations invalid"| D["drop statement"]
  K --> O["join kept statements"]
  D --> O
  O -->|"none kept"| FB
```

This is the implemented transformation; it does not assess medical truth beyond quote-to-retrieved-document support.

## Exact rules

1. Missing `plain_answer`, retrieval results, formatted docs, or ID map returns `(None, None)` before any LLM call.
2. The structuring LLM may fail/parse to `None`; that also returns `(None, None)`.
3. **Scaffold defense (fail closed):** if the raw plain answer text begins with `"Documents for citation context:"`, `"Document ID:"`, or a `SYSTEM` line — i.e. the model echoed leaked prompt/system scaffolding instead of a real answer — the whole answer is replaced with `FALLBACK_MESSAGE`, regardless of what the structuring LLM returned. Covered by `tests/test_validation_scaffold_prefix.py::test_generated_scaffold_prefix_fails_closed`.
4. If the raw answer has no `[doc_N]` markers at all, or the structured citation IDs do not exactly match the markers found in the raw text (`reconstruct_source_answer` returns `None`), the answer also falls back to `FALLBACK_MESSAGE` — this guards against the structuring step silently inventing or dropping citations relative to what was actually generated.
5. `resolve_citation_ids` replaces each temporary `doc_N` with its original document UUID using `prompt_id_map`. An unknown ID remains unresolvable and later fails document lookup.
6. For each citation, the validator finds the UUID among **the retrieved documents only**. A quote passes if it is an exact substring or `fuzzywuzzy.process.extractOne` meets the threshold (default `85`) against that document's entire content.
7. A statement with citations survives if **at least one** citation passes; failed citations are removed. A statement with **no citations is considered valid and is kept**. A statement with citations where all fail is dropped.
8. Kept text has existing `[doc_N]` markers removed, then sorted/deduplicated valid markers are appended. Only `\n`, `\n\n`, and `\n\n\n` linebreak values are converted; unexpected values become no break (`validation_rendering.convert_linebreaks`).

If no statements survive — or there were no structured statements, or the scaffold/marker-mismatch defenses fired — the returned text is exactly: `I'm sorry, I couldn't validate the information to answer your question.` (`FALLBACK_MESSAGE`). That string is truthy, so a normal turn completes with it. Missing inputs or structuring failure yield no text; the graph node `validate_answer` wraps the call so any exception also yields `(None, None)` (validation must never fail open), and every kept statement/citation field is additionally run through `scrub_phi` before it lands in state (`graph/nodes/generate.py`; see [privacy sanitizer](../privacy/sanitizer.md)). `fold_branches` marks the selected branch FAILED when `validated` is `None` (`graph/engine_record.py`). With `HC_RAG_DISABLE_STAGES=validate` the node passes the **unvalidated** (scrubbed) plain answer through instead — that is ablation machinery, not a production setting.

## Safe changes

Changing prompt marker format requires changing `format_documents_for_prompt`, `prompt_id_map`, resolution in `validation_citations.py`, and marker cleaning/sorting in `validation_rendering.py` together. Changing the scaffold-prefix pattern in `validation_source.py` changes what counts as leaked prompt text — keep it conservative enough not to reject legitimate answers that happen to mention "system" or "document" (see `test_source_reconstruction_keeps_benign_system_and_document_prose`). Changing the fuzzy threshold changes acceptance sensitivity but does not make uncited statements fail. A stricter product-safety policy requires an explicit code change to the no-citation path, not merely template language. This distinction is central to [safety posture](../safety/posture.md).

**Focused validation:** `tests/test_answer_validation.py` and `tests/test_validation_scaffold_prefix.py` exercise `structure_and_validate_async` directly (invalid UUID, quote below threshold, mixed valid/invalid citations, uncited statement, no surviving statement, and the three scaffold-prefix variants) without any live LLM call. Use a golden answer case plus `make eval-nojudge PREFIX=validation-change`; then run judge evaluation because groundedness measures claims against retrieved context.
