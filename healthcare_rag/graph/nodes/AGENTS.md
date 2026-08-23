<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# graph/nodes

## Purpose
The individual `StateGraph` node implementations wired together by
`graph/build.py`: safety classification, query preprocessing (context
extraction, clarification, decomposition), routing between direct response and
retrieval, retrieval + merge, retrieval-gap evaluation, and answer
generation/validation/follow-ups.

## Key Files
| File | Description |
|------|-------------|
| `__init__.py` | `render_display_answer(validated, notices)` — shared helper joining safety notices with the validated answer; mirrors `SafetyDecision.prefix_notices`. |
| `safety.py` | `safety_gate` node (`Command[GateTarget]`) — the mandatory first node on every query; deterministic regex pre-checks OR-ed with an LLM classification; scrubs the raw question channel; `_gate_command` computes `goto` via `routers.route_after_gate`. |
| `safety_classifier.py` | `LangChainSafetyGate(SafetyGate)` — the concrete `_llm_assess` implementation used by the graph's `safety_gate`, wrapping the `LangChainLLMGateway` call with a safe default `SafetyAssessment` on failure. |
| `safety_finalize.py` | `finalize` node — terminal node for both `build_pipeline` and the public graph; assembles the final answer from `safety_response` / `direct_response` / `validated`+`follow_ups`, scrubs PHI one last time. |
| `preprocess.py` | `extract_conversation_context`, `clarify_query`, `decompose_query` (`Command[DecomposeTarget]`, `_decompose_command` fans out via `routers.route_after_decompose`'s `Send` list); shares one module-level `GATEWAY` override hook for tests. |
| `query_or_respond.py` | `generate_query_or_respond` + `route_query_or_respond` (`Command[QueryOrRespondTarget]`) — decides `direct` vs `retrieve` via `graph.query_response.query_or_respond_decision`, handles `SocialIntent` short-circuiting via `processors.social_responses.social_response`. |
| `retrieve.py` | `retrieve_documents`, `merge_retrievals` (`Command[MergeTarget]`); `resolve_arm(backend)` picks the Weaviate/PageIndex/Pinecone search callable + its exception type; `accepts_limit` introspects the callable signature; wraps retrieval-arm exceptions (`WeaviateBaseError`, `PineconeException`). |
| `evaluate.py` | `evaluate_retrieval` (`Command[EvaluateCommandTarget]`) — judges the merged retrieval and either gap-fills (`Send` back to `retrieve_documents`) or moves on to `generate_answer`, per `routers.route_after_evaluate`. |
| `generate.py` | `generate_answer`, `validate_answer` (the most expensive stage — see `graph/AGENTS.md`), `generate_follow_ups`; falls back to a fixed "I'm sorry, I don't know" string (`_UNKNOWN_ANSWER`) when merged retrieval has no docs. |

## For AI Agents

### Working In This Directory
- Every node here that both updates state and picks a successor must return
  `Command[Literal[...]]` using the matching alias from `graph/routers.py`
  (`GateTarget`, `DecomposeTarget`, `MergeTarget`, `EvaluateCommandTarget`,
  `QueryOrRespondTarget`) and compute `goto` by calling that router's
  `route_after_*` function on its **own post-update state** — never return a
  bare string or hand-roll the routing decision inline in the node.
- `safety.py`'s `safety_gate` is the first node on every turn without
  exception; it clears the raw question channel and never puts a number with
  a clinical unit into a short-circuit response (see
  `tests/test_safety_gate.py`).
- Decomposition (`preprocess.py::decompose_query`) always merges back to one
  answer for the **original** query: sub-branches stop after retrieve and
  their documents are de-duplicated by `doc_id` before generation runs once.
- `retrieve.py::resolve_arm` is the single seam that swaps in the
  PageIndex/Pinecone A/B arms — an injected `Resources.hybrid_search` still
  wins over the `HC_RAG_RETRIEVER` env knob.
- The safety gate's short-circuit path returns `follow_ups == []` and sets
  `safety_outcome`; the engine also sets `monitor.raw_answer_event` (with no
  raw answer) so a UI waiting on the preliminary-answer event doesn't sit on
  its timeout — preserve that behavior if you touch `safety_finalize.py`.

### Testing Requirements
- See `graph/AGENTS.md`'s testing section — most `tests/graph/*.py` files
  exercise this package's nodes directly or through the compiled graph.
  `test_router_typing.py` specifically pins every node's `Command` literal to
  its router and the compiled graph's edges.

### Common Patterns
- Nodes call `get()` / `get_resources()` from `graph/resources.py` to reach
  the LLM gateway, search callables, and settings — never construct clients
  directly inside a node.
- `scrub_phi` from `processors/safety.py` is applied at the boundary of
  nearly every node before state leaves it.

## Dependencies

### Internal
- `healthcare_rag/graph/routers.py`, `graph/resources.py`, `graph/state.py`, `graph/history.py`, `graph/query_response.py`.
- `healthcare_rag/processors/*` (`safety`, `retrieval`, `pageindex_retrieval`, `pinecone_retrieval`, `rerank`, `generation`, `validation`, `social_responses`, `refusal_boundary`, `safety_responses`).
- `healthcare_rag/models/*` (`SafetyAssessment`, `RetrievalEvaluation`, `CitedAnswerResult`, `FollowUpQuestions`, etc.).

### External
- `langgraph.types.Command`/`Send`, `langsmith.run_helpers.traceable`, `weaviate.exceptions.WeaviateBaseError`, `pinecone.exceptions.PineconeException`, `anyio`.

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
