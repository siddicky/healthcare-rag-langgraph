---
type: configuration
title: Models, environment, and runtime state
description: Model selection, reasoning-compatible sampling parameters, stage ablations, connection settings, and conversation persistence.
tags: [configuration, models, runtime]
---

# Models, environment, and runtime state

## Model policy

`healthcare_rag/services/models.py` centralizes selected models so processors do not hard-code incompatible sampling options.

| Variable | Default | Owner |
|---|---|---|
| `HC_RAG_SAFETY_GATE` | `true` | when true, every query passes the [runtime safety gate](../safety/gate.md) before the pipeline: PHI is scrubbed and personal-advice/emergency/out-of-scope/injection messages get templated refusals; `false` (or `safety` in `HC_RAG_DISABLE_STAGES`) runs un-gated for before/after ablations. `safety_gate_enabled()` reads both (`services/models.py#L173-L179`) |
| `HC_RAG_LLM_MODEL` | `gpt-5.6-luna` | router, preprocessing, context, retrieval evaluation, generation, follow-ups, and the safety gate's classification call |
| `HC_RAG_VALIDATOR_MODEL` | `gpt-5.6-terra` | answer structuring and citation validation |
| `HC_RAG_REASONING_EFFORT` | `none` | GPT-5.x / o-series `reasoning_effort` |
| `HC_RAG_DISABLE_STAGES` | empty | ablation of safety, clarify, decompose, evaluate, validate, followups (`VALID_STAGES` in `services/models.py#L104`) |
| `HC_RAG_MAX_SUBQUERIES` | `3` | hard cap on sub-query fan-out per decomposition; extras dropped. `max_subqueries()` rejects non-integers and values < 1. Note the router also enforces its own `_MAX_FAN_OUT = 3` (`graph/routers.py`) |
| `HC_RAG_DECOMPOSE_ONLY_COMPLEX` | `true` | decompose only when the decomposer labelled `query_complexity == "complex"` |
| `HC_RAG_HISTORY_MAX_TOKENS` | `4000` | token cap when trimming checkpointed history for prompts (`GraphSettings.from_env`; must be an integer) |
| `HC_RAG_STRUCTURED_STRICT` | `false` | passes `strict=True` to structured-output calls (`HC_RAG_STRUCTURED_STRICT` must be a boolean) |
| `HC_RAG_CHECKPOINT` | empty | `sqlite:<path>` enables durable per-thread history via `AsyncSqliteSaver` (needs the `graph-sqlite` extra); empty means in-memory checkpointing (`graph/engine.py`) |

The decomposition settings implement journey findings F06/F07 (see [architecture](../architecture/overview.md)): `gpt-5.6-luna` emits up to 8 sub-queries even for simple or out-of-scope questions. Boolean parsing via `_env_bool` accepts `1/true/yes/on` and `0/false/no/off` and raises `ValueError` on anything else (`healthcare_rag/services/models.py#L125-L136`). The former `HC_RAG_SYNTHESIS` flag is gone: in the graph runtime, decomposed sub-queries only retrieve and their documents are always merged for a single answer to the original query.

`sampling_params(model, temperature, reasoning_effort)` exists because GPT-5.x reasoning models reject `temperature`/`top_p` except when `reasoning_effort="none"`, whereas chat models accept temperature. It emits `reasoning_effort` for `gpt-5`, `o1`, `o3`, and `o4`; with effort `none`, it also includes supplied temperature. o-series upgrades `none` to `low`; non-reasoning models receive temperature only (`healthcare_rag/services/models.py#L77-L95`). Every LLM call goes through `LangChainLLMGateway.chat_model`, which caches `ChatOpenAI` clients per `(tier, model, temperature, reasoning_effort)` (`healthcare_rag/graph/llm.py`). Do not add direct LLM calls that bypass it.

Stage disabling is experiment machinery, not a production safety setting. Unknown stage names raise `ValueError`; disabled validation returns the raw answer, disabled follow-ups return `[]` (see [processors](../processors/overview.md)). Record it in eval metadata and do not compare an ablation against a normal baseline without matching configuration.

## Required runtime settings

`OPENAI_API_KEY` is required — `Resources.weaviate()` raises without it, and Weaviate's OpenAI vectorizer needs it in the `X-OpenAI-Api-Key` header. The app loads `.env` at import time but documentation must never expose its values. Weaviate connection defaults are `WEAVIATE_HOST=127.0.0.1`, `WEAVIATE_PORT=8080`, and `WEAVIATE_GRPC_PORT=50051`; `GraphSettings.from_env` snapshots all of these plus collection names (default `Lipitor`, `Metformin`) (`healthcare_rag/graph/settings.py`). Optional LangSmith variables belong in [observability](../observability/evaluations.md).

## State and lifecycle

Conversation history is now LangGraph checkpoint state, not files: `finalize` appends a scrubbed Human/AI message pair per answered turn, and `GraphEngine` compiles the graph with an `InMemorySaver` or, when `HC_RAG_CHECKPOINT=sqlite:...`, a durable `AsyncSqliteSaver` (`graph/engine.py`; `graph/nodes/safety.py`). History views scrub every message when the gate is on, trim to `HC_RAG_HISTORY_MAX_TOKENS` keeping the last messages, expose the five most recent turns (newest-first) and render the last three as clarification context (`graph/history.py`). Since the [safety gate](../safety/gate.md), persisted queries and answers are scrubbed on write, but SQLite checkpoints still have no encryption, access control, expiry, user-ID validation, or deletion API. Treat user IDs and questions/answers as sensitive and see [safety posture](../safety/posture.md).

The CLI closes the engine (and with it the Weaviate client) on exit (`engine.aclose()`); embedding callers should do the same (`healthcare_rag/cli/interactive.py`). For setup and operations, use [runbook](../operations/runbook.md).
