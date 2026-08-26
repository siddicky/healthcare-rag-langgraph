---
type: engineering decision record
title: Evidence-backed scope and remaining boundaries
description: Completed production-readiness, regression-protection, AI-contributor, and safety directions as shown by source, tests, decisions, and recorded evidence, separated from future work.
tags: [decisions, scope, safety, production]
---

# Evidence-backed scope and remaining boundaries

This page distinguishes implemented work from rationale and next steps. It does not infer intent from the assignment brief.

## Completed directions

| Direction | Repository evidence | What it establishes |
|---|---|---|
| Healthcare safety | `processors/safety.py`, privacy sanitizer, refusal boundaries, graph gate, and safety/boundary/privacy tests | Runtime gate, scrubbed processing, templated terminal behavior, and measured regression categories; not clinical correctness certification. |
| Regression protection | `evals/`, golden/multiturn datasets, calibration, seals/parity, retrieval/routing gates, and tests | Repeatable evaluation machinery with explicit comparability constraints. See [evaluation governance](../observability/evaluation-governance.md). |
| Production readiness | Compose/Fly configuration, deploy workflow, server readiness, perimeter, smoke tests | Operational topology and acceptance checks; production storage moved from in-memory to Postgres-durable (`SERVER_STORAGE=postgres`, v1.0.7) and the member stream perimeter moved from v1 to v2 (2026-08-25), both human-gated flips recorded in `docs/deploy.md`. See [deployment](../operations/deploy.md) and the [clean-room agent server](../server/agent-server.md). |
| AI-contributor readiness | root/nested `AGENTS.md`, Make targets, fakes, fixtures, parity/eval loops | Source-grounded change instructions and narrow feedback loops. See [AI-assisted workflow](../contributing/ai-assisted.md). |

## Trade-off and deliberate limits

The strongest implementation trade-off is safety/conservatism versus answer coverage: gate short circuits and validation can refuse/suppress output rather than generate unsupported information. Recorded safety comparisons in `docs/baseline-report.md` are particular experiment observations, not general product-quality claims. The system deliberately retains a small monograph scope and Weaviate default retrieval rather than claiming full clinical or multi-tenant service readiness; server durability itself has since moved past the pure in-memory posture (production runs `SERVER_STORAGE=postgres` for threads/store/crons, though in-flight runs, the queue, and open streams remain process-local — see [clean-room agent server](../server/agent-server.md)).

The removal of the speculative orchestrator is documented by the current `StateGraph` composition and commit `3435caf`; it is not part of the active architecture.

## Evidence limits and another-week work

Second-pass candidates are inference unless a decision record says otherwise: strengthen classifier coverage across dialects/third-person emergencies, enforce citation coverage for every statement, add secure retention/access/deletion controls for durable checkpoints, and resolve/evaluate routing candidates only through their gates. The routing lanes are neither adoption/rejection evidence nor completed comparisons: query evidence stops at failed authored-judge calibration; semantic evidence stops at dependency preflight. See [routing evaluations](../observability/routing-evals.md).

For any proposed next step, first add a focused test/eval fixture, retain configuration/provenance, and state whether execution occurred.
