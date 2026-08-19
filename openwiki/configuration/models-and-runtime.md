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
| `HC_RAG_MAX_SUBQUERIES` | `3` | hard cap on sub-query branches per decomposition; extras dropped with a warning. `max_subqueries()` rejects non-integers and values < 1 |
| `HC_RAG_SYNTHESIS` | `true` | when true, decomposed sub-branches only retrieve+evaluate and a single `synthesized` branch answers the original query; `false` restores per-sub-branch answer+validate |
| `HC_RAG_DECOMPOSE_ONLY_COMPLEX` | `true` | decompose only when the decomposer labelled `query_complexity == "complex"` |

The three decomposition settings implement journey findings F06/F07 (see [architecture](../architecture/overview.md)): `gpt-5.6-luna` emits up to 8 sub-queries even for simple or out-of-scope questions, and the old per-sub-branch answer+validate multiplied cost while `_select_best_answer` returned one sub-question's answer. Boolean parsing via `_env_bool` accepts `1/true/yes/on` and `0/false/no/off` and raises `ValueError` on anything else (`healthcare_rag/services/models.py#L113-L158`).

`sampling_params(model, temperature, reasoning_effort)` exists because GPT-5.x reasoning models reject `temperature`/`top_p` except when `reasoning_effort="none"`, whereas chat models accept temperature. It emits `reasoning_effort` for `gpt-5`, `o1`, `o3`, and `o4`; with effort `none`, it also includes supplied temperature. o-series upgrades `none` to `low`; non-reasoning models receive temperature only (`healthcare_rag/services/models.py#L1-L79`). Both parsed and freeform/streamed calls use it (`services/llm.py#L49-L58`; `processors/base.py#L125-L129`). Do not add direct OpenAI calls that bypass it.

Stage disabling is experiment machinery, not a production safety setting. Unknown stage names raise `ValueError`; disabled validation returns raw answer, and disabled follow-ups returns `[]` (`services/models.py#L82-L96`; `orch/tasks.py#L151-L179`). Record it in eval metadata and do not compare an ablation against a normal baseline without matching configuration.

## Required runtime settings

`OPENAI_API_KEY` is required by `setup_medical_rag` and by Weaviate's OpenAI vectorizer. The app loads `.env` at import time but documentation must never expose its values. Weaviate connection defaults are `WEAVIATE_HOST=127.0.0.1`, `WEAVIATE_PORT=8080`, and `WEAVIATE_GRPC_PORT=50051`; `setup_medical_rag` creates an async client with the key in `X-OpenAI-Api-Key` and defaults to `Lipitor` and `Metformin` collections (`healthcare_rag/config.py#L12-L108`). Optional LangSmith variables belong in [observability](../observability/evaluations.md).

## State and lifecycle

`MedicalRAG` owns a `ConversationHistory` configured by `conversation_history_dir`, default `data/conversations` (`healthcare_rag/pipeline/medical_rag.py#L36-L105`). It creates the directory, retains in-memory lists per `user_id`, serializes entries after every final answer, and lazily reloads them. History reads at most five entries newest-first; clarification context uses last three chronological entries (`healthcare_rag/storage/history.py#L11-L143`). Since the [safety gate](../safety/gate.md), persisted queries are scrubbed of identifiers on write and re-scrubbed on read, but there is still no encryption, access control, expiry, user-ID validation, or deletion API. Treat user IDs and questions/answers as sensitive and see [safety posture](../safety/posture.md).

The CLI owns client shutdown; embedding callers should close `rag.weaviate_client` when finished (`healthcare_rag/cli/interactive.py#L154-L162`). For setup and operations, use [runbook](../operations/runbook.md).
