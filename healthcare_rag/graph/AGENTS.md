<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# graph

## Purpose
The LangGraph `StateGraph` that is the actual RAG runtime (the pre-port
speculative orchestrator was deleted in Phase 2 — this package is not an
alternative implementation, it's the only one). Contains the graph shape
(`build.py`), the pure routing decisions the dynamic nodes consult
(`routers.py`), the runtime engine that executes turns with isolated
telemetry (`engine.py`), state/serialization boundaries (`state.py`),
settings sourced from `services/models.py` (`settings.py`), lazily-constructed
external resources (`resources.py`), history/message projection
(`history.py`), the LangChain LLM gateway (`llm.py`), and prompt loading
(`prompts.py`).

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | Compiles and exposes the public graph: `graph = build_graph().compile(name="healthcare_rag")`. |
| `build.py` | `PipelineInput`/`add_pipeline`/`build_pipeline`/`build_graph` — builds the internal pipeline and the public healthcare graph. Nodes that both update state and pick a successor return `Command[Literal[...]]` (`safety_gate`, `decompose_query`, `merge_retrievals`, `evaluate_retrieval`) and get **no** edge wired here; `validate_answer` is the one exception needing an explicit path map because its "finalize" target differs between `build_pipeline` (`END`) and the public graph (`finalize`). |
| `routers.py` | Pure routing functions read by the `Command`-returning nodes on their own post-update state: `route_after_gate`, `route_after_query_or_respond`, `route_after_decompose`, `route_after_merge`, `route_after_evaluate`, `route_after_validate`. Also the canonical node-name constants (`NODE_SAFETY`, `NODE_RETRIEVE`, etc.) and the `Literal` type aliases (`GateTarget`, `DecomposeTarget`, `MergeTarget`, `EvaluateCommandTarget`) that LangGraph reads to render dynamic edges — pinned by `tests/graph/test_router_typing.py`. |
| `state.py` | `RAGState` TypedDict (the graph's JSON-native domain state; `messages` is the sole `BaseMessage`/`add_messages`-channel exception), `RetrieveInput`, `GraphInput`/`GraphOutput`, `dump_results`/`load_results` (QueryResultList JSON round-trip). |
| `settings.py` | `GraphSettings` — immutable dataclass snapshotting all env-overridable knobs from `services/models.py` (retriever/reranker backend, disabled stages, subquery caps, refusal-boundary toggle, etc.) once per engine build. |
| `resources.py` | `Resources` — lazily-constructed, process-wide singleton bundling the Weaviate client, `LangChainLLMGateway`, `PrivacySanitizer`, and `GraphSettings`; `get()`/`override()` for test injection; no import-time network connections. |
| `engine.py` | `GraphEngine` (implements the `Engine` protocol) and `build_engine()` — runs the compiled graph per turn with an isolated `UsageRecorder`, `InMemorySaver` checkpointing, LangSmith `@traceable` root run, PHI-redacted root inputs (`_redact_root_inputs`); `SafetyClassifierUnavailableError`. |
| `engine_record.py` | `build_result`/`fold_branches`/`ResultContext`/`TurnTiming` — deterministic projection of final graph state into the legacy evaluation record shape (`safety_outcome`, timing, router telemetry with PHI-sensitive keys scrubbed via `ROUTER_SENSITIVE_KEYS`). |
| `engine_usage.py` | `UsageRecorder` — an `AsyncCallbackHandler` that accumulates per-turn LLM call usage/timing; one instance belongs to exactly one turn. |
| `history.py` | Checkpoint-message views: `build_history_views`, `seed_messages`, `render_history_text`, `render_followup_history`; `_scrub_message` PHI-scrubs every message before it's rendered into a prompt. |
| `llm.py` | `LangChainLLMGateway` — the single `ChatOpenAI` call surface graph nodes use; routes through `services.models.sampling_params()` so reasoning-model constraints (no bare `temperature`) are respected. |
| `prompts.py` | `PromptRegistry`/`get_registry()` — loads the Jinja YAML prompts from `healthcare_rag/prompts/` and renders them against the Pydantic response models in `healthcare_rag/models/`. |
| `query_response.py` | `QueryOrRespondDecision`, `RouterAction`, `QUERY_OR_RESPOND_TOOL` (the `retrieve_monographs` function-calling schema), `query_or_respond_decision`, `project_history`, `scrub_router_text`. |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `nodes/` | The individual graph node implementations (`safety.py`, `preprocess.py`, `retrieve.py`, `evaluate.py`, `generate.py`, `query_or_respond.py`) that `build.py` wires together (see `nodes/AGENTS.md`). |

## For AI Agents

### Working In This Directory
- The `Command[Literal[...]]` vs. conditional-edge split in `build.py` is the
  central invariant of this package: if a node computes its own successor, it
  must return `Command[Literal[...]]` and must **not** get an edge added in
  `build.py`; a `X | Y` union of `Literal`s inside `Command[...]` renders **no**
  edges at all — nest the literals in a single alias instead (see
  `routers.py`'s docstring). `tests/graph/test_router_typing.py` pins the
  literals to the node constants, the `Command` annotations, and the compiled
  graph's actual edges — run it after touching any router or node signature.
- `validate_answer` is the one node that stays on a conditional edge with an
  explicit path map, because its "finalize" target is `finalize` in the
  public graph and `END` in `build_pipeline`.
- Chunk ids are stored as `id_` in Weaviate (`id` is a reserved property
  name) — this surfaces through `resources.py`/`processors/retrieval.py`.
- `resources.py` must stay import-time-connection-free; `Resources` is a
  lazy singleton so tests can `override()` it without touching a real
  Weaviate/OpenAI connection.
- `validate_answer` (in `nodes/generate.py`) is by far the most expensive
  stage — look there first when optimizing cost/latency.

### Testing Requirements
- `tests/graph/` is extensive and mirrors this package closely:
  `test_graph_build.py`/`test_router_typing.py` (`build.py`/`routers.py`
  invariants), `test_graph_routing.py`/`test_graph_flow.py` (routing
  behavior), `test_graph_integration.py`/`test_direct_graph_integration.py`
  (end-to-end), `test_engine_record.py` (`engine_record.py`),
  `test_resources.py` (`resources.py`), `test_settings.py` (`settings.py`),
  `test_state.py` (`state.py`), `test_history.py` (`history.py`),
  `test_query_or_respond*.py` (`query_response.py` + node), `test_graph_safety.py`
  / `test_boundary_durability.py` (safety gate + refusal boundary wiring),
  `test_graph_privacy*.py` (PHI scrubbing at graph boundaries),
  `test_prompt_fidelity.py` (`prompts.py`), `test_union_results.py`,
  `test_route_tools.py`, `test_evals_engine_contract.py`,
  `test_branch_fold.py` (decomposition merge behavior),
  `test_safety_node_exports.py`.

### Common Patterns
- Every node function signature takes `dict[str, Any]` or `RAGState` and
  returns either a plain state-update dict or `Command[SomeTarget]` — never a
  bare node-name string.
- Logging goes through `logging.getLogger("MedicalRAG")`.

## Dependencies

### Internal
- `healthcare_rag/processors/*` — the actual LLM-calling/pure-logic work each node delegates to.
- `healthcare_rag/models/*` — Pydantic response models rendered by `prompts.py`.
- `healthcare_rag/services/models.py` — model selection/sampling, all env knobs `settings.py` snapshots.
- `healthcare_rag/agent/rag_relay.py` depends on this package's `build_graph()` in the other direction — `relay_question()` is called by the coach agent's `medical_lookup` tool.

### External
- `langgraph` (`StateGraph`, `Command`, `Send`, `InMemorySaver`), `langchain_core.messages`, `langchain_openai.ChatOpenAI`, `weaviate` (client types), `langsmith` (`traceable`).

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
