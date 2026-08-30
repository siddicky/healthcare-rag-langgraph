---
type: runtime configuration
title: Model configuration, runtime controls, and cost boundaries
description: Environment-derived controls for model selection, compatible sampling, graph stages, retrieval, checkpointing, and resource lifecycle in the Healthcare RAG runtime. Use this page to change runtime behavior safely and evaluate—not assume—cost or quality effects.
tags: [configuration, models, runtime, operations]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-30T08:22:08.381Z
sources:
  - id: openwiki-source-3f718dfc0cae53689e49b15c
    resource: repo://docs/baseline-report.md
  - id: openwiki-source-4ab4e92e28196bf577454906
    resource: repo://docs/writeup.md
  - id: openwiki-source-e464dbfbded5331ec055dd1a
    resource: repo://healthcare_rag/AGENTS.md
  - id: openwiki-source-ce5020c63c5a2b23e700e1d0
    resource: repo://healthcare_rag/config.py
  - id: openwiki-source-184fc99d49c5faae867575f7
    resource: repo://healthcare_rag/graph/engine.py
  - id: openwiki-source-7772f43efa9811bd36483e17
    resource: repo://healthcare_rag/graph/llm.py
  - id: openwiki-source-9b0965dd12a5f2c42ed4d2a7
    resource: repo://healthcare_rag/graph/settings.py
  - id: openwiki-source-05c6c517a6da00d1f78ecc7d
    resource: repo://healthcare_rag/services/model_sampling.py
  - id: openwiki-source-5dac0d93eedc2d38a0fc6eaf
    resource: repo://healthcare_rag/services/models.py
  - id: openwiki-source-3e028c0fa647f1fe0f2c897c
    resource: repo://tests/test_model_sampling.py
generated: { by: "openwiki/0.4.3", at: "2026-08-30T08:22:08.381Z" }
---

# Model configuration, runtime controls, and cost boundaries

`GraphSettings.from_env()` is the configuration boundary for a graph run. It creates an immutable environment snapshot; `GraphEngine`, `Resources`, and the model gateway retain the snapshot with which they were constructed. Set variables before creating the runtime and reconstruct or restart it after a change—editing the process environment does not update an existing settings object.

```mermaid
flowchart TD
    Env["Environment variables"] --> Settings["GraphSettings snapshot"]
    Settings --> Engine["GraphEngine"]
    Settings --> Resources["Resources"]
    Resources --> Gateway["LangChainLLMGateway"]
    Gateway --> Models["default and validator models"]
    Resources --> Retrieval["retrieval arm and reranker"]
    Engine --> Saver["memory or SQLite checkpointer"]
```

This shows ownership: settings select behavior, resources lazily own external clients, and the engine owns graph compilation and checkpoint lifecycle.

`healthcare_rag/config.py` is a separate, legacy env-var helper (`get_env_var(name, default, required)`) that also loads `.env` at import time; no module in this repository currently imports it. Model and sampling configuration lives entirely in `healthcare_rag/services/model_sampling.py`, re-exported unchanged by `healthcare_rag/services/models.py` — do not add new model knobs to `config.py`.

## Settings, entry points, and lifecycle

`GraphEngine` is the in-process entry point: its first use initializes the privacy sanitizer, selects and initializes a checkpointer, and compiles the graph. Use `async with GraphEngine(...)` or call `await engine.aclose()`; closing exits an engine-owned SQLite context and closes resources held by the process-wide `Resources` owner. `Resources.get()` returns that singleton, while `override()` replaces it for test injection. Constructing resources does not connect to Weaviate, Pinecone, or OpenAI.

`GraphEngine.describe()` reports effective safety, model, sampling, retrieval/reranking, structured-output, and routing choices. Record this output with an experiment or rollout so a result can be attributed to the actual snapshot rather than an assumed environment.

## Models and sampling compatibility

| Variable | Default | Effect |
|---|---:|---|
| `HC_RAG_LLM_MODEL` | `gpt-5.6-luna` | Default tier for routing, preprocessing, retrieval evaluation, generation, and follow-ups. |
| `HC_RAG_VALIDATOR_MODEL` | `gpt-5.6-terra` | Validator tier used for the `validate_answer` structured stage. |
| `HC_RAG_REASONING_EFFORT` | `none` | Reasoning control supplied for recognized GPT-5.x and o-series names. |
| `HC_RAG_STRUCTURED_STRICT` | `false` | Enables `strict=True` for JSON-schema structured output (`with_structured_output(method="json_schema", strict=...)`). |

