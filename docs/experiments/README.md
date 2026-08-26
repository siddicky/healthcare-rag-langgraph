# Retrieval experiment archive

This directory preserves the evidence behind the retrieval decisions in
`docs/decisions/` and the summary in `docs/retrieval-experiments.md`. The files
are copies from git-excluded `.omo/autoresearch/<mission>/` working directories.
Treat them as historical records, not current configuration or a place to run
new experiments.

| Directory | Archived evidence |
|---|---|
| `pageindex-vs-weaviate/` | The pre-result `deep-interview-spec.md`, mission, evaluator contract, timestamped decision log, and evaluator JSON for each recorded iteration |
| `pinecone-rerank/` | The mission, inherited gate contract, timestamped decision log, four-arm stage 1 (`iteration-0002.json`), labelled dense-only diagnostic (`iteration-0003-diag-pinecone-dense.json`), and paired stage 2 (`iteration-0003.json`) |

`evals/pageindex_gate.py` is the reusable evaluator. Its durable reports are in
`evals/results/`, chiefly `pageindex-vs-weaviate*`, `pinecone-rerank*`,
`pinecone-dense-diag*`, and the `pi-gate-*` paired runs. Read those reports and
the matching decision record before proposing another retrieval change.
