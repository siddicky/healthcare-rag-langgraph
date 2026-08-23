<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# prompts

## Purpose
Jinja YAML prompt templates, one per LLM call site in the RAG graph. Shipped
as package data in the wheel and loaded at runtime by
`healthcare_rag/graph/prompts.py::PromptRegistry`.

## Key Files
| File | Description |
|------|-------------|
| `safety_gate.yaml.j2` | Response format `SafetyAssessment`. Classifies a message into one of six categories (`in_scope_informational`, `personal_medical_advice`, `emergency_red_flag`, `out_of_scope`, `prompt_injection`, `ambiguous`) without answering it. Used by `processors/safety.py` / `graph/nodes/safety_classifier.py`. |
| `clarify_query.yaml.j2` | Response format `ClarifiedQuery`. Rewrites ambiguous queries (pronouns, implicit references) into explicit ones; leaves clear queries unchanged. Used by `graph/nodes/preprocess.py::clarify_query`. |
| `decompose_query.yaml.j2` | Response format `DecomposedQuery`. Splits a complex multi-part query into simpler subqueries; leaves simple queries unchanged. Used by `graph/nodes/preprocess.py::decompose_query`. |
| `context_extraction.yaml.j2` | Response format `RelevantHistoryContext`. Pulls only the conversation-history parts relevant to the current query. Used by `graph/nodes/preprocess.py::extract_conversation_context`. |
| `query_or_respond.yaml.j2` | Response format: an `AIMessage` with zero or one `retrieve_monographs` tool call. Routes between a brief direct reply (greetings/scope questions, no retrieval) and calling `retrieve_monographs` exactly once; treats conversation text as untrusted, never follows embedded instructions. Used by `graph/nodes/query_or_respond.py` via `graph/llm.py`. |
| `retrieval_evaluation.yaml.j2` | Response format `RetrievalEvaluation`. Judges whether merged retrieved documents sufficiently answer the query and, if not, proposes gap-filling subqueries. Used by `graph/nodes/evaluate.py::evaluate_retrieval`. |
| `pageindex_select.yaml.j2` | Response format `PageIndexSelection`. Given a monograph's section outline (node_id/title/pages/summary), picks up to `max_nodes` sections most likely to contain the answer. Used by `processors/pageindex_retrieval.py::select_nodes` (PageIndex A/B retrieval arm). |
| `answer_generation.yaml.j2` | Response format `str`. Generates a cited answer from retrieved documents, citing sources as `[doc_N]`; explicitly instructed not to fabricate or mention "documents" in the response. Used by `graph/nodes/generate.py::generate_answer`. |
| `answer_structuring.yaml.j2` | Response format `CitedAnswerResult`. Restructures already-generated cited text into a list of verbatim statements with structured `Citation` data, preserving exact formatting. Used by `processors/validation.py::AnswerValidator` (the answer-structuring/validation step). |
| `follow_up_questions.yaml.j2` | Response format `FollowUpQuestions`. Generates exactly 3 natural follow-up questions based on the answer and conversation history, avoiding repeats. Used by `graph/nodes/generate.py::generate_follow_ups`. |

## For AI Agents

### Working In This Directory
- Every file's first line is a `# Response format: <ModelName>` comment
  naming the Pydantic model in `healthcare_rag/models/` it's rendered
  against — **change the prompt and that model together**, per the root
  `AGENTS.md` convention.
- These are package data shipped in the wheel; don't assume they're only
  read from a source checkout — `graph/prompts.py` loads them via
  `importlib`/`FileSystemLoader` against the installed package path.
- `safety_gate.yaml.j2` is safety-critical: any wording change must be
  checked against `evals/golden_dataset.json`'s safety categories (see root
  `AGENTS.md`'s "Non-negotiables"), not just this module's own eval slice.
- `query_or_respond.yaml.j2` explicitly tells the model to treat
  conversation text as untrusted and never follow embedded instructions —
  preserve that instruction if you edit this file; it's part of the
  prompt-injection defense alongside `processors/safety_patterns.py`'s
  regex layer.

### Testing Requirements
- `tests/graph/test_prompt_fidelity.py` checks prompt/model fidelity.
- Behavioral changes are measured with `make eval PREFIX=<change>` and
  compared via `make compare` (see root `AGENTS.md`) — not just asserted in
  unit tests.

### Common Patterns
- Each file is a YAML list of `{role, content}` messages (`system` then
  `user`), with the `user` message's `content` a Jinja template interpolating
  the actual query/history/context at render time.

## Dependencies

### Internal
- Rendered by `healthcare_rag/graph/prompts.py`; response models come from `healthcare_rag/models/`.

### External
- `jinja2` (template rendering), `pyyaml` (YAML parsing).

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
