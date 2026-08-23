# Files

- [openwiki/observability/](AGENTS.md)
- [Evaluation evidence and release gates](evaluation-governance.md) - How versioned datasets, calibrated evaluators, provenance, report publication, parity seals, retrieval gates, and deployment smoke checks turn changes into comparable evidence.
- [LangSmith tracing and regression evaluations](evaluations.md) - Opt-in observability plus single-turn and multi-turn real-pipeline evaluation, metrics, reports, and comparison workflow.
- [Routing gates — query and safety lanes](routing-evals.md) - Paired go/no-go gates in evals/routing_gate.py comparing routing arms (current+llm, deterministic+llm, tool+llm, current+semantic_router) across two stages; datasets, judges, provenance invariants, and the measured/dependency reasons both lanes are currently INCONCLUSIVE.
