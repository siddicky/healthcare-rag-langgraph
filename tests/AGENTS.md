<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# tests

## Purpose
Fast, offline (no network, no Weaviate) pytest suite for everything the `evals/`
harness doesn't already regression-test: safety-gate behaviour, PHI scrubbing, the
refusal-boundary state machine, model-sampling rules, retrieval-arm unit logic, the
routing-gate and retriever-gate pure functions, and eval-artifact integrity (seal
cleanliness, parity gates). Graph runtime tests live in `graph/`, coach-agent
platform tests in `agent/`, and OSS Agent Server tests in `server/` — this
top-level directory holds cross-cutting and evals-adjacent tests plus one shared
fixtures folder.

## Key Files
| File | Description |
|------|-------------|
| `conftest.py` | Loads `.env`, forces `LANGSMITH_TRACING=false` for tests unless `HC_RAG_TEST_TRACING=true`, registers the `judge` marker |
| `fake_routing_arm_adapter.py` | Fake `ArmAdapter` for exercising `evals/routing_arm_runner.py` without a real subprocess |
| `routing_gate_cases.py`, `routing_gate_fixtures.py`, `routing_gate_publication_cases.py`, `routing_gate_runner_cases.py` | Shared parametrized case tables and fixtures for the `test_routing_gate*.py` suite (kept out of the test files themselves because the case tables are large and reused across several test modules) |
| `test_agent_eval.py` | `ChunkCatalog` resolution/rejection, the offline coach parity checker's regression naming, and that graph state carries no eval-only telemetry channels |
| `test_answer_validation.py` | Public `processors` export preserves the validator's identity (no accidental re-wrap) |
| `test_catalog_data_ref_fixture.py` | Backend `__ref` acceptance matches `tests/fixtures/catalog_data_refs.json` exactly |
| `test_cli_interactive.py` | Module entrypoints return nonzero on invalid init config |
| `test_eval_seal.py` | `evals/seal_clean.py` + provenance-manifest comparison: rejects dirty protected paths, hash/order drift, stale LangSmith rows, mixed lane settings |
| `test_evaluator_calibration.py` | Frozen routing-fixture cardinality/coverage, label-inversion isolation between lanes, non-finite/non-boolean score rejection, delimiter-breakout serialization safety |
| `test_evaluators.py` | Deterministic evaluators and LLM judges both match `evals/judge_calibration.json`; every evaluator declares its output keys |
| `test_forget_member.py` | Member self-erasure flow: AI-message marker latch, disconnected-stream polling, paginated snapshot-then-delete ordering, fail-stop on a non-current delete failure |
| `test_model_sampling.py` | `sampling_params()` model-family rules (temperature vs `reasoning_effort`), case-insensitive reasoning-model detection, env-override precedence, facade signature characterization |
| `test_multiturn_evaluators.py` | An answerable boundary replay is scored as a precision failure |
| `test_pageindex_gate.py` | Pure-function tests for `evals/pageindex_gate.py` — stage 1/2 verdict logic, eligible-item filtering, `build_outputs` shape, report persistence, CLI JSON/exit-code contract (no network, no LLM) |
| `test_pageindex_retrieval.py` | PageIndex tree-search adapter: node-to-page expansion, dedup, chunk cap, cache-miss error naming, retriever-backend knob parsing |
| `test_parity_gate.py` | `evals/parity.py` `ParityGate`: positive control passes, injected regressions/tampering fail, judge-median handling |
| `test_pinecone_retrieval.py` | Pinecone hybrid arm: convex dense/sparse scaling, page-number string round-trip, namespace derivation, vector building, knob validation, arm registry |
| `test_privacy_sanitizer.py` | PHI/PII scrubber: overlap unioning, LLM-span authority limits, cue-bound identifier detection (MRN, dates, names), oversized-input handling |
| `test_refusal_boundary.py` | The persisted-refusal-boundary state machine end to end: precedence, topic gating, upsert/refresh, inheritance rules for drugless queries, byte-identity of replayed templates |
| `test_rerank.py` | Pinecone Inference reranking: ranking order, top-k truncation, document integrity, malformed-ranking tolerance |
| `test_routing_dataset.py` | Routing dataset contract: authored-artifact loading, hash/round-trip stability, duplicate/wrong-cardinality/near-duplicate rejection, LangSmith sync id staleness |
| `test_routing_evaluators.py` | `evaluate_routing_records()` metric correctness, boundary-replay precision, non-finite/missing-field rejection |
| `test_routing_gate.py`, `test_routing_gate_runtime.py` | The gate's decision matrix (adopt/reject/inconclusive/error precedence) and its runner (subprocess protocol, batch publication rollback, calibration gating) |
| `test_routing_report.py`, `test_routing_report_batch.py`, `test_routing_report_integrity.py` | Routing report model round-trip, and the transactional temp-write/publish/rollback machinery under simulated failures at every phase |
| `test_routing_settings.py` | Routing env-knob defaults, round-trip, malformed-value errors, safe telemetry projection |
| `test_safety_gate.py` | The safety classifier and templates: PHI pattern detection, injection/identifier-recall/red-flag detection, "no template contains a specific dose," template content invariants |
| `test_seal_clean.py` | `is_clean_status()` parametrized over git-status lines; untracked-executable is dirty; explicit git error surfaces |
| `test_social_responses.py` | Social/capability direct-response channel routing and typed intent proposal validation |
| `test_tracing_privacy.py` | LangSmith tracing is fully disabled unless input-hiding is exactly enabled; `.env.example` ships privacy-safe defaults |
| `test_validation_scaffold_prefix.py` | (currently no test functions — scaffold/placeholder file; check before assuming coverage here) |
| `test_vector_store.py` | Weaviate connection uses cloud credentials when `url` is set; collection creation picks the right vectorizer for cloud |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `agent/` | Coach-agent platform tests: auth, gates, documents, features, streaming, memory, perimeter, reminders, store, tools (see `agent/AGENTS.md`) |
| `graph/` | LangGraph `StateGraph` runtime tests: build, routing, safety, privacy, history, resources (see `graph/AGENTS.md`) |
| `server/` | OSS Agent Server (clean-room) unit + parity tests, including `contract/` and `oracle/` (see `server/AGENTS.md`) |
| `fixtures/` | Small shared JSON fixtures used across multiple test modules (see `fixtures/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- This suite must stay network-free and Weaviate-free; anything that needs the real pipeline belongs in `evals/`, not here. `conftest.py` forces tracing off for exactly this reason.
- Tests marked `judge` call OpenAI and cost money (`make test-judges`); don't add a `judge`-marked test unless calibration against `evals/judge_calibration.json` genuinely requires an LLM call.
- Large parametrized case tables belong in a dedicated `*_cases.py` / `*_fixtures.py` module (see the `routing_gate_*` files) rather than inline in the test file, once a table is reused by more than one test module.
- When changing a safety template, refusal-boundary precedence rule, or PHI pattern, run the corresponding test file directly — these are the tests the `evals/README.md` safety-net language points at as "checked against."

### Testing Requirements
```
make test           # offline: evaluator calibration + deterministic subset (this whole tree, minus `judge`)
make test-judges     # adds the `judge`-marked LLM calibration tests (~$0.10)
uv run pytest tests/ -m "not judge"     # equivalent to make test, run directly
uv run pytest tests/test_refusal_boundary.py -q
```

### Common Patterns
- Test names are long and descriptive sentences (`test_<condition>_<expected_result>`) rather than short labels — preserve that style for new tests; it's what makes `pytest -k` usable as documentation.
- Fixtures shared across many test files live in `conftest.py` at the appropriate level (`tests/conftest.py`, `tests/agent/conftest.py`, `tests/graph/conftest.py`) — check there before adding a new local fixture.
- Golden/reference JSON belongs in `fixtures/` (top-level) or a test-local `fixtures/` subfolder (see `graph/fixtures/legacy_renders/`), never inlined as a huge string literal.

## Dependencies

### Internal
- `healthcare_rag/` (the app under test — safety, processors, graph, agent, storage)
- `evals/` (many pure-function modules here are unit-tested directly: `pageindex_gate`, `parity`, `routing_*`, `seal_clean`)

### External
- `pytest` (+ `pytest.mark.parametrize`, custom `judge` marker)
- `python-dotenv` (`.env` loading in `conftest.py`)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
