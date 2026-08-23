---
type: contributor guide
title: Safe workflow for AI-assisted contributors
description: Repository rules, architectural invariants, generated-data boundaries, and evidence loops for changing the healthcare RAG and deployed coach safely.
tags: [contributing, ai-assistance, safety, workflow]
---

# Safe workflow for AI-assisted contributors

Read root `AGENTS.md` first, then the closest `AGENTS.md` in the area being changed (`healthcare_rag/`, `healthcare_rag/graph/`, `healthcare_rag/processors/`, `evals/`, `tests/`, `server/`, or `frontend/`). Source and tests override this generated wiki. The runtime RAG graph, coach graph, member perimeter, and clean-room server are distinct systems with coupled safety contracts.

## Do not casually edit

- Secrets or local environment files; keep credentials in `.env` only and do not print them.
- `data/chunks_*.json`, PageIndex trees, PDFs, evaluation reports, or lockfiles without understanding their generation and comparison impact. Corpus replacement is destructive; use [retrieval and ingestion](../retrieval/weaviate-and-ingestion.md).
- Generated OpenWiki `index.md` files. Regenerate repository documentation using `make wiki-update` after structural changes.
- Production defaults or safety-stage flags as a shortcut: `HC_RAG_SAFETY_GATE`, `HC_RAG_QUERY_RESPONSE_ARM=current`, and `HC_RAG_SAFETY_CLASSIFIER=llm` have evaluation implications. See [safety gate](../safety/gate.md) and [routing evaluations](../observability/routing-evals.md).

## Invariants to preserve

1. `safety_gate` is before retrieval/generation; deterministic checks only escalate; the scrubbed query is what reaches prompts, retrieval, and persistence.
2. Terminal medical refusals do not retrieve or generate, contain no clinical-unit number, and have no follow-ups.
3. `Command[Literal[...]]` node targets, router constants, and graph wiring must agree. Add/reset new per-turn `RAGState` fields deliberately.
4. Generation's prompt ID map and formatted documents must travel with the answer into validation; validation failure must not fail open.
5. A member client is not trusted to choose identity, assistant, native route, cron wake, state projection, or catalog facts. Server perimeter enforcement is authoritative.
6. Member data is namespaced and scrubbed; upload bytes are request-lifetime only; erasure and reminder workflows are fail-closed/retryable where their owners require it.

## Narrow verification ladder

| Change | First check | Then, if behavior changed |
|---|---|---|
| Graph/prompt/RAG processor | focused `tests/graph/` test; `make test` | `make eval-smoke`, filtered deterministic eval, then judge eval |
| Safety/privacy/refusal/history | `tests/test_safety_gate.py`, privacy/boundary graph tests | safety categories plus `make eval-multiturn` |
| Retrieval/corpus/arm | retrieval unit tests and narrow factual eval | paired retrieval gate where an adoption is proposed |
| Coach/perimeter/data lifecycle | focused `tests/agent/` module | `make eval-agent`; deployed smoke for deployed boundary changes |
| Frontend protocol/catalog | `bun --cwd frontend run test` | build/E2E as applicable |
| Server route/runtime | `make server-test` | `make parity`; container/deployed smoke where topology changes |

`make test` excludes `judge` tests by default. `make test-judges` and real evaluations call providers; never present an unrun command as evidence. See [evaluation governance](../observability/evaluation-governance.md) for comparable-result rules.

## Reliable change pattern

Make one behavior change, identify its owner and caller, add or update a focused test, run the narrowest real path, then compare equivalent evaluation reports. Preserve full failure output. For a prompt/Pydantic change, update the prompt, response model, registry/wiring, and fidelity test together. For a template change used by refusal boundaries, update the allowed-template/version rules as well.

Do not infer experimental success from an adapter, smoke fixture, prompt, or empty report. In particular, the routing lanes have no completed paired or paid measurement; keep calibration facts, dependency facts, capabilities, hypotheses, and proposals distinct.

## Real-application verification

For the base RAG: `make weaviate`, `make ingest`, and `make run` exercise the CLI; a preliminary streamed answer is **not** validated final output. For production-oriented coach work, use the offline agent harness first and only run `make deployed-smoke` with the required deployed configuration. The server remains in-memory by design, so restart behavior is a boundary rather than durable-data proof.