All four are optional; every getter (`default_llm_model`, `default_validator_model`, `default_reasoning_effort` in `healthcare_rag/services/model_sampling.py`) reads the environment at call time (not at import time), trims whitespace, and falls back to its default on a blank value — there is no required model environment variable. `LangChainLLMGateway` (`healthcare_rag/graph/llm.py`) centralizes client construction: it picks `settings.llm_model` for the `"default"` tier or `settings.validator_model` for the `"validator"` tier (used specifically for the `validate_answer` stage), builds `ChatOpenAI` with `use_responses_api=False` and `max_retries=3`, and caches each client by `(tier, model, requested_temperature, reasoning_effort)` so a settings change only takes effect in a freshly constructed gateway. Structured (`astructured`) and plain-text (`acomplete`) stages fail soft: any exception logs `LLM_STRUCTURED_STAGE_FAILED` (or the plain-text equivalent) and returns the caller-supplied default rather than propagating out of the gateway — a broken model call degrades a stage's output, it does not crash the turn.

### Why `sampling_params` exists, and reasoning controls vs. temperature

`sampling_params(model, temperature, reasoning_effort)` in `healthcare_rag/services/model_sampling.py` exists because the GPT-5.x family and the o-series are **reasoning models**: they reject `temperature`/`top_p` unless `reasoning_effort="none"`, while older chat models (the gpt-4o family) only understand `temperature`. Every call site used to hard-code `temperature=0.1`, which is a 400 error against a reasoning model. `sampling_params` turns one "desired temperature" input into whatever kwargs the selected model actually accepts, so a model can be swapped from the environment without touching call sites in processors or graph nodes.

`is_reasoning_model(model)` recognizes, case-insensitively, any model name starting with `gpt-5`, `o1`, `o3`, or `o4`. Given that:

* For a **reasoning model**, it always sends `reasoning_effort` (the supplied value, or `default_reasoning_effort()` if none was given), and sends the supplied `temperature` only when the effective effort is `"none"`.
* For an **o-series** model specifically, an effort of `"none"` is converted to `"low"` before being sent, because o-series has no `"none"` level — so an o-series call never receives `temperature`, even with the default effort setting.
* For a **non-reasoning model** (e.g. `gpt-4o-mini`), it sends the supplied `temperature` and no reasoning parameter at all.

`tests/test_model_sampling.py` pins this matrix exactly (including the `o1-mini`/`o3`/`o4-mini` "none"→"low" conversion and the case-insensitive prefix match) plus the identity-preserving `services.models` facade over `services.model_sampling`; run it after touching model-family or sampling logic.

Do not bypass the gateway with a direct `ChatOpenAI` construction or hard-code a sampling combination. The compatibility rules protect a model-name swap from an unsupported-parameter 400 error; they do **not** by themselves establish a quality, latency, or cost improvement — the model/temperature values only decide which kwargs are legal, not whether the resulting answers are better. Measure any such effect with the [evaluation workflow](../observability/evaluations.md).

### Model history and what a tier change actually costs

The defaults (`gpt-5.6-luna` for the default tier, `gpt-5.6-terra` for the validator) replaced an original `gpt-4o-mini` / `gpt-4o` pairing during a documented migration (`docs/baseline-report.md`, section "Model migration (gpt-5.6)"; `docs/writeup.md`). That measurement matters for anyone changing a tier again:

* Swapping the *validator* tier down to `gpt-5.6-luna` (i.e. `luna` everywhere) measurably dropped correctness from 0.75 to 0.55 on the core golden set, because a weaker structurer discards good content it cannot cite — the validator tier is not a safe place to cost-optimize by itself.
* Swapping the *default* tier alone (luna generation + terra validation) initially raised per-query cost roughly 2–4× over the `gpt-4o` baseline, driven by more aggressive decomposition, not by the per-call price difference.
* Cost was only brought back down to the original baseline (~$0.028/query) after a separate, non-model change — capping decomposition fan-out and adding a synthesis branch — was combined with the tier swap; that combined result is the one committed measurement of a net cost outcome for this migration (`docs/baseline-report.md`; `docs/writeup.md`).

Treat that as a caution, not a template: it is evidence that a model tier and the orchestration around it interact, not a general claim that any tier swap is cost-neutral or beneficial. A different tier swap has not been measured and should not be assumed to behave the same way.

## Stage switches, routing choices, and fan-out boundaries

