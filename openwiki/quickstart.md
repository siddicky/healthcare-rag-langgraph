---
type: wiki entrypoint
title: Healthcare RAG engineering guide
description: Source-grounded navigation for the healthcare RAG, coach product, server, safety controls, evaluations, and operations.
tags: [overview, navigation, rag]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-30T08:22:08.381Z
sources:
  - id: openwiki-source-4d1d392666be6dfdd7a91a2e
    resource: repo://.github/workflows/release.yml
  - id: openwiki-source-8037e2358a2c4f9b2c722a11
    resource: repo://AGENTS.md
  - id: openwiki-source-d6dbe2ca06d9e4feabdcde4d
    resource: repo://docs/decisions/dependabot-requirements-txt.md
  - id: openwiki-source-e2d8cb6620de3b4c16f6eab6
    resource: repo://docs/journey.json
  - id: openwiki-source-32b0d84a28d0c3a9400c33f6
    resource: repo://healthcare_rag/agent/coach_agent.py
  - id: openwiki-source-d29afe87b08650650d8273b0
    resource: repo://healthcare_rag/agent/rag_relay.py
  - id: openwiki-source-029ad9418d65d39851d3f024
    resource: repo://healthcare_rag/agent/tools/medical_lookup.py
  - id: openwiki-source-4637324e6e32c034a6095a28
    resource: repo://healthcare_rag/graph/build.py
  - id: openwiki-source-7772f43efa9811bd36483e17
    resource: repo://healthcare_rag/graph/llm.py
  - id: openwiki-source-56b79b6d8262f2037cd8bd60
    resource: repo://healthcare_rag/graph/nodes/retrieve.py
  - id: openwiki-source-aa698cddb837b0369bcb12cb
    resource: repo://healthcare_rag/graph/prompts.py
  - id: openwiki-source-84feefce1f4b71f9befa5c23
    resource: repo://healthcare_rag/processors/privacy.py
  - id: openwiki-source-87f98f33716569ae6b45609f
    resource: repo://healthcare_rag/processors/refusal_boundary.py
  - id: openwiki-source-6716f82708e52a00841d5c61
    resource: repo://healthcare_rag/processors/retrieval.py
  - id: openwiki-source-2548c11a25976cb64a4edf59
    resource: repo://healthcare_rag/processors/safety.py
  - id: openwiki-source-5bfd2a59ff90e1d4a18105f7
    resource: repo://healthcare_rag/processors/validation.py
  - id: openwiki-source-05c6c517a6da00d1f78ecc7d
    resource: repo://healthcare_rag/services/model_sampling.py
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-012f2c78e3b1446dfc35803f
    resource: repo://Makefile
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
  - id: openwiki-source-bf90e16d0f806741d36c310e
    resource: repo://scripts/next_version.py
  - id: openwiki-source-a7c96560a75972959888e56a
    resource: repo://server/registries.py
  - id: openwiki-source-d8bf193a74d78ce706478aa9
    resource: repo://server/storage.py
generated: { by: "openwiki/0.4.3", at: "2026-08-30T08:22:08.381Z" }
---

# Healthcare RAG engineering guide

This repository contains two related LangGraph products: a healthcare RAG over Lipitor and Metformin monographs, and a separately deployed member-facing coach graph with a protected HTTP perimeter. The public `StateGraph` built in `healthcare_rag/graph/build.py` is the only RAG runtime; the pre-port speculative-execution orchestrator that once lived under `healthcare_rag/orch/` was deleted in commit `3435caf` and is not part of the current architecture — do not plan changes against it or restore it. Weaviate provides default hybrid retrieval and OpenAI powers configured model stages. Start with [RAG architecture](architecture/overview.md), then route work using this map.

## System map

