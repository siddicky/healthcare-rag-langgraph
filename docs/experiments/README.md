# Experiment evidence trail

Raw artefacts behind the retrieval decisions in `docs/decisions/` and the write-up in `docs/retrieval-experiments.md`.
Copied verbatim from the (git-excluded) `.omc/autoresearch/<mission>/` working directories so the evidence travels with the repo.

| dir | contents |
|---|---|
| `pageindex-vs-weaviate/` | `deep-interview-spec.md` (requirements + frozen thresholds, written **before** any result), `mission.md`, `evaluator.json`, `decision-log.md` (timestamped iteration log), `evaluations/` (evaluator JSON per iteration) |
| `pinecone-rerank/` | same shape; `evaluations/iteration-0002.json` = 4-arm stage 1, `iteration-0003-diag-pinecone-dense.json` = labelled diagnostic, `iteration-0003.json` = paired stage 2 |

The evaluator itself is `evals/pageindex_gate.py`; the per-run `run_baseline` reports it consumed are under `evals/results/` (`pinecone-rerank*`, `pi-gate-*`, `pageindex-vs-weaviate*`, `pinecone-dense-diag*`).
