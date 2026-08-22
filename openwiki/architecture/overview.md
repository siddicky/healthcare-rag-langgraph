---
type: architecture
title: LangGraph runtime architecture
description: How the healthcare RAG StateGraph routes queries through safety, preprocessing, fan-out retrieval, gap-fill, generation, validation, and finalization, and how the engine projects results for evals.
tags: [orchestration, runtime, rag, langgraph]
openwiki:
  roles: [architecture, domain, testing]
  change_kinds: [lifecycle, routing]
  source_paths: [healthcare_rag/graph/build.py, healthcare_rag/graph/routers.py, healthcare_rag/graph/engine.py, healthcare_rag/graph/state.py, healthcare_rag/graph/engine_record.py]
  symbols: [build_graph, build_pipeline, GraphEngine, Engine, route_after_gate, route_after_decompose, route_after_evaluate, RAGState, build_result, fold_branches]
  test_paths: [tests/graph/test_graph_build.py, tests/graph/test_graph_routing.py, tests/graph/test_graph_flow.py, tests/graph/test_branch_fold.py, tests/graph/test_engine_record.py]
  invariants: [Safe queries fan out to at most 3 decomposed plus 1 parent retrieval via Send; gap-fill runs at most one capped round.,The safety gate resets all downstream state per turn and the scrubbed query is what flows through retrieval and generation.,finalize appends the Human/AI message pair only when a nonempty answer exists.]
  validation_commands: [make test]
---

# LangGraph runtime architecture

The runtime is a LangGraph `StateGraph` — the speculative orchestrator (`healthcare_rag/orch/`) and the old linear `MedicalRAG` pipeline were removed in the Phase-2 cleanup. The CLI enters at `healthcare_rag/__main__.py`, loads `.env`, and calls `build_engine()` to get a `GraphEngine` (`healthcare_rag/cli/interactive.py`); the engine owns the compiled graph, its checkpointer, and per-thread history (`healthcare_rag/graph/engine.py`). The same graph is served by the local LangGraph Agent Server (`langgraph.json` → `healthcare_rag/graph/__init__.py:graph`, `make dev`); `langgraph.json` also serves the separate [coach agent](../agent/coach.md) graph (`coach`) with its own auth and HTTP app.

## Graph topology

`build_graph()` (`healthcare_rag/graph/build.py`) composes the public graph from `add_pipeline` plus the terminal-side nodes `safety_gate` and `finalize`. Node names are canonical constants in `healthcare_rag/graph/routers.py` (`NODE_SAFETY`, `NODE_CLARIFY`, `NODE_CONTEXT`, `NODE_DECOMPOSE`, `NODE_RETRIEVE`, `NODE_MERGE`, `NODE_EVALUATE`, `NODE_GENERATE`, `NODE_VALIDATE`, `NODE_FOLLOW_UPS`, `NODE_FINALIZE`).

```mermaid
flowchart TD
  S -->|"refusal"| F["finalize"]
  S -->|"in scope / ambiguous"| C["clarify_query"]
  S -->|"in scope / ambiguous"| X["extract_conversation_context"]
  C --> D["decompose_query"]
  X --> D
  D -->|"Send fan-out: parent + up to 3 sub-queries"| R["retrieve_documents (parallel)"]
  R --> M["merge_retrievals: dedupe by doc_id"]
  M -->|"gap_fill already merged"| G["generate_answer"]
  M -->|"first pass"| E["evaluate_retrieval"]
  E -->|"insufficient + queries remain"| R
  E -->|"sufficient"| G
  G --> V["validate_answer: structure + citation check"]
  V --> FU["generate_follow_ups"]
  V --> FU2["finalize"]
  FU --> F
```

Caption: one safety pass, parallel preprocessing, capped retrieval fan-out via LangGraph `Send`, at most one gap-fill round, then generate → validate → follow-ups → finalize.

### Routing rules (`graph/routers.py`)

* `route_after_gate`: a refusal goes straight to `finalize`; safe queries fan in to `[clarify_query, extract_conversation_context]`, which both edge into `decompose_query`.
* `route_after_decompose`: always `Send`s the parent (working) query to `retrieve_documents`; if `decomposed`, it also sends up to `_MAX_FAN_OUT = 3` sub-queries (hard-coded; `HC_RAG_MAX_SUBQUERIES` gates decomposition itself, see [models and runtime](../configuration/models-and-runtime.md)). There is no supersession — all retrievals append into state.
* `route_after_merge`: after a gap-fill merge (`gap_filled`) skip re-evaluation and go to `generate_answer`; otherwise `evaluate_retrieval`.
* `route_after_evaluate`: if `gap_pending` (evaluation said `is_sufficient=False`, `gap_round == 0`, and nonempty `additional_queries` capped at 3), `Send` the gap-fill queries back to `retrieve_documents` with `phase=1, kind="gap_fill"`; otherwise `generate_answer`. Only **one** gap-fill round ever runs.
* `route_after_validate`: follow-ups (when enabled) or finalize.

### Nodes

