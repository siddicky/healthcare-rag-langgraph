---
type: wiki entrypoint
title: Healthcare RAG engineering guide
description: Source-grounded navigation for the healthcare RAG, coach product, server, safety controls, evaluations, and operations.
tags: [overview, navigation, rag]
---

# Healthcare RAG engineering guide

This repository contains two related LangGraph products: a healthcare RAG over Lipitor and Metformin monographs, and a member-facing coach application with a protected HTTP perimeter. The RAG graph is the runtime; Weaviate provides default hybrid retrieval and OpenAI powers configured model stages. Start with [RAG architecture](architecture/overview.md), then route work using this map.

## System map

- [RAG architecture](architecture/overview.md) — public `StateGraph`, nodes, routing, state, checkpoints, and engine.
- [Processors and prompts](processors/overview.md) and [answer validation](processors/validation.md) — typed LLM contracts and fail-closed citations.
- [Safety gate](safety/gate.md), [safety posture](safety/posture.md), and [privacy sanitizer](privacy/sanitizer.md) — enforced medical and PHI boundaries.
- [Weaviate ingestion](retrieval/weaviate-and-ingestion.md) and [retrieval arms](retrieval/arms-and-reranking.md) — corpus/search and experimental alternatives.
- [Models and runtime](configuration/models-and-runtime.md) — environment-driven tiers, sampling, caps, and persistence.
- [Tracing and evaluations](observability/evaluations.md), [evaluation governance](observability/evaluation-governance.md), and [routing gates](observability/routing-evals.md) — experiments, calibration, reporting, and independent routing records.
- [Coach routing and catalog](agent/coach-routing.md), [member perimeter](agent/member-perimeter.md), and [member data lifecycle](agent/member-data-lifecycle.md) — coach behavior, authorization, member records, uploads, reminders, and erasure.
- [Member frontend](frontend/member-frontend.md) and [clean-room agent server](server/agent-server.md) — browser protocol and service runtime.
- [Runbook](operations/runbook.md) and [deployment](operations/deploy.md) — local setup, topology, release, readiness, and smoke acceptance.
- [AI-assisted workflow](contributing/ai-assisted.md) and [scope and decisions](decisions/submission-scope.md) — inherited rules, verification loops, and evidence boundaries.

## Task routing

