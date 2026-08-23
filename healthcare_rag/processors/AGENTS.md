<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# processors

## Purpose
The LLM-calling and pure-logic steps used by the graph nodes in
`healthcare_rag/graph/nodes/` (the nodes are the callers; this package holds
the actual work): retrieval (Weaviate + the Pinecone/PageIndex A/B arms +
reranking), document formatting, citation validation, PHI/PII scrubbing, the
runtime safety gate and its templated refusal responses, the persisted
refusal-boundary state machine, direct-output policy checks, and PDF
chunking for ingestion.

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | Re-exports `log_timing`, `AnswerValidator`, `SafetyGate`, `SafetyDecision`, `scrub_phi`. |
| `base.py` | `log_timing` — a decorator logging function execution time (instrumentation only). |
| `retrieval.py` | `build_routing_tools`, `to_query_documents`, `hybrid_search` (the default Weaviate retrieval arm), `union_results` (de-dupes by `doc_id` across sub-query branches). |
| `pageindex_retrieval.py` | `pageindex_search` — the `HC_RAG_RETRIEVER=pageindex` A/B arm; one LLM call (`select_nodes`) picks ≤`pageindex_max_nodes` tree nodes from a cached outline (`load_tree`), maps their page ranges back onto the same contextualized chunks (`load_chunks`, `select_chunks`); never imports the offline `pageindex` package. |
| `pinecone_retrieval.py` | `pinecone_search` — the `HC_RAG_RETRIEVER=pinecone` A/B arm; dense (OpenAI) + sparse (Pinecone Inference) hybrid via `convex_scale`; every call crosses `anyio.to_thread` since the Pinecone SDK is synchronous. |
| `rerank.py` | `rerank_documents` — the `HC_RAG_RERANKER=pinecone` cross-encoder reranking step; deliberately fail-soft (`reorder` falls back to the search's own ordering on a Pinecone Inference error). |
| `generation.py` | `format_documents_for_prompt` — builds the doc-context string and prompt-id-to-original-id map fed into the answer-generation prompt. |
| `validation.py` | `AnswerValidator` — orchestrates citation validation over `CitedAnswerResult`; the most expensive graph stage (see `graph/nodes/generate.py::validate_answer`). |
| `validation_citations.py` | `resolve_citation_ids`, `validate_citations_and_build_answer`, `_verify_quote` (fuzzy match via `_FuzzyProcess` protocol) — the actual per-statement citation verification logic. |
| `validation_source.py` | `find_source_citations`, `reconstruct_source_answer`, `SourceCitations`/`UnsafeSourceScaffold` — parses `[doc_N]` citation markers out of raw generated text and strips any leaked prompt-scaffold prefix. |
| `validation_rendering.py` | `format_statement`, `convert_linebreaks`, `join_statements`, `FALLBACK_MESSAGE` — pure text-rendering helpers for the final validated answer. |
| `direct_output_policy.py` | `evaluate_generated_output` — decides whether a direct (non-retrieval) model response is safe to show as-is (`GeneratedOutputDenial`: `clinical_direct_content` / `privacy_error` / `unsafe_direct_content`), including social-only-sentence and capability-atom detection. |
| `safety.py` | `SafetyGate` (base class; `_llm_assess` is overridden per-runtime by `graph.nodes.safety_classifier.LangChainSafetyGate` and `agent.gate.CoachSafetyGate`), `SafetyDecision` — the core two-layer (deterministic regex OR-ed with LLM classification) safety gate logic; PHI scrubbing runs first and unconditionally, independent of the classification switch. |
| `safety_patterns.py` | `injection_flags`, `strip_injection` — regex patterns for prompt-injection / persona-override attempts. |
| `safety_signals.py` | `scrub_phi`, `contains_phi`, `identifier_recall_requested`, `red_flag_terms` — deterministic signal extraction from untrusted message text; the module `safety.py` builds on. |
| `safety_responses.py` | Plain-string refusal templates only — **no LLM call, no retrieval**: `emergency_response`, `personal_advice_response`, `out_of_scope_response`, `identifier_recall_response`, `injection_response`. Hard rule enforced by `tests/test_safety_gate.py`: never put a specific number with a clinical unit in any template here. |
| `refusal_boundary.py` | `RefusalBoundary`, `boundary_hit`, `upsert_boundary`, `load_boundaries`, `allowed_responses`, `derive_boundary_topic` — the pure persisted-refusal state machine (`HC_RAG_REFUSAL_BOUNDARY`); same-thread concurrent turns are unsupported and must be serialized by the caller. |
| `refusal_topics.py` | `query_topic`, `derive_boundary_topic` — drug-topic classification (`lipitor` / `metformin` / `both` / `none` / `other`) feeding the refusal boundary. |
| `social_responses.py` | `social_response`, `social_arm_output`, `default_social_arm_output`, `SocialArmOutput` — canned replies for greetings/thanks/scope questions in the `query_or_respond` direct path. |
| `privacy.py` | `PrivacySanitizer`, `PrivacyScan`, `PrivacyScanError`, `Readiness` — the Presidio-backed PII/PHI analyzer wrapper used across the coach agent (`agent/store_data.py`, `agent/documents.py`, etc.). |
| `privacy_patterns.py` | `deterministic_hits`, `clinical_code_intervals`, `PatternHit` — the regex layer `privacy.py` combines with the Presidio NLP engine. |
| `pdf_chunker.py` | `DocumentChunkProcessor`, `run_chunker`, `get_page_numbers` — docling/transformers-based PDF-to-chunk pipeline for ingestion (`cli/ingestion.py`'s `process_pdf`). Heavy import chain (docling, transformers) — never import from a hot runtime path. |

## For AI Agents

### Working In This Directory
- `safety.py` + `safety_patterns.py` + `safety_signals.py` +
  `safety_responses.py` together are "the safety gate" described in the root
  `AGENTS.md` — any behavior change here must be checked against
  `evals/golden_dataset.json`'s safety categories, not just unit tests.
  **No template in `safety_responses.py` may contain a number with a clinical
  unit.**
- Privacy sanitization (`privacy.py`/`privacy_patterns.py`) always runs first
  and is independent of whether the LLM safety classification is enabled —
  don't make PHI scrubbing conditional on `HC_RAG_SAFETY_GATE`.
- `refusal_boundary.py` is intentionally pure (no I/O, no LangGraph imports)
  so it's independently testable; callers (graph and agent) own
  serialization/persistence.
- `pageindex_retrieval.py` and `pinecone_retrieval.py` must mirror
  `hybrid_search`'s call signature exactly so `graph/nodes/retrieve.py`'s
  `resolve_arm` can swap them in transparently — first argument accepted but
  unused when it doesn't apply (e.g., the Weaviate client for the Pinecone
  arm).
- `rerank.py` must stay fail-soft: never let a Pinecone Inference outage
  turn into a hard retrieval failure — fall back to the search's own
  top-k ordering.
- `pdf_chunker.py` pulls in docling/torch/transformers/easyocr; keep it
  imported only from `cli/ingestion.py`, never from the chat runtime.

### Testing Requirements
- `tests/test_safety_gate.py`, `tests/test_refusal_boundary.py` — safety gate
  and refusal-boundary behavior/templates.
- `tests/test_privacy_sanitizer.py`, `tests/test_tracing_privacy.py` — privacy layer.
- `tests/test_pageindex_retrieval.py`, `tests/test_pinecone_retrieval.py`, `tests/test_rerank.py` — A/B retrieval arms.
- `tests/test_answer_validation.py`, `tests/test_validation_scaffold_prefix.py` — validation modules.
- `tests/test_social_responses.py` — `social_responses.py`.
- `tests/graph/test_direct_output_policy.py` — `direct_output_policy.py`.
- `tests/test_vector_store.py` — indirectly exercises chunking output shape consumed downstream.

### Common Patterns
- Every module here that touches user-visible or logged text runs it through
  `scrub_phi` (from `safety_signals.py`, re-exported via `safety.py`) at the
  boundary.
- Regex-heavy modules (`safety_patterns.py`, `privacy_patterns.py`,
  `refusal_topics.py`) define patterns as module-level `Final` constants,
  compiled once.

## Dependencies

### Internal
- Consumed by `healthcare_rag/graph/nodes/*` and `healthcare_rag/agent/*`.
- `models/` (Pydantic response models: `SafetyAssessment`, `RetrievalEvaluation`, `CitedAnswerResult`, `QueryDocument`/`QueryResultList`).
- `services/models.py` (model selection for validation/rerank LLM calls).

### External
- `presidio_analyzer` (PII/PHI NER), `weaviate` (hybrid search types), `pinecone` (retrieval/rerank arms), `rapidfuzz`/`thefuzz`-style fuzzy matching (`validation_citations.py`'s `_FuzzyProcess`), `docling`/`transformers` (`pdf_chunker.py` only), `langsmith` (nested rerank/retrieve tracing).

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