`HC_RAG_DISABLE_STAGES` is an ablation switch, not a production safety or cost control. It is a comma-separated, case-normalized list limited to `safety`, `clarify`, `decompose`, `evaluate`, `validate`, and `followups`; an unknown stage raises `ValueError` while settings are built. Each switch short-circuits its corresponding work: clarification returns no change, decomposition uses the parent query default, and evaluation proceeds without gap fill. Disabling `safety` also disables the gate; do not use it as a production safety bypass.

| Variable | Default | Validation and behavior |
|---|---:|---|
| `HC_RAG_SAFETY_GATE` | `true` | Enabled only when this boolean is true and `safety` is not disabled. |
| `HC_RAG_REFUSAL_BOUNDARY` | `true` | Allows matching safety refusals to be persisted and replayed per thread; the safety node reads this flag live each turn. |
| `HC_RAG_MAX_SUBQUERIES` | `3` | Integer at least 1; decomposition always retrieves the parent and can add at most this many subquery branches. |
| `HC_RAG_DECOMPOSE_ONLY_COMPLEX` | `true` | Requires at least two proposals and a `complex` label unless disabled. |
| `HC_RAG_HISTORY_MAX_TOKENS` | `4000` | Integer token cap for history-aware gateway calls and history views. |
| `HC_RAG_QUERY_RESPONSE_ARM` | `current` | Exact, case-sensitive choice: `current`, `deterministic`, or `tool`. |
| `HC_RAG_SAFETY_CLASSIFIER` | `llm` | Exact choice: `llm` or `semantic_router`; this build rejects the latter at engine construction. |

Boolean controls accept case-insensitive `1`, `true`, `yes`, `on`, `0`, `false`, `no`, or `off`; blank values use their default and other values fail parsing. `HC_RAG_STRUCTURED_STRICT` follows the same token rule. The two routing enums do not normalize whitespace or case: blank, differently cased, and unknown values are errors. Although `semantic_router` parses, it cannot execute in this build and raises `SafetyClassifierUnavailableError`; use `llm`.

The graph has two explicit fan-out limits. Decomposition sends the parent query plus the capped subqueries. Retrieval evaluation can request only one gap-fill round: it requires `gap_round == 0`, retains at most three additional queries, marks the round used before routing, and the merge after gap fill routes directly to generation. These are behavioral bounds, not evidence of a measured saving.

## Retrieval and reranking

| Variable(s) | Default | Effect |
|---|---:|---|
| `HC_RAG_RETRIEVER` | `weaviate` | Selects `weaviate`, `pageindex`, or `pinecone`; invalid values fail parsing. |
| `HC_RAG_PAGEINDEX_DIR` | `data` | Directory containing cached PageIndex trees and chunks. |
| `HC_RAG_PAGEINDEX_MAX_NODES` / `HC_RAG_PAGEINDEX_MAX_CHUNKS` | `4` / `8` | Positive limits for PageIndex selection and chunk expansion. |
| `HC_RAG_RERANKER` | `none` | Selects `none` or `pinecone`; invalid values fail parsing. |
| `HC_RAG_RERANK_CANDIDATES` / `HC_RAG_RERANK_TOP_K` | `12` / `4` | Positive candidate-fetch and survivor limits when reranking is selected. |
| `HC_RAG_RERANK_MODEL` | `bge-reranker-v2-m3` | Pinecone Inference rerank model. |
| `HC_RAG_PINECONE_INDEX` / `HC_RAG_PINECONE_SPARSE_MODEL` | `healthcare-rag` / `pinecone-sparse-english-v0` | Pinecone index and sparse embedding model. |
| `HC_RAG_PINECONE_ALPHA` | `0.65` | Dense hybrid weight, inclusive range 0.0–1.0. |
| `HC_RAG_EMBEDDING_MODEL` | `text-embedding-3-small` | Dense embedding model for the Pinecone arm. |

The arm changes only the per-collection search callable; routing, merging, and downstream graph stages remain shared. Weaviate and Pinecone open their clients only when selected. PageIndex reads cached JSON and opens no retrieval client, but requires its tree and chunk files; missing files instruct the operator to run `make index-pageindex`.

Reranking is part of retrieval. When `HC_RAG_RERANKER=pinecone`, a limit-aware search fetches `rerank_candidates`, then Pinecone reranks each result to `rerank_top_k`. A rerank failure or empty ranking falls back to the search order truncated to `top_k`, preserving turn availability. See [retrieval arms and reranking](../retrieval/arms-and-reranking.md) for arm-specific behavior and evaluation gates.

