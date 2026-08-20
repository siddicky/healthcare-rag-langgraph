---
type: wiki entrypoint
title: Healthcare RAG engineering guide
description: Navigate the speculative healthcare RAG repository safely, from runtime architecture to retrieval, validation, operations, safety, and evaluation.
tags: [overview, navigation, rag]
---

# Healthcare RAG engineering guide

This repository is an async RAG assistant over Lipitor and Metformin monograph chunks. The runtime is a LangGraph `StateGraph` (`healthcare_rag/graph/`) with a runtime safety gate, query clarification/decomposition, capped fan-out retrieval, gap-fill, validated citations, and follow-ups; Weaviate supplies hybrid retrieval; OpenAI supplies routing and LLM stages. Start with [architecture](architecture/overview.md) for runtime behavior, then use the task map below rather than hunting directories.

## Map

- [Architecture](architecture/overview.md) — LangGraph graph topology, node ordering, fan-out/gap-fill routing, engine lifecycle and state.
- [Processors and prompts](processors/overview.md) — complete prompt/model/Pydantic contract map.
- [Answer validation](processors/validation.md) — citation evidence and fallback semantics.
- [Retrieval and ingestion](retrieval/weaviate-and-ingestion.md) — routing tools, hybrid values, schema, corpus lifecycle.
- [Models and runtime state](configuration/models-and-runtime.md) — environment, GPT reasoning compatibility, stage switches, checkpointed history.
- [Runbook](operations/runbook.md) — setup, Docker, ingestion, CLI, LangGraph dev server, recovery.
- [Tracing and evaluations](observability/evaluations.md) — LangSmith plus single-/multi-turn regression workflows and the parity gate.
- [Safety gate](safety/gate.md) — runtime pre-pipeline guard: PHI scrubbing, classification, templated refusals.
- [Safety posture](safety/posture.md) — safeguards, gaps, and required safety evidence.

## Route a change

| Intent | Read first | Owning source symbols | Focused validation |
|---|---|---|---|
| alter graph topology, node order, or fan-out | [Architecture](architecture/overview.md) | `build_graph`/`add_pipeline`, `route_after_*` | `make test` (`tests/graph/test_graph_build.py`, `test_graph_routing.py`); `make eval-smoke` |
| change decomposition cap or complexity gate | [Architecture](architecture/overview.md), [Models](configuration/models-and-runtime.md) | `decompose_query`, `route_after_decompose`, `max_subqueries` | `make test`; filtered factual/cross-drug eval |
| change a prompt or typed LLM output | [Processors](processors/overview.md) | `PromptRegistry` (`STAGE_FILES`/`RESPONSE_MODELS`), graph node | `make test` (`tests/graph/test_prompt_fidelity.py`), `make eval-smoke`, then filtered/full eval |
| change citations or fallback answers | [Validation](processors/validation.md) | `AnswerValidator.structure_and_validate_async`, `validate_answer` node | deterministic eval then judge groundedness |
| tune retrieval/schema/corpus | [Retrieval](retrieval/weaviate-and-ingestion.md) | `build_routing_tools`, `hybrid_search`, `EXPECTED_PROPERTIES` | `make test` (`tests/graph/test_route_tools.py`, `test_union_results.py`); filtered factual eval |
| swap model or adjust sampling | [Models](configuration/models-and-runtime.md) | `sampling_params`, `LangChainLLMGateway.chat_model` | compare equal-config experiments |
| change history/multi-turn behavior | [Architecture](architecture/overview.md), [Evaluations](observability/evaluations.md) | `build_history_views`, `seed_messages`, `finalize`, checkpointer | `make test` (`tests/graph/test_history.py`); multi-turn carryover/safety/latency profile |
| refactor without intended behavior change | [Evaluations](observability/evaluations.md) | `build_result`, parity gate | `make test` (`tests/test_parity_gate.py`) — graph refactors must reproduce sealed baselines |
| operate local stack/rebuild data | [Runbook](operations/runbook.md) | Makefile, Compose, vector loader | ready probe, narrow retrieval eval |
| change medical safety/privacy behavior | [Safety gate](safety/gate.md), [Safety posture](safety/posture.md) | `safety_gate` node, `SafetyGate.evaluate`, `scrub_phi`, `safety_responses.py`, `safety_gate_enabled` | `make test` (`tests/test_safety_gate.py`, `tests/graph/test_graph_safety.py`); filtered safety eval + multi-turn safety run |

## First local command sequence

```bash
make venv
make weaviate
make ingest
make run
```

Use Python >=3.11; `make venv` selects 3.12. `make ingest` deletes every existing Weaviate collection, so read [runbook](operations/runbook.md) first. `OPENAI_API_KEY` is required; do not place secret values in docs or source.

## Boundaries and backlog

No source area is intentionally deferred. Existing production conversation files, PDFs, eval result artifacts, environment files, and other ignored paths are intentionally not inspected/documented because they may contain sensitive, generated, or excluded content. The offline pytest suite (`make test`, `tests/`) regression-tests the eval graders (`test_evaluators.py`), the full LangGraph runtime (`tests/graph/` — build, routing, flow, safety, history, state, branch folding, prompt fidelity, routing tools, union, engine record, evals-engine contract), and the parity gate (`test_parity_gate.py`, `test_seal_clean.py`); the eval harnesses remain the primary validation path for end-to-end runtime behavior. Design rationale and experiment history live in `docs/journey.json` (`make journey` rebuilds `docs/journey.html`).
