<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# services

## Purpose
Centralized model selection/sampling and optional LangSmith tracing — the
seam that lets the pipeline's model choices, A/B retrieval/reranker backends,
and stage-disable knobs all be controlled from the environment without
touching call sites.

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | Package docstring only, no exports. |
| `model_sampling.py` | Lower-level model/sampling primitives: `DEFAULT_LLM_MODEL` (`gpt-5.6-luna`), `DEFAULT_VALIDATOR_MODEL` (`gpt-5.6-terra`), `DEFAULT_REASONING_EFFORT` (`"none"`), `default_llm_model`/`default_validator_model`/`default_reasoning_effort` (env-overridable getters), `is_reasoning_model`, `sampling_params(model, temperature, reasoning_effort)` — turns a "desired temperature" into whatever the selected model actually accepts. |
| `models.py` | The full config surface built on `model_sampling.py`: `disabled_stages`/`stage_enabled` (`HC_RAG_DISABLE_STAGES`), `safety_gate_enabled` (`HC_RAG_SAFETY_GATE`), `refusal_boundary_enabled` (`HC_RAG_REFUSAL_BOUNDARY`), `max_subqueries`/`decompose_only_complex` (`HC_RAG_MAX_SUBQUERIES`, `HC_RAG_DECOMPOSE_ONLY_COMPLEX`), `query_response_arm` (`QueryResponseArm`), `safety_classifier_backend`, `retriever_backend`/`reranker_backend` (the Weaviate/PageIndex/Pinecone A/B knobs — `HC_RAG_RETRIEVER`, `HC_RAG_RERANKER`), `pageindex_max_nodes`/`pageindex_max_chunks`, `rerank_candidates`/`rerank_top_k`/`rerank_model`, `pinecone_index_name`/`pinecone_sparse_model`/`pinecone_alpha`, `embedding_model`. |
| `tracing.py` | Optional LangSmith tracing, a hard no-op when disabled (no runtime dependency on the `langsmith` package): `tracing_enabled`, `enforce_input_hiding` (requires `LANGSMITH_HIDE_INPUTS=true`), `wrap_openai_client`, `traceable`, `rag_stage`, `query_result_list_to_documents`. |

## For AI Agents

### Working In This Directory
- **GPT-5.x reasoning models reject `temperature`/`top_p` unless
  `reasoning_effort="none"`.** Every call site must go through
  `sampling_params()` — never hard-code `temperature=` on a chat-completion
  call. This is the root `AGENTS.md`'s explicit non-negotiable.
- `models.py` is the single source of truth for every env-overridable knob
  documented in the root `AGENTS.md`'s "Where things are" table
  (`HC_RAG_MAX_SUBQUERIES`, `HC_RAG_RETRIEVER`, `HC_RAG_RERANKER`,
  `HC_RAG_PAGEINDEX_MAX_NODES`, etc.) — add new knobs here, not scattered
  `os.environ` reads in processors/nodes.
- Tracing must stay fully no-op when `LANGSMITH_TRACING` is unset — don't
  introduce an import of `langsmith` at module scope outside `tracing.py`
  that would break that guarantee.
- Every Pinecone-dependent getter (`pinecone_index_name`, etc.) raising
  `ValueError("PINECONE_API_KEY is not set")` rather than hanging is a
  deliberate fail-fast contract for the Pinecone A/B arm — preserve it.

### Testing Requirements
- `tests/test_model_sampling.py` covers `model_sampling.py`.
- `tests/test_routing_settings.py`, `tests/graph/test_settings.py` cover the
  env-knob surface in `models.py` (consumed by `graph/settings.py`).
- `tests/test_tracing_privacy.py` covers `tracing.py`'s input-hiding
  enforcement.

### Common Patterns
- Every getter follows the same shape: read an env var with `os.getenv`,
  strip it, fall back to a module-level default constant if empty — see
  `_env_bool`, `_env_str`, `_env_positive_int`, `_enum_env` in `models.py` for
  the shared parsing helpers.

## Dependencies

### Internal
- Consumed by `healthcare_rag/graph/settings.py`, `graph/llm.py`, `graph/nodes/retrieve.py`, `processors/*`.

### External
- `langsmith` (`tracing.py` only, optional/no-op when disabled).

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
