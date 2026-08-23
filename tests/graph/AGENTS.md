<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# tests/graph

## Purpose
Tests for the LangGraph `StateGraph` runtime (`healthcare_rag/graph/`) — the graph
build/compile shape, the pure routers, node privacy/safety behaviour, history
rendering, prompt fidelity against legacy renders, and the union-results merge
logic. This is the runtime the root `AGENTS.md` calls "the graph is THE runtime,"
so these tests are the primary guard against router/node contract drift.

## Key Files
| File | Description |
|------|-------------|
| `conftest.py` | Shared fixtures for graph tests (engine/state builders, fake resources) |
| `query_or_respond_fakes.py` | Fakes for the query-or-respond node used across several `test_query_or_respond*.py` files |
| `test_boundary_durability.py` | Persisted-refusal-boundary durability wiring at the graph level (complements `tests/test_refusal_boundary.py`'s calibration-level tests) |
| `test_branch_fold.py` | `fold_branches` handles out-of-order and randomized event arrival for decomposed sub-branches |
| `test_direct_graph_integration.py` | Direct-result projection only exposes direct channels; a refusal result suppresses conflicting direct/medical channels |
| `test_direct_output_policy.py` | The direct-response content policy: rejects action-target and clinical-unit transformations, prompt injection, PHI, factual medical prose; allows benign whole-token controls and context-sensitive social farewells |
| `test_engine_record.py` | `redact_root_inputs()` returns empty (not raw) when the scrubber itself fails |
| `test_evals_engine_contract.py` | The graph engine's eval-facing contract: multiturn slim output preserves `safety_outcome` per turn; engine description identifies the graph runtime |
| `test_graph_build.py` | Pipeline and full graph both compile (with and without an external checkpointer); full graph contains every runtime stage node; the follow-up node can be omitted for internal runs; compiled graph's mermaid matches the committed `docs/graph.mmd` |
| `test_graph_flow.py` | End-to-end flow through the compiled graph across representative query shapes |
| `test_graph_integration.py` | Broader integration coverage of node sequencing |
| `test_graph_privacy.py` | `Resources` owns exactly one sticky, ready sanitizer; initialization failure is raw-free and sticky (never partially leaks pre-scrub state) |
| `test_graph_privacy_persistence.py` | Privacy guarantees hold across checkpointed/persisted state, not just in-memory |
| `test_graph_routing.py` | Every pure router in `routers.py`: fan-out after the safety gate, finalize-on-refusal/direct-response, decompose parent/sub-query ordering and the `HC_RAG_MAX_SUBQUERIES` cap, merge-vs-evaluate selection, capped gap-round metadata |
| `test_graph_safety.py` | Large suite asserting refusal templates never contain a numeric clinical unit (the "never puts a number with a clinical unit in a refusal" invariant from the root `AGENTS.md`) |
| `test_history.py` | `build_history_views` legacy window/order preservation, PHI scrubbing when the gate is on, token-cap-before-windows ordering; `seed_messages` scrubbing; follow-up history uses five newest entries; display-answer notice prefixing |
| `test_prompt_fidelity.py` | The Jinja prompt registry's rendered output matches frozen legacy renders byte-for-byte (`fixtures/legacy_renders/*.json`), independent of cwd; unknown stage/role rejection; lazy resolution from `GraphResources` |
| `test_query_or_respond.py` | The query-or-respond LLM contract keeps its legacy re-export shape |
| `test_query_or_respond_direct_safety.py` | Direct-response safety checks specific to the query-or-respond node |
| `test_query_or_respond_factual_output.py` | Factual-output shape/content checks for query-or-respond |
| `test_query_or_respond_privacy.py` | PHI/PII handling specific to the query-or-respond node |
| `test_resources.py` | `GraphResources` construction and lazy dependency wiring |
| `test_route_tools.py` | Settings still carry the legacy Weaviate collection names |
| `test_router_typing.py` | Static contract: `Literal` targets in `Command[Literal[...]]` match the node-name constants; return annotations are `Literal`-based; self-routing nodes declare their own targets; compiled graph edges match the router `Literal`s; `validate_answer`'s "finalize" maps to `END` when the pipeline omits follow-ups |
| `test_safety_node_exports.py` | The safety node's old and new import paths resolve to identical coroutine objects/signatures (a refactor-safety net for the `SafetyGate` subclassing described in the root `AGENTS.md`) |
| `test_settings.py` | `HC_RAG_REFUSAL_BOUNDARY` env parsing: defaults enabled, accepts `false`, rejects invalid booleans; `GraphSettings` reflects both |
| `test_state.py` | Overwrite channels reset while `messages` accumulates; the raw question channel is run-local and absent from both successful and failed checkpoints; explicit I/O schemas exclude it from results; query-results serialization round-trips |
| `test_union_results.py` | `union_results()` dedups by first occurrence and groups by source; `format_documents_for_prompt` matches both object and state-dict document shapes |
| `test_validation_privacy.py` | PHI/PII handling in the `validate_answer` stage |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `fixtures/legacy_renders/` | Frozen JSON renders of every prompt template (`safety_gate__autoescape.json`, `generate_answer__*.json`, `decompose_query__apostrophe.json`, `evaluate_retrieval__multiple_documents.json`, `clarify_query__conversation_context.json`, `validate_answer__formatted_documents.json`) — `test_prompt_fidelity.py`'s byte-identity baseline |

## For AI Agents

### Working In This Directory
- The router/node-name contract enforced by `test_router_typing.py` and `test_graph_build.py::test_compiled_graph_mermaid_matches_committed_artifact` is strict: a node returning `Command[Literal[...]]` gets **no** edge in `build.py`, and its `Literal` targets must match the router in `routers.py` exactly (see the root `AGENTS.md` "Known gotchas" — a `X | Y` union inside `Command[...]` renders no edges, so nest unions instead). Changing a router's return type without updating both the graph build and `docs/graph.mmd` will fail this suite.
- Changing a prompt template requires regenerating the matching fixture in `fixtures/legacy_renders/` — `test_prompt_fidelity.py` compares byte-for-byte, not semantically.
- Any refusal-template change must keep `test_graph_safety.py`'s "no numeric clinical unit" invariant; run that file specifically after touching `healthcare_rag/processors/safety_responses.py`.
- The decomposer's non-determinism (same query can route `simple` or `complex` across calls) means `test_graph_routing.py`'s decompose tests assert on router *logic* given a fixed classification, not on the classifier's actual output — don't add a test that depends on the decomposer's live decision.

### Testing Requirements
```
uv run pytest tests/graph/ -q
uv run pytest tests/graph/test_router_typing.py tests/graph/test_graph_build.py -q   # after any router/build.py change
uv run pytest tests/graph/test_prompt_fidelity.py -q                                  # after any prompt template change
```

### Common Patterns
- Router tests call the pure functions in `routers.py` directly with constructed state, rather than running the compiled graph — keep new router tests at that same pure-function level; save full-graph exercises for `test_graph_flow.py`/`test_graph_integration.py`.
- Fixture-backed byte-identity tests (`test_prompt_fidelity.py`, parts of `test_refusal_boundary.py` in the parent `tests/`) load a JSON fixture and assert on exact string equality — when a legitimate change requires updating a fixture, regenerate it deliberately and note why in the commit, don't hand-edit the JSON.

## Dependencies

### Internal
- `healthcare_rag/graph/` — `build.py`, `routers.py`, `nodes/`, `engine.py`, `state.py`, `settings.py`, `resources.py`, `history.py` (the system under test)
- `healthcare_rag/prompts/*.yaml.j2` (source of the fixtures in `fixtures/legacy_renders/`)
- `docs/graph.mmd` (the committed mermaid artifact `test_graph_build.py` checks against)

### External
- `langgraph` (`StateGraph`, `Command`, checkpointer types)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
