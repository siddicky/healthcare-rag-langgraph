---
type: wiki entrypoint
title: Healthcare RAG engineering guide
description: Navigate the speculative healthcare RAG repository safely, from runtime architecture to retrieval, validation, operations, safety, and evaluation.
tags: [overview, navigation, rag]
---

# Healthcare RAG engineering guide

This repository is an async RAG assistant over Lipitor and Metformin monograph chunks. The normal CLI uses speculative query refinement and validation; Weaviate supplies hybrid retrieval; OpenAI supplies routing and LLM stages. Start with [architecture](architecture/overview.md) for runtime behavior, then use the task map below rather than hunting directories.

## Map

- [Architecture](architecture/overview.md) — branch creation/supersession, stage ordering, selection, linear and streaming alternatives.
- [Processors and prompts](processors/overview.md) — complete prompt/model/Pydantic contract map.
- [Answer validation](processors/validation.md) — citation evidence and fallback semantics.
- [Retrieval and ingestion](retrieval/weaviate-and-ingestion.md) — router, hybrid values, schema, corpus lifecycle.
- [Models and runtime state](configuration/models-and-runtime.md) — environment, GPT reasoning compatibility, stage switches, history state.
- [Runbook](operations/runbook.md) — setup, Docker, ingestion, commands, recovery.
- [Tracing and evaluations](observability/evaluations.md) — LangSmith plus single-/multi-turn regression workflows.
- [Safety gate](safety/gate.md) — runtime pre-pipeline guard: PHI scrubbing, classification, templated refusals.
- [Safety posture](safety/posture.md) — safeguards, gaps, and required safety evidence.

## Route a change

| Intent | Read first | Owning source symbols | Focused validation |
|---|---|---|---|
| alter speculative ordering or winning answer | [Architecture](architecture/overview.md) | `RefactoredOrchestrator`, `TaskHandler`, `ProcessingBranch` | `make test`; `make eval-smoke`; ambiguous follow-up filtered baseline |
| change decomposition cap, synthesis, or complexity gate | [Architecture](architecture/overview.md), [Models](configuration/models-and-runtime.md) | `handle_decompose_result`, `_synthesize_group`, `max_subqueries`/`synthesis_enabled` | `make test` (synthesis suite); filtered factual/cross-drug eval |
| change a prompt or typed LLM output | [Processors](processors/overview.md) | `PromptManager`, processor method, Pydantic model | `make eval-smoke`, then filtered/full eval |
| change citations or fallback answers | [Validation](processors/validation.md) | `AnswerValidator.structure_and_validate_async` | deterministic eval then judge groundedness |
| tune retrieval/schema/corpus | [Retrieval](retrieval/weaviate-and-ingestion.md) | `QueryRouter._process_tool_call`, `EXPECTED_PROPERTIES` | filtered factual eval with routing/chunk/page metrics |
| swap model or adjust sampling | [Models](configuration/models-and-runtime.md) | `sampling_params`, model defaults | compare equal-config experiments |
| change history/multi-turn behavior | [Architecture](architecture/overview.md), [Evaluations](observability/evaluations.md) | `ConversationHistory`, multi-turn harness | multi-turn carryover/safety/latency profile |
| operate local stack/rebuild data | [Runbook](operations/runbook.md) | Makefile, Compose, vector loader | ready probe, narrow retrieval eval |
| change medical safety/privacy behavior | [Safety gate](safety/gate.md), [Safety posture](safety/posture.md) | `SafetyGate.evaluate`, `scrub_phi`, `safety_responses.py`, `safety_gate_enabled` | `make test` (`tests/test_safety_gate.py`); filtered safety eval + multi-turn safety run |

## First local command sequence

```bash
make venv
make weaviate
make ingest
make run
```

Use Python >=3.11; `make venv` selects 3.12. `make ingest` deletes every existing Weaviate collection, so read [runbook](operations/runbook.md) first. `OPENAI_API_KEY` is required; do not place secret values in docs or source.

## Boundaries and backlog

No source area is intentionally deferred. Existing production conversation files, PDFs, eval result artifacts, environment files, and other ignored paths are intentionally not inspected/documented because they may contain sensitive, generated, or excluded content. The offline pytest suite (`make test`, `tests/`) regression-tests the eval graders (`test_evaluators.py`), the orchestrator's decomposition cap + synthesis branch (`test_orchestrator_synthesis.py`), and the scheduler's done-but-unprocessed task invariant (`test_scheduler_fast_tasks.py`); the eval harnesses remain the primary validation path for end-to-end runtime behavior. Design rationale and experiment history live in `docs/journey.json` (`make journey` rebuilds `docs/journey.html`).
