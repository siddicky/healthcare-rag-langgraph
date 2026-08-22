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
| `HC_RAG_REFUSAL_BOUNDARY` | `true` | persist qualifying gate refusals per thread and replay them deterministically on matching re-asks ([safety gate](../safety/gate.md)); the settings snapshot is telemetry only — the runtime reads this flag live each turn |
| `HC_RAG_LLM_MODEL` | `gpt-5.6-luna` | router, preprocessing, context, retrieval evaluation, generation, follow-ups, and the safety gate's classification call |
| `HC_RAG_VALIDATOR_MODEL` | `gpt-5.6-terra` | answer structuring and citation validation |
| `HC_RAG_REASONING_EFFORT` | `none` | GPT-5.x / o-series `reasoning_effort` |
| `HC_RAG_DISABLE_STAGES` | empty | ablation of safety, clarify, decompose, evaluate, validate, followups (`VALID_STAGES` in `services/models.py#L104`) |
| `HC_RAG_MAX_SUBQUERIES` | `3` | hard cap on sub-query fan-out per decomposition; extras dropped. `max_subqueries()` rejects non-integers and values < 1; `route_after_decompose` slices `sub_queries` with this cap directly — there is no separate router-side fan-out constant (`graph/routers.py`) |
| `HC_RAG_DECOMPOSE_ONLY_COMPLEX` | `true` | decompose only when the decomposer labelled `query_complexity == "complex"` |
| `HC_RAG_RETRIEVER` | `weaviate` | retrieval arm: `weaviate` (default), `pageindex`, or `pinecone` — see [retrieval arms and reranking](../retrieval/arms-and-reranking.md); only the per-collection search callable changes |
| `HC_RAG_RERANKER` / `HC_RAG_RERANK_*` | `none` | rerank stage: `none` or `pinecone` (`bge-reranker-v2-m3`), `HC_RAG_RERANK_CANDIDATES=12` fetched, `HC_RAG_RERANK_TOP_K=4` kept, fail-soft |
| `HC_RAG_PINECONE_*` / `PINECONE_API_KEY` | — | pinecone arm: index `healthcare-rag`, sparse model `pinecone-sparse-english-v0`, dense/sparse convex-scaling `HC_RAG_PINECONE_ALPHA=0.65`, embeddings `text-embedding-3-small`; `PINECONE_API_KEY` is a secret kept in `.env` |
| `HC_RAG_PAGEINDEX_*` | — | pageindex arm: `MAX_NODES=4`, `MAX_CHUNKS=8`, tree/chunk dir `HC_RAG_PAGEINDEX_DIR=data` |
| `HC_RAG_HISTORY_MAX_TOKENS` | `4000` | token cap when trimming checkpointed history for prompts (`GraphSettings.from_env`; must be an integer) |
| `HC_RAG_STRUCTURED_STRICT` | `false` | passes `strict=True` to structured-output calls (`HC_RAG_STRUCTURED_STRICT` must be a boolean) |
| `HC_RAG_CHECKPOINT` | empty | `sqlite:<path>` enables durable per-thread history via `AsyncSqliteSaver` (needs the `graph-sqlite` extra); empty means in-memory checkpointing (`graph/engine.py`) |
| `HC_RAG_RETRIEVER` | `weaviate` | retrieval arm: `weaviate`, `pageindex` (LLM tree-search), or `pinecone` (serverless hybrid). Swaps only the per-collection search callable — see [retrieval arms](../retrieval/arms-and-reranking.md) |
| `HC_RAG_RERANKER` | `none` | `pinecone` enables cross-encoder reranking over the retrieved candidates (part of the retrieval stage; fail-soft) |
| `HC_RAG_RERANK_CANDIDATES` / `HC_RAG_RERANK_TOP_K` / `HC_RAG_RERANK_MODEL` | `12` / `4` / `bge-reranker-v2-m3` | candidates fetched per collection when reranking / survivors per collection (default keeps top-k into generation constant) / Pinecone Inference rerank model |
| `HC_RAG_PINECONE_INDEX` / `HC_RAG_PINECONE_SPARSE_MODEL` / `HC_RAG_PINECONE_ALPHA` / `HC_RAG_EMBEDDING_MODEL` | `healthcare-rag` / `pinecone-sparse-english-v0` / `0.65` / `text-embedding-3-small` | pinecone arm: index name (one namespace per lower-cased collection), sparse embedding model, convex-scaling dense weight (must be 0.0–1.0), dense embedding model |
| `HC_RAG_PAGEINDEX_MAX_NODES` / `HC_RAG_PAGEINDEX_MAX_CHUNKS` / `HC_RAG_PAGEINDEX_DIR` | `4` / `8` / `data` | pageindex arm: tree nodes the selection call may keep / chunk cap / directory holding `pageindex_tree_*.json` and `chunks_*.json` |
| `HC_RAG_QUERY_RESPONSE_ARM` | `current` | how a turn is answered: `current` (pipeline/refusals), `deterministic` (hard-coded benign-social direct text), `tool` (model query-or-respond decision gated by the direct-output policy) — see [safety gate](../safety/gate.md) |
| `HC_RAG_SAFETY_CLASSIFIER` | `llm` | safety classification backend; `semantic_router` was never installed or exercised — `semantic-router==0.1.16` is unsatisfiable under the unchanged `openai>=1.76,<2` / `python-dotenv>=1.1` bounds, so that lane is dependency-INCONCLUSIVE — see [routing evaluations](../observability/routing-evals.md) |

