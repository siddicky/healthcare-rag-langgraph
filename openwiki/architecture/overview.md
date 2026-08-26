---
type: architecture
title: LangGraph RAG runtime architecture
description: Active LangGraph StateGraph topology, turn state, routing, durable checkpoint history, failure behavior, and the GraphEngine boundary for the healthcare RAG runtime.
tags: [orchestration, runtime, rag, langgraph]
openwiki:
  roles: [architecture, domain, testing]
  change_kinds: [lifecycle, routing]
  source_paths: [healthcare_rag/graph/build.py, healthcare_rag/graph/routers.py, healthcare_rag/graph/engine.py, healthcare_rag/graph/state.py, healthcare_rag/graph/engine_record.py, healthcare_rag/graph/nodes/query_or_respond.py]
  symbols: [build_graph, build_pipeline, GraphEngine, Engine, route_after_gate, route_after_query_or_respond, route_after_decompose, route_after_evaluate, RAGState, build_result, fold_branches, generate_query_or_respond]
  test_paths: [tests/graph/test_graph_build.py, tests/graph/test_graph_routing.py, tests/graph/test_graph_flow.py, tests/graph/test_branch_fold.py, tests/graph/test_engine_record.py, tests/graph/test_router_typing.py]
  invariants: [Safe queries fan out to at most max_subqueries decomposed plus 1 parent retrieval via Send; gap-fill runs at most one capped round.,The safety gate resets all downstream state per turn and the scrubbed query is what flows through retrieval and generation.,finalize appends the Human/AI message pair only when a nonempty answer exists.]
  validation_commands: [make test]
verified:
  - by: openwiki/0.4.0
    at: 2026-08-26T20:21:43.477Z
---

# LangGraph RAG runtime architecture

The active RAG runtime is a LangGraph `StateGraph`. `build_graph()` supplies the public graph with constrained `GraphInput` and `GraphOutput`; `GraphEngine` compiles and executes it for the CLI and other in-process callers. The LangGraph server separately exports the compiled `healthcare_rag` graph from `healthcare_rag/graph/__init__.py`; `langgraph.json` also registers a distinct `coach` graph.

## Compiled graph and control ownership

The public graph starts at `safety_gate` and ends only after `finalize`. `add_pipeline()` provides the post-gate stages, while `build_pipeline()` is an internal, trusted post-gate variant: it starts directly with the two preprocessing nodes and maps validation's terminal path to `END` rather than `finalize`.

Decision nodes own their transitions by returning `Command` with a typed `goto`: `safety_gate`, `generate_query_or_respond`, `decompose_query`, `merge_retrievals`, and `evaluate_retrieval`. Ordinary sequencing is declared in the builder. `validate_answer` is deliberately different: a conditional edge maps its `finalize` label to `finalize` in the public graph or to `END` in the internal pipeline. Canonical node-name constants and router target literals are tested against the compiled graph, so dynamic routes cannot silently drift from the topology.

```mermaid
flowchart TD
  Start([START]) --> Gate["safety_gate"]
  Gate -->|"refusal or direct response"| Final["finalize"]
  Gate -->|"tool decision"| Query["generate_query_or_respond"]
  Gate -->|"safe retrieval path"| Clarify["clarify_query"]
  Gate -->|"safe retrieval path"| Context["extract_conversation_context"]
  Query -->|"direct response"| Final
  Query -->|"retrieve"| Clarify
  Query -->|"retrieve"| Context
  Clarify --> Decompose["decompose_query"]
  Context --> Decompose
  Decompose -->|"Send parent and capped subqueries"| Retrieve["retrieve_documents"]
  Retrieve --> Merge["merge_retrievals"]
  Merge -->|"initial merge"| Evaluate["evaluate_retrieval"]
  Merge -->|"gap-fill merged"| Generate["generate_answer"]
  Evaluate -->|"one capped gap-fill round"| Retrieve
  Evaluate -->|"otherwise"| Generate
  Generate --> Validate["validate_answer"]
  Validate -->|"validated"| Followups["generate_follow_ups"]
  Validate -->|"not validated"| Final
  Followups --> Final
  Final --> Done([END])
```

This is the public compiled graph: safety or direct answers short-circuit to finalization; retrieval work fans out through `Send`, then rejoins before generation.

### Routing invariants

- The gate finalizes a refusal or precomputed direct response. A tool-arm turn enters `generate_query_or_respond`; otherwise a safe retrieval turn fans out to `clarify_query` and `extract_conversation_context`, both of which must complete before decomposition.
- The query-or-respond node can finalize only a direct response. All other outcomes rejoin the same two-node preprocessing fan-out.
- Decomposition always sends the parent working query to `retrieve_documents`. When decomposition is enabled and appropriate, it additionally sends at most `max_subqueries` subqueries. Each send has `phase`, `kind`, `index`, and `branch` metadata.
- Retrieval envelopes append to state rather than replacing each other. Merge sorts them deterministically by phase, kind, and index before unioning results. Initial retrieval is evaluated; a gap-fill merge goes straight to generation.
- Evaluation can launch only one additional round: it requires insufficient retrieval, `gap_round == 0`, and nonempty additional queries. The router caps that round at three sends, and the evaluation update advances `gap_round` to one before routing. A later merge therefore cannot re-enter evaluation.

## Nodes and data lifecycle

### Safety and response shortcuts