| Change area or user intent | Relevant wiki page | Exact source entry points | Important symbols or types | Focused tests | Minimal validation command |
|---|---|---|---|---|---|
| RAG node order, state, or conditional branching | [Architecture](architecture/overview.md) | `healthcare_rag/graph/build.py`, `healthcare_rag/graph/routers.py`, `healthcare_rag/graph/state.py` | `build_graph`, `add_pipeline`, `route_after_*`, `RAGState`, `RetrieveInput` | `tests/graph/test_graph_build.py`, `test_graph_routing.py`, `test_router_typing.py` | `.venv/bin/python -m pytest -q tests/graph/test_graph_build.py tests/graph/test_graph_routing.py tests/graph/test_router_typing.py` |
| Medical refusal, PHI, or checkpointed history | [Safety gate](safety/gate.md), [privacy sanitizer](privacy/sanitizer.md) | `healthcare_rag/graph/nodes/safety.py`, `healthcare_rag/processors/safety.py`, `healthcare_rag/processors/privacy.py` | `safety_gate`, `SafetyGate`, `SafetyOutcome`, `scrub_phi`, `PrivacySanitizer`, `RefusalBoundary` | `tests/test_safety_gate.py`, `tests/test_privacy_sanitizer.py`, `tests/test_refusal_boundary.py` | `.venv/bin/python -m pytest -q tests/test_safety_gate.py tests/test_privacy_sanitizer.py tests/test_refusal_boundary.py` |
| Prompt, model, or structured-output contract | [Processors](processors/overview.md), [models and runtime](configuration/models-and-runtime.md) | `healthcare_rag/graph/prompts.py`, `healthcare_rag/graph/llm.py`, `healthcare_rag/services/model_sampling.py` | `PromptRegistry`, `RESPONSE_MODELS`, `LangChainLLMGateway`, `sampling_params` | `tests/graph/test_prompt_fidelity.py`, `tests/test_model_sampling.py` | `.venv/bin/python -m pytest -q tests/graph/test_prompt_fidelity.py tests/test_model_sampling.py` |
| Corpus, Weaviate schema, or retrieval arm | [Weaviate ingestion](retrieval/weaviate-and-ingestion.md), [retrieval arms](retrieval/arms-and-reranking.md) | `healthcare_rag/storage/vector_store.py`, `healthcare_rag/graph/nodes/retrieve.py`, `healthcare_rag/processors/pdf_chunker.py` | `hybrid_search`, `resolve_arm`, `retrieve_documents`, `DocumentChunkProcessor` | `tests/graph/test_union_results.py`, `tests/test_pageindex_retrieval.py`, `tests/test_pinecone_retrieval.py` | `.venv/bin/python -m pytest -q tests/graph/test_union_results.py` |
| Citation rendering, answer fallback, or grounding boundary | [Answer validation](processors/validation.md) | `healthcare_rag/processors/validation.py`, `healthcare_rag/graph/nodes/generate.py` | `AnswerValidator`, `structure_and_validate_async`, `validate_answer`, `CitedAnswerResult` | `tests/test_answer_validation.py`, `tests/graph/test_validation_privacy.py` | `.venv/bin/python -m pytest -q tests/test_answer_validation.py` |
| Evaluation dataset, evaluator, comparison, or routing gate | [Tracing and evaluations](observability/evaluations.md), [evaluation governance](observability/evaluation-governance.md), [routing gates](observability/routing-evals.md) | `evals/dataset.py`, `evals/evaluators.py`, `evals/run_baseline.py`, `evals/routing_gate.py` | `run_one`, `EVALUATOR_KEYS`, `run_gate`, `Lane`, `ArmName` | `tests/test_evaluator_calibration.py`, `tests/test_routing_gate.py`, `tests/test_parity_gate.py` | `.venv/bin/python -m pytest -q tests/test_evaluator_calibration.py` |
| Trace enablement or trace privacy | [Tracing and evaluations](observability/evaluations.md), [privacy sanitizer](privacy/sanitizer.md) | `healthcare_rag/services/tracing.py`, `healthcare_rag/graph/engine.py` | `enforce_input_hiding`, `traceable`, `rag_stage`, `_redact_root_inputs` | `tests/test_tracing_privacy.py`, `tests/test_redact_smoke_log.py` | `.venv/bin/python -m pytest -q tests/test_tracing_privacy.py tests/test_redact_smoke_log.py` |
| Coach routing or generated UI | [Coach routing](agent/coach-routing.md) | `healthcare_rag/agent/`, `evals/coach_engine.py` | `coach_gate`, `rag_relay`, `compose_ui` | `tests/agent/test_coach_gate.py`, `tests/agent/test_route_b.py`, `tests/agent/test_rag_relay.py` | `.venv/bin/python -m pytest -q tests/agent/test_coach_gate.py tests/agent/test_route_b.py tests/agent/test_rag_relay.py` |
| Member auth, threads, or attachments | [Member perimeter](agent/member-perimeter.md) | `healthcare_rag/agent/`, `server/` | auth and perimeter middleware | `tests/agent/test_auth.py`, `tests/agent/test_perimeter_composed.py` | `.venv/bin/python -m pytest -q tests/agent/test_auth.py tests/agent/test_perimeter_composed.py` |
| Upload, review, reminders, or erasure | [Member lifecycle](agent/member-data-lifecycle.md) | `healthcare_rag/agent/` | documents, store, reminders, erase | `tests/agent/test_documents.py`, `tests/agent/test_reminders.py`, `tests/test_forget_member.py` | `.venv/bin/python -m pytest -q tests/agent/test_documents.py tests/agent/test_reminders.py` |
| Browser protocol or catalog | [Member frontend](frontend/member-frontend.md) | `frontend/` | `coachApi`, stream reducer, catalog | frontend Vitest tests | `cd frontend && bun test` |
| Server or release topology | [Agent server](server/agent-server.md), [deployment](operations/deploy.md) | `server/`, `.github/`, `deploy/` | `create_app`, run engine, Fly workflow | `tests/server/test_topology.py`, `tests/server/test_runs.py` | `make server-test` |

## Safe local path

```bash
make venv
make weaviate
make ingest
make run
```

Python 3.11+ is required; `make venv` selects Python 3.12 and installs the locked project extras. `OPENAI_API_KEY` is required. `make ingest` uses `--delete-all`, so it removes every local Weaviate collection. Read the [runbook](operations/runbook.md) before running it. The obsolete root `requirements.txt` must not be restored; `pyproject.toml` and `uv.lock` are the dependency authority.

## Verification policy

Use the narrowest offline test first, then a real-pipeline evaluation when changing model-visible behavior, retrieval, safety, or prompts. Compare only equal dataset/evaluator/configuration runs. A smoke, capability branch, prompt instruction, or report file is not proof that an experiment ran. In particular, production defaults remain `HC_RAG_QUERY_RESPONSE_ARM=current` and `HC_RAG_SAFETY_CLASSIFIER=llm`; [routing gates](observability/routing-evals.md) records the independent, currently inconclusive evidence without treating it as adoption or quality evidence.

## Backlog

No inspected substantial component is deferred. Excluded PDFs, production conversations, `.env` files, and evaluation result artifacts are intentionally not inspected or reproduced because the repository policy treats them as sensitive, generated, or out of scope.