`PINECONE_API_KEY` (secret, `.env`) is required by the pinecone arm and the reranker; `PINECONE_CLOUD`/`PINECONE_REGION` override serverless placement. The model defaults and `sampling_params` live in `services/model_sampling.py` (re-exported by `services/models.py`, which keeps the env-var policy above); `tests/test_model_sampling.py` pins the reasoning-vs-chat sampling rules.

The three retrieval/rerank groups exist for A/B experiments: the snapshot lives in `GraphSettings` (`graph/settings.py`) and the docstring of `services/models.py` is their authoritative description; behavior is covered in [retrieval arms and reranking](../retrieval/arms-and-reranking.md).

The decomposition settings implement journey findings F06/F07 (see [architecture](../architecture/overview.md)): `gpt-5.6-luna` emits up to 8 sub-queries even for simple or out-of-scope questions. Boolean parsing via `_env_bool` accepts `1/true/yes/on` and `0/false/no/off` and raises `ValueError` on anything else (`healthcare_rag/services/models.py#L125-L136`). The former `HC_RAG_SYNTHESIS` flag is gone: in the graph runtime, decomposed sub-queries only retrieve and their documents are always merged for a single answer to the original query.

`sampling_params(model, temperature, reasoning_effort)` exists because GPT-5.x reasoning models reject `temperature`/`top_p` except when `reasoning_effort="none"`, whereas chat models accept temperature. It emits `reasoning_effort` for `gpt-5`, `o1`, `o3`, and `o4`; with effort `none`, it also includes supplied temperature. o-series upgrades `none` to `low`; non-reasoning models receive temperature only (`healthcare_rag/services/model_sampling.py`). Every LLM call goes through `LangChainLLMGateway.chat_model`, which caches `ChatOpenAI` clients per `(tier, model, temperature, reasoning_effort)` (`healthcare_rag/graph/llm.py`). Do not add direct LLM calls that bypass it.

Stage disabling is experiment machinery, not a production safety setting. Unknown stage names raise `ValueError`; disabled validation returns the raw answer, disabled follow-ups return `[]` (see [processors](../processors/overview.md)). Record it in eval metadata and do not compare an ablation against a normal baseline without matching configuration.

## Required runtime settings

`OPENAI_API_KEY` is required — `Resources.weaviate()` raises without it, and Weaviate's OpenAI vectorizer needs it in the `X-OpenAI-Api-Key` header. The app loads `.env` at import time but documentation must never expose its values. Weaviate connection defaults are `WEAVIATE_HOST=127.0.0.1`, `WEAVIATE_PORT=8080`, and `WEAVIATE_GRPC_PORT=50051`; `GraphSettings.from_env` snapshots all of these plus collection names (default `Lipitor`, `Metformin`) (`healthcare_rag/graph/settings.py`). Optional LangSmith variables belong in [observability](../observability/evaluations.md).

## State and lifecycle

Conversation history is now LangGraph checkpoint state, not files: `finalize` appends a scrubbed Human/AI message pair per answered turn, and `GraphEngine` compiles the graph with an `InMemorySaver` or, when `HC_RAG_CHECKPOINT=sqlite:...`, a durable `AsyncSqliteSaver` (`graph/engine.py`; `graph/nodes/safety.py`). Engine entry also initializes the process-wide [PrivacySanitizer](../privacy/sanitizer.md) (`GraphEngine._initialize`), and a `PrivacyScanError` during a turn fails that turn closed — no answer is produced (`graph/engine.py`). History views scrub every message when the gate is on, trim to `HC_RAG_HISTORY_MAX_TOKENS` keeping the last messages, expose the five most recent turns (newest-first) and render the last three as clarification context (`graph/history.py`). Since the [safety gate](../safety/gate.md), persisted queries and answers are scrubbed on write, but SQLite checkpoints still have no encryption, access control, expiry, user-ID validation, or deletion API. Treat user IDs and questions/answers as sensitive and see [safety posture](../safety/posture.md).

The CLI closes the engine (and with it the Weaviate client) on exit (`engine.aclose()`); embedding callers should do the same (`healthcare_rag/cli/interactive.py`). For setup and operations, use [runbook](../operations/runbook.md).