- [RAG architecture](architecture/overview.md) — public `StateGraph`, nodes, routing, state, checkpoints, and engine.
- [Processors and prompts](processors/overview.md) and [answer validation](processors/validation.md) — typed LLM contracts and fail-closed citations.
- [Safety gate](safety/gate.md), [safety posture](safety/posture.md), and [privacy sanitizer](privacy/sanitizer.md) — enforced medical and PHI boundaries.
- [Weaviate ingestion](retrieval/weaviate-and-ingestion.md) and [retrieval arms](retrieval/arms-and-reranking.md) — corpus/search and experimental alternatives.
- [Models and runtime](configuration/models-and-runtime.md) — environment-driven tiers, sampling, caps, and persistence.
- [Tracing and evaluations](observability/evaluations.md), [evaluation governance](observability/evaluation-governance.md), and [routing gates](observability/routing-evals.md) — experiments, calibration, reporting, and independent routing records.
- [Coach agent architecture](agent/coach.md), [coach routing and catalog](agent/coach-routing.md), [member perimeter](agent/member-perimeter.md), and [member data lifecycle](agent/member-data-lifecycle.md) — the separately deployed coach graph, its medical relay, authorization, member records, uploads, reminders, and erasure.
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
| Coach model/tool behavior or the medical-lookup relay into the RAG graph | [Coach agent architecture](agent/coach.md) | `healthcare_rag/agent/coach_agent.py`, `healthcare_rag/agent/tools/medical_lookup.py`, `healthcare_rag/agent/rag_relay.py` | `build_route_b_agent`, `medical_lookup`, `relay_question`, `relay_medical_answer` | `tests/agent/test_route_b.py`, `tests/agent/test_rag_relay.py` | `.venv/bin/python -m pytest -q tests/agent/test_route_b.py tests/agent/test_rag_relay.py` |
| Member auth, threads, attachments, or the stream perimeter version | [Member perimeter](agent/member-perimeter.md) | `healthcare_rag/agent/perimeter.py`, `healthcare_rag/agent/perimeter_middleware.py`, `server/` | `validate_member_request`, `MemberPerimeterMiddleware`, `HC_RAG_MEMBER_STREAM_PERIMETER` | `tests/agent/test_auth.py`, `tests/agent/test_perimeter_composed.py`, `tests/agent/test_perimeter_v2.py` | `.venv/bin/python -m pytest -q tests/agent/test_auth.py tests/agent/test_perimeter_composed.py tests/agent/test_perimeter_v2.py` |
| Upload, review, reminders, or erasure | [Member lifecycle](agent/member-data-lifecycle.md) | `healthcare_rag/agent/` | documents, store, reminders, erase | `tests/agent/test_documents.py`, `tests/agent/test_reminders.py`, `tests/test_forget_member.py` | `.venv/bin/python -m pytest -q tests/agent/test_documents.py tests/agent/test_reminders.py` |
| Browser protocol or catalog | [Member frontend](frontend/member-frontend.md) | `frontend/` | `coachApi`, stream reducer, catalog | frontend Vitest tests | `cd frontend && bun test` |
| Server or release topology, or the storage backend (memory/Postgres) | [Agent server](server/agent-server.md), [deployment](operations/deploy.md) | `server/app.py`, `server/storage.py`, `server/registries.py`, `.github/`, `deploy/` | `create_app`, `create_storage`, `PostgresRegistries`, run engine, Fly workflow | `tests/server/test_topology.py`, `tests/server/test_runs.py`, `tests/server/test_storage_postgres.py` | `make server-test` (add `make server-test-pg` for Postgres-path changes) |
| Release tagging, version bump, or rollback dispatch | [Deployment: release identity, version bumps, and rollback](operations/deploy.md#release-identity-version-bumps-and-rollback) | `.github/workflows/release.yml`, `.github/workflows/deploy.yml`, `scripts/next_version.py` | `next_version`, `classify`, `latest_release` | `tests/test_release_pipeline.py`, `tests/test_next_version.py` | `.venv/bin/python -m pytest -q tests/test_release_pipeline.py tests/test_next_version.py` |

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
