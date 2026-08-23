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

| Change intent | Read first | Owning surface | Narrow validation |
|---|---|---|---|
| RAG node order, state, branching | [Architecture](architecture/overview.md) | `build_graph`, `add_pipeline`, `route_after_*`, `RAGState` | `make test`; `make eval-smoke` |
| Medical refusal, PHI, history | [Safety gate](safety/gate.md) | `SafetyGate`, `safety_gate`, `scrub_phi`, boundaries | safety/privacy graph tests; filtered plus multi-turn eval |
| Prompt, model, structured output | [Processors](processors/overview.md) | `PromptRegistry`, gateway, response model | prompt-fidelity tests; equal-config eval |
| Corpus, Weaviate, retrieval arm | [Retrieval](retrieval/weaviate-and-ingestion.md) | `hybrid_search`, `resolve_arm`, loader | retrieval tests; factual no-judge eval |
| Citation rendering or fallback | [Validation](processors/validation.md) | `AnswerValidator`, `validate_answer` | `tests/test_answer_validation.py`; eval |
| Evaluation/data/gate/report | [Evaluation governance](observability/evaluation-governance.md) | dataset, calibration, provenance, report modules | relevant eval/parity/gate tests |
| Coach routing or generated UI | [Coach routing](agent/coach-routing.md) | `coach_gate`, `rag_relay`, `compose_ui` | coach/Route-B tests; `make eval-agent` |
| Member auth, threads, attachments | [Member perimeter](agent/member-perimeter.md) | auth, perimeter middleware | auth/perimeter tests |
| Upload, review, reminders, erasure | [Member lifecycle](agent/member-data-lifecycle.md) | documents, store, reminders, erase | documents/store/reminder tests |
| Browser protocol/catalog | [Member frontend](frontend/member-frontend.md) | `coachApi`, stream reducer, catalog | Bun tests/build; conditional E2E |
| Server/release topology | [Agent server](server/agent-server.md), [deployment](operations/deploy.md) | `create_app`, run engine, Fly workflow | `make server-test`; `make parity`; conditional smoke |

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
