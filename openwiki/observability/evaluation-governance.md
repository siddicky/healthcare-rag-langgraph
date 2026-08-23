---
type: evaluation governance
title: Evaluation evidence and release gates
description: How versioned datasets, calibrated evaluators, provenance, report publication, parity seals, retrieval gates, and deployment smoke checks turn changes into comparable evidence.
tags: [evaluations, governance, regression, release]
---

# Evaluation evidence and release gates

Evaluation is a control system, not an aggregate score. The canonical execution workflow is [tracing and evaluations](evaluations.md); this page owns the artifacts and gates that decide whether evidence is comparable or releasable.

## Evidence inputs and mutation surface

- `evals/golden_dataset.json` is the single-turn source of truth. `evals/dataset.py` assigns stable UUIDv5 IDs for LangSmith synchronization, so an edited local row updates rather than duplicates its remote example. Rows carry expected behavior, sources, required/forbidden terms, category, split, and optional history.
- `evals/multiturn_dataset.json` is the corresponding conversation source. It is evaluated through the same graph thread semantics described in [architecture](../architecture/overview.md).
- `evals/judge_calibration.json` and `evals/calibrate.py` test evaluator behavior against labelled adversarial cases. A calibration mismatch exits nonzero; amend the calibration set when a grader misclassifies a real failure.
- Routing data has separate typed loaders and validation (`routing_dataset*.py`). Its tests freeze corpus cardinality, split/stratum coverage, duplicate checks, and holdout separation (`tests/test_routing_dataset.py`).

Changing a dataset, prompt, threshold, evaluator, model, lockfile, or experiment configuration changes the evidence surface. Do not compare runs across those changes without rescore/re-run work that makes them equivalent.

## Comparability, publication, and seals

`RoutingProvenance` records the source revision, arm environment, local/remote row populations, artifact hashes, model/version settings, repetitions, concurrency, and experiment identity. The routing gate rejects a pair when binding differs; this prevents attributing a delta to an arm when inputs or settings also changed. The dedicated [routing evaluation record](routing-evals.md) defines its lane-specific binding invariant.

Routing report publication is transactional: `routing_report_io.py` writes JSON and Markdown as a set, restores the prior set after a write/interruption failure, and leaves recoverable backups if restoration itself fails. `routing_gate_publish.py` validates arm bindings, unique experiment identity, output-directory consistency, and calibration-row exclusion before publishing. Focused evidence: `tests/test_routing_report_integrity.py`, `tests/test_routing_report_batch.py`, and routing publication fixtures.

Seals and parity catch non-comparable regression claims:

- `evals/seal_clean.py` treats modified/untracked source inputs as unclean except for narrowly allowlisted generated artifacts; `tests/test_eval_seal.py` covers hash, row-population, and cleanliness failures.
- `evals/agent_parity.py` compares sealed baselines with bounded recall/judge tolerances and safety non-regression constraints. Missing, duplicate, malformed, or non-finite values fail; multi-turn checks also reject increased safety drift/boundary violations. Negative drills are in `tests/test_parity_gate.py`.

## Adoption gates

The retrieval gate (`evals/pageindex_gate.py`) is a two-stage decision, documented with arm mechanics in [retrieval arms](../retrieval/arms-and-reranking.md). Stage 1 compares eligible items' page recall and sends only the winner to stage 2. Stage 2 runs paired core/holdout real-pipeline measurements. Adoption requires a correctness gain of at least 0.03, no groundedness or holdout-correctness regression, and cost and p50 latency each no more than 1.25× reference. Quality failure is `REJECT`; cost/latency-only failure is `INCONCLUSIVE`; missing evidence cannot adopt. `tests/test_pageindex_gate.py` pins thresholds, selection, population accounting, environments, reports, and exit codes.

Routing gates similarly require valid calibration and binding before a staged decision. Their present outcomes are intentionally limited to the evidence stated in [routing evaluations](routing-evals.md), not inferred from gate code.

## Acceptance layers

| Layer | What it proves | Typical command |
|---|---|---|
| Offline contract | Parsing, routing, evaluator, seal, and gate logic against fakes | `make test` |
| Real pipeline | Current graph with Weaviate/OpenAI and configured judges | `make eval PREFIX=name`; `make eval-multiturn PREFIX=name` |
| Paired adoption | Equal-config reference/candidate decision | `uv run python -m evals.pageindex_gate --json` |
| Deployment acceptance | Deployed environment, perimeter, and production integration | `make deployed-smoke` after deployment |

A deploy is tag-triggered and uses an immutable image; workflow checks, readiness, and smoke failure behavior are covered by `tests/test_deploy_workflow.py`. Smoke failure is not an automatic rollback. A passing offline suite, a favorable historical report, or a smoke fixture alone is not a substitute for the appropriate layer.

## Change checklist

1. Add a regression as a dataset row/conversation and calibrate a new grader where needed.
2. Keep the dataset, evaluator, prompt/model, thresholds, repetitions, concurrency, and arm manifests equal for a comparison.
3. Run the narrow deterministic check first; use judge and multi-turn runs for behavior changes.
4. Publish/read both report artifacts under `evals/results/`; do not manually edit generated reports.
5. For an adoption/release claim, retain gate/seal/provenance evidence and state what was actually executed.
