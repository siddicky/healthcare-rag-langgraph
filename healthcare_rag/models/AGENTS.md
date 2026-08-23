<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# models

## Purpose
Pydantic models shared across the graph, processors, and prompts — these are
the "response format" contracts each LLM call site in `healthcare_rag/prompts/`
is rendered against (the first line comment of each prompt `.yaml.j2` file
states which model here it targets), plus a few pure data-transfer models used
internally (retrieval results, safety outcomes).

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | Re-exports the commonly used models from every submodule for convenience (`ClarifiedQuery`, `DecomposedQuery`, `RetrievalEvaluation`, `QueryDocument`, `QueryResult`, `QueryResultList`, `Citation`, `StatementWithCitations`, `CitedAnswerResult`, `AnswerGenerationResult`, `RelevantHistoryContext`, `ConversationEntry`, `FollowUpQuestions`, `SafetyAssessment`, `SafetyCategory`, `SafetyOutcome`). |
| `queries.py` | `ClarifiedQuery` (with a `model_validator` forcing `clarified_query == original_query` when ambiguity is "clear and specific"), `DecomposedQuery`, `RetrievalEvaluation` — response models for `clarify_query`, `decompose_query`, `retrieval_evaluation` prompts. |
| `answers.py` | `Citation`, `StatementWithCitations`, `CitedAnswerResult` (response model for `answer_structuring`), `AnswerGenerationResult`, `RelevantHistoryContext` (response model for `context_extraction`). |
| `retrieval.py` | `QueryDocument`, `QueryResult`, `QueryResultList` — the retrieval result shapes serialized to/from graph state via `graph.state.dump_results`/`load_results`; `ErrorResult`; `PageIndexSelection` (response model for `pageindex_select`). |
| `misc.py` | `ConversationEntry`, `FollowUpQuestions` (response model for `follow_up_questions`). |
| `safety.py` | `SafetyCategory` (`Literal` of the six gate categories), `SafetyAssessment` (response model for `safety_gate` prompt — structured LLM output), `SafetyOutcome` (the observability record exposed as `orchestrator.safety_outcome`, **never** sent to a model). |

## For AI Agents

### Working In This Directory
- Change a prompt and its Pydantic response model together — the repo
  convention (see root `AGENTS.md`) — and check `graph/prompts.py`'s
  `STAGE_FILES` mapping stays consistent with whichever model a prompt
  targets.
- `SafetyAssessment` vs `SafetyOutcome` are easy to conflate: `SafetyAssessment`
  is model-facing structured output; `SafetyOutcome` is a downstream,
  never-sent-to-a-model record built from it plus deterministic checks.
- `ClarifiedQuery`'s validator is a safety net against a model returning a
  "no clarification needed" verdict alongside an edited `clarified_query` —
  don't remove it without checking `tests/graph/` clarify-path tests.

### Testing Requirements
- No dedicated `tests/models/` directory; these models are exercised
  indirectly through `tests/graph/*.py` (node tests) and
  `tests/test_answer_validation.py`, `tests/test_safety_gate.py`,
  `tests/test_refusal_boundary.py`.

### Common Patterns
- Every model is a plain `pydantic.BaseModel` with `Field(description=...)`
  on every field — those descriptions are part of the structured-output
  schema the model sees, not just documentation; keep them accurate and
  written for the LLM as the audience.

## Dependencies

### Internal
- Consumed by `healthcare_rag/graph/prompts.py` (response-model rendering), `healthcare_rag/graph/nodes/*`, `healthcare_rag/processors/*`.

### External
- `pydantic` (`BaseModel`, `Field`, `model_validator`).

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
