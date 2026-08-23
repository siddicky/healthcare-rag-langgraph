<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# docs/experiments

## Purpose
Raw evidence trail behind the retrieval decisions in `../decisions/` and the
write-up in `../retrieval-experiments.md`. Copied verbatim from the (git-excluded)
`.omo/autoresearch/<mission>/` working directories used to run each experiment, so
the full evidence travels with the repo even though the live autoresearch scratch
space doesn't.

## Key Files
| File | Description |
|------|-------------|
| `README.md` | Explains the two subdirectories' shared shape and points to the evaluator (`evals/pageindex_gate.py`) and the `run_baseline` reports under `evals/results/` (`pinecone-rerank*`, `pi-gate-*`, `pageindex-vs-weaviate*`, `pinecone-dense-diag*`) that each mission consumed |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `pageindex-vs-weaviate/` | Evidence for the PageIndex-vs-Weaviate mission: `deep-interview-spec.md` (requirements + frozen thresholds, written **before** any result), `mission.md`, `evaluator.json`, `decision-log.md` (timestamped iteration log), `evaluations/` (per-iteration evaluator JSON) |
| `pinecone-rerank/` | Same shape for the Pinecone-hybrid/reranker mission; `evaluations/iteration-0002.json` is the 4-arm stage-1 sweep, `iteration-0003-diag-pinecone-dense.json` a labelled diagnostic, `iteration-0003.json` the paired stage-2 run |

## For AI Agents

### Working In This Directory
- This directory is a **copy** of already-completed mission working directories — treat it as historical record, not a place to run new experiments. A new experiment's live working directory is `.omo/autoresearch/<mission>/` (git-excluded); only copy the finished artifacts here once a decision record in `../decisions/` is written.
- The `deep-interview-spec.md` files record thresholds that were frozen **before** any result was seen — never edit a spec file after the fact to match what actually happened; that would falsify the evidence trail the decision record relies on.
- If you start a new retrieval/routing experiment, follow this same shape (`mission.md`, `deep-interview-spec.md` written first, `decision-log.md` as you go, `evaluator.json` + `evaluations/` per iteration) so it can be copied here the same way once concluded.

### Testing Requirements
None — this is static evidence, not code. Reproducibility is checked by re-running
the named evaluator command in `../decisions/*.md` or `evals/README.md`, not by a
test in this tree.

### Common Patterns
- Every mission directory is self-contained: a spec, a running log, an evaluator config, and per-iteration evaluation JSON — don't add narrative-only files without a corresponding `evaluations/*.json` backing them.

## Dependencies

### Internal
- `evals/pageindex_gate.py` (the evaluator every `evaluator.json`/`evaluations/*.json` here corresponds to)
- `evals/results/` (the `run_baseline` reports each mission's stage-2 iteration consumed)
- `../decisions/pageindex-vs-weaviate.md`, `../decisions/pinecone-rerank.md` (the conclusions drawn from this evidence)

### External
- None

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