### Credentials and lazy connections

`OPENAI_API_KEY` is required by Weaviate on first use and is supplied as `X-OpenAI-Api-Key`; the Pinecone arm also needs it when it creates query embeddings. `PINECONE_API_KEY` is required when a Pinecone client is first needed—either for the Pinecone retrieval arm or a Pinecone reranker on another arm. Missing credentials fail at that lazy boundary. A failed Weaviate connection is not cached, so a subsequent request can retry. `Resources.aclose()` closes only clients it owns, including gateway HTTP clients, a connected Weaviate client, and closeable Pinecone handles.

## Checkpoints and conversation history

`HC_RAG_CHECKPOINT` selects an engine-owned saver at initialization:

* Empty or any value not beginning `sqlite:` selects `InMemorySaver`.
* `sqlite:<path>` enters an `AsyncSqliteSaver` context using the suffix path and runs schema setup.
* SQLite requires `pip install healthcare-rag[graph-sqlite]`; otherwise initialization raises `RuntimeError` with that instruction.

The compiled graph keys state by `thread_id`. `seed_history()` writes scrubbed legacy turns as `HumanMessage`/`AIMessage` checkpoint state. For each new turn, history views scrub messages, token-trim them to `HC_RAG_HISTORY_MAX_TOKENS`, retain at most five completed pairs newest-first, and render the latest three exchanges oldest-first for context. Choose SQLite storage deliberately for durable history and close the engine so its context is released.

The LangGraph deployment reads `.env`, exposes the `healthcare_rag` graph, and separately configures an `openai:text-embedding-3-small` store index with 1,536 dimensions. That deployment store configuration is not a replacement for the engine's `HC_RAG_CHECKPOINT` choice.

## Safe model or runtime change procedure

1. **State scope and rollback.** Identify the single intended model/configuration change, retain the previous environment, and distinguish a production change from an ablation. Never make `HC_RAG_DISABLE_STAGES=safety` a production rollout.
2. **Validate a fresh snapshot.** Check enum spelling and case, boolean/numeric bounds, selected-arm prerequisites, required credentials, PageIndex artifacts if applicable, and the `graph-sqlite` extra for SQLite. Construct a new engine and capture `GraphEngine.describe()`.
3. **Preserve sampling compatibility.** Keep calls behind `LangChainLLMGateway`; do not conflate GPT-5.x reasoning effort with temperature, and do not assume an untested model name's reasoning-vs-temperature behavior — check it against `is_reasoning_model`'s prefix list and the API directly if it is not a recognized GPT-5.x/o-series/gpt-4o-style name. Run `uv run pytest tests/test_model_sampling.py` after changing model-family or sampling logic.
4. **Exercise the changed path.** Run `uv run pytest tests/test_routing_settings.py tests/graph/test_settings.py` for routing/settings changes, plus focused graph, retrieval, reranking, resource, or history tests for the modified control.
5. **Evaluate before asserting outcomes — do not invalidate a baseline silently.** A safety-gate template, a judge model, and a golden/hold-out dataset are all pinned to a specific pipeline behavior; changing the default or validator model changes what the safety gate sees and what the judge is grading, so an old evaluation report or safety-gate acceptance is not automatically valid evidence for the new model. Compare a fresh baseline and a fresh treatment run that differ only in the intended setting, using the [evaluation workflow](../observability/evaluations.md) and, for routing-sensitive changes, [routing evaluations](../observability/routing-evals.md). Review safety, correctness, groundedness, latency, usage, and cost only where the harness records them for that run. Do not assert a quality or cost outcome — including a cost saving — without a fresh, committed measurement backing it; the one committed cost result in this repository (`docs/baseline-report.md`, `docs/writeup.md`) covers a specific combined model-and-decomposition change, not model swaps in general.
6. **Roll out and observe.** Monitor initialization and lazy credential/connection failures, retrieval and rerank warnings, and outcome telemetry; keep the tested rollback configuration. See the [runbook](../operations/runbook.md) for setup, commands, and service recovery.

## Focused tests

`tests/test_model_sampling.py` fixes model defaults/blank fallback, case-insensitive reasoning-family detection, the sampling matrix, environment fallback, and the identity-preserving `services.models` facade. `tests/test_routing_settings.py` locks default and malformed routing settings and the early `semantic_router` rejection. Resource tests verify lazy connection retry and that closing resources permits fresh model and Weaviate clients. These are configuration contracts, not replacements for an end-to-end evaluation of a model or retrieval change.