` safety_gate` first derives scrubbed, token-bounded history views from checkpointed messages, then resets all per-turn downstream channels before processing the new question. The reset is essential for a reused thread: it clears prior retrievals, generation, validation, route events, direct-response fields, and error state. The gate writes a scrubbed question and working query, safety outcome, notices, and either a refusal/direct response or a route to the retrieval pipeline.

When refusal boundaries are enabled, the gate can replay a persisted matching refusal before invoking the classifier; qualifying new refusals are upserted into `refusal_boundaries`. Classification adapter failures return the classifier default rather than propagating from that adapter.

`generate_query_or_respond` is active only for the `tool` query-response arm. It makes one gateway decision and records safe router telemetry. A benign social turn may return a direct response; an in-scope informational turn is forced onto retrieval rather than accepting medical free text as a direct answer. `finalize` renders refusals with notices, preserves direct answers, or renders a validated answer with notices. It scrubs answer and follow-ups again at this output boundary.

### Retrieval, answer, and validation

The preprocessing fan-out has distinct responsibilities: clarification acts only when history context exists, context extraction summarizes relevant history, and decomposition decides whether to create subqueries. Structured-call defaults make context extraction and decomposition continue with safe defaults when no model result is returned.

Each retrieval send scrubs its query, uses tool routing to select collection queries, then dispatches the configured retrieval arm (`weaviate`, `pageindex`, or `pinecone`; a resource-injected search override takes precedence). Routing failures are fail-soft. Weaviate and Pinecone transient SDK errors are attempted up to three times with one- then two-second delays; a terminal failure produces an empty envelope, allowing other fan-out branches to survive. Optional reranking fetches a wider candidate set and trims it to `rerank_top_k`.

`generate_answer` returns a fixed unknown-answer fallback when the merged result has no documents; otherwise it generates from formatted retrieved documents and relevant conversation context, then scrubs the model output. `validate_answer` passes through a scrubbed answer only when validation is disabled. With no merged data it produces no validated answer; otherwise citation/structure validation uses an 85 quote-match threshold and treats exceptions as validation failure, never as approval. Follow-ups run only for a validated answer with a `user_id`; expected gateway failures return an empty list.

## State, durable history, and result projection

`RAGState` is primarily JSON-native. `messages` is the exception: it uses LangGraph's `add_messages` reducer for `HumanMessage` and `AIMessage`; `retrievals`, `route`, and `branch_events` are append-only reducer channels. `RetrieveInput` is intentionally narrower than full graph state and is the payload for each retrieval `Send`. The public output exposes only answer, follow-ups, safety, selected branch data, and error.

The checkpoint is the conversation store for an engine instance. Finalization appends a scrubbed human/AI pair with a shared ISO-8601 timestamp only when there is a nonempty answer. On the next turn, the gate scrubs stored messages, token-trims them, and derives processed history and history text. `seed_history()` supports migration-style insertion of scrubbed legacy turns into a thread checkpoint as messages.

`GraphEngine` uses `InMemorySaver` by default. `HC_RAG_CHECKPOINT=sqlite:...` selects an async SQLite saver, initializes its schema, and requires the `graph-sqlite` extra. A checkpointer is supplied at compile time only by the engine; the server-exported graph is compiled without that engine-owned saver. This is the main graph-engine boundary: nodes describe state transitions and resource use, while the engine owns compilation lifecycle, checkpoint choice, thread configuration, execution telemetry, and projection to the legacy/evaluation-shaped result.

During a turn the engine streams updates with `stream_mode="updates"` and `durability="exit"`, forwards node status and answers to an optional `QueryMonitor`, and retrieves the final checkpoint snapshot. It creates one `UsageRecorder` per turn. `build_result()` converts persisted state into answer, raw answer, contexts and retrieval identifiers, branch status, usage, timing, router telemetry, and error fields. `fold_branches()` sorts append-only events deterministically, so parallel completion order does not alter reported branch telemetry.

## Failure and privacy boundaries

Engine initialization calls `PrivacySanitizer.initialize()` before compilation, so missing privacy runtime prerequisites fail initialization rather than silently disabling scrubbing. It also rejects the unavailable `semantic_router` safety classifier selection. At the request boundary, tracing root inputs are scrubbed and tracing fails closed to an empty input map if scrubbing fails.

The engine catches unhandled turn exceptions: `PrivacyScanError` is retained as its specific error string; other failures become `PIPELINE_EXECUTION_FAILED`. If state cannot be read after a non-privacy error, it reports `PIPELINE_STATE_READ_FAILED` and projects an empty state. These errors are returned in the result record rather than re-raised by `run_turn` or `process_query`.

## Safe extension and operations

When adding a stage, add its state contract and reset behavior, canonical `NODE_*` constant and typed router target, builder registration/wiring, and focused routing/flow tests. Do not use a state field across turns unless the gate intentionally preserves it. A node that both updates state and decides the successor should compute its `Command.goto` from the post-update state, as the existing decision nodes do.

Changing decomposition caps, gap-fill behavior, retriever/reranker settings, disabled stages, query-response arm, or checkpoint URI changes runtime cost or behavior. `GraphEngine.describe()` reports the effective safety, retrieval, model, reranking, structured-output, and query-response settings for experiment comparison. Focused graph tests compile both graph variants, pin dynamic router behavior and Mermaid topology, exercise deterministic merging and capped fan-out, and cover fail-soft retrieval and validation behavior. Run `make test` before changing graph topology; use the evaluation workflow for behavior and cost comparisons.