* **`safety_gate`** (`graph/nodes/safety.py`): wraps `SafetyGate.evaluate` via `LangChainSafetyGate` (LLM adapter is fail-soft). Emits a full per-turn **reset** of all downstream state (including `Overwrite([])` for `retrievals`, `route`, `branch_events`) so a checkpointed thread cannot leak stale results from a previous turn, then the scrubbed query, `SafetyOutcome`, refusal template, and notices. When `HC_RAG_REFUSAL_BOUNDARY` is on, a matching persisted refusal in `refusal_boundaries` short-circuits **before** any LLM call (`boundary_replay`), and new qualifying refusals are upserted into that state field. Full policy on the [safety gate](../safety/gate.md) page.
* **`extract_conversation_context`** / **`clarify_query`** / **`decompose_query`** (`graph/nodes/preprocess.py`): typed structured calls with fail-soft defaults. Clarify only runs with history context and records a `clarified` branch event when the text changes; decompose applies the complexity gate and `HC_RAG_MAX_SUBQUERIES` cap.
* **`retrieve_documents`** (`graph/nodes/retrieve.py`): routes the query with `gateway.aroute_tools` (fail-soft), runs `hybrid_search` per tool call with up to 3 attempts (1 s/2 s backoff on `WeaviateBaseError`), `union_results` dedupes by `doc_id`, and appends an envelope (`phase`/`kind`/`index`/`branch`/results) to `retrievals`. Non-gap-fill retrievals also append `branch_events` (COMPLETED/FAILED). See [retrieval](../retrieval/weaviate-and-ingestion.md).
* **`merge_retrievals`**: sorts envelopes by `(phase, kind rank, index)` (`initial/clarified < decomposed < gap_fill`) and merges into `merged`; sets `gap_filled` when a phase-1 merge added documents.
* **`generate_answer`**: no merged documents → fixed fallback `"I'm sorry, I don't know the answer to that question."`; otherwise one plain completion with the conversation summary context, producing `plain_answer`, `formatted_docs`, `prompt_id_map`.
* **`validate_answer`**: `validate` disabled in `HC_RAG_DISABLE_STAGES` → passes the raw answer through; otherwise runs `AnswerValidator.structure_and_validate_async` (threshold 85) inside try/except — validation must never fail open, exceptions yield `(None, None)`. See [answer validation](../processors/validation.md).
* **`generate_follow_ups`**: only when a validated answer and `user_id` exist; disabled or failure yields `[]`.
* **`finalize`**: refusal answers join notices + template with `follow_ups = []`; normal answers are `render_display_answer(validated, notices)` (notices prefixed). Appends the `HumanMessage`/`AIMessage` pair (with ISO `ts`) to `messages` **only when the answer is nonempty** — that pair is the persisted conversation.

## State and engine

`RAGState` (`graph/state.py`) is JSON-native; the exceptions are `messages` (LangGraph `add_messages` channel) and the append-only `retrievals`/`route`/`branch_events` reducers. `refusal_boundaries` holds serialized persisted refusals (see [safety gate](../safety/gate.md)). `RetrieveInput` is the per-`Send` retrieval payload.

`GraphEngine` (`graph/engine.py`):

* Compiles `build_graph()` with an `InMemorySaver`, or an async SQLite saver when `HC_RAG_CHECKPOINT=sqlite:...` (requires the `graph-sqlite` extra).
* `process_query`/`run_turn` stream the graph with `stream_mode="updates"`, `durability="exit"`, feeding node updates to the `QueryMonitor`, capturing first-answer and finalize timings, and folding the final checkpointed state via `build_result` into the legacy eval record (contexts, chunk/page/source IDs, folded branch statuses, usage, latency, error). Root inputs are PHI-scrubbed for LangSmith (`_redact_root_inputs` fails closed).
* `seed_history` writes scrubbed legacy turns into the thread checkpoint as messages (`graph/history.py`); conversation memory is now the checkpointer, not the old file-backed store — there is no `data/conversations` reader any more.
* `UsageRecorder` (an `AsyncCallbackHandler`) accumulates per-LLM-call model/tokens/latency for eval usage; one instance belongs to one turn.
* `describe()` reports engine config (`safety`, `max_subqueries`, `decompose_only_complex`, `structured_strict`, models, `reasoning_effort`) — read it when comparing experiments.

`fold_branches` (`graph/engine_record.py`) deterministically orders append-only `branch_events` by `(phase, kind rank, index)` — arrival order never changes telemetry — and marks the selected branch FAILED when validation produced nothing (and not a refusal, and validate not disabled).

## Shared resources

`Resources` (`graph/resources.py`) is the lazy process-wide owner: `PromptRegistry`, the `LangChainLLMGateway` (ChatOpenAI clients cached by tier/model/temperature/effort), and the eagerly-connected async Weaviate client (raises if `OPENAI_API_KEY` is unset). Tests override it via `override(Resources())` — see `tests/graph/conftest.py` (`FakeGateway`, `FakeRetriever`). The gateway's stage→tier rule: `validate_answer` uses the validator model, everything else the default model; `HC_RAG_STRUCTURED_STRICT=true` enables strict JSON-schema structured output.

## Changing the graph safely

* Adding a node: define it in `graph/nodes/`, add its `NODE_*` constant, wire it in `build.py` (or `add_pipeline`), add state fields to `RAGState`, and reset the field in `safety_gate`'s per-turn reset if it must not leak across turns.
* Changing fan-out or the gap-fill round changes cost and retrieval breadth; validate with `make test` (`tests/graph/test_graph_routing.py`, `test_graph_flow.py`) plus a filtered eval.
* The graph tests in `tests/graph/` run offline against fakes; `tests/graph/test_graph_integration.py` and `test_graph_safety.py` cover end-to-end flow and gate behaviour; `tests/graph/test_prompt_fidelity.py` pins which prompt stage each node invokes. `make eval-smoke` remains the end-to-end smoke.
