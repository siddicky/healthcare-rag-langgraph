<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# docs/decisions

## Purpose
One short, dated record per architectural decision, each ending in an explicit
verdict (`ADOPT` / `REJECT` / `INCONCLUSIVE`) backed by a named evaluator and
report. These are the durable "we tried X, here's why we did or didn't keep it"
artifacts referenced from the root `AGENTS.md`'s "Before proposing a retrieval
change" gotcha and from `evals/README.md`.

## Key Files
| File | Description |
|------|-------------|
| `pageindex-vs-weaviate.md` | **REJECT.** PageIndex tree-search retrieval vs. Weaviate hybrid search. Stage 1 only (retrieval-only gate) — stage 2 never ran because stage 1 already rejected. PageIndex stays as an opt-in arm (`HC_RAG_RETRIEVER=pageindex`) |
| `pinecone-rerank.md` | **REJECT both.** Pinecone hybrid index and a bge reranking stage, each vs. current Weaviate hybrid (`limit=4`, `alpha=0.65`, relative-score fusion). Both stages ran; both arms stay as opt-in knobs (`HC_RAG_RETRIEVER=pinecone`, `HC_RAG_RERANKER=pinecone`) |
| `query-or-respond-vs-current.md` | **INCONCLUSIVE.** Query-or-respond routing arm vs. current — the authored query-judge calibration didn't clear its own threshold (22/24 fixtures, two acceptable greeting cases scored just under 0.80), so the paired arm comparison never ran. Production stays `HC_RAG_QUERY_RESPONSE_ARM=current` |
| `semantic-router-vs-llm-safety.md` | **INCONCLUSIVE.** Semantic Router safety classifier vs. the current LLM classifier — blocked entirely by an unsatisfiable dependency (`semantic-router==0.1.16` needs a `litellm` version incompatible with the project's unchanged `openai<2` bound); no adapter was ever built or run |
| `routing-experiment-summary.md` | Consolidates both routing decisions above into one summary of applicability — makes explicit that "attempted but blocked" is not a measurement and must not be reported as one |

## For AI Agents

### Working In This Directory
- Every file here must open with the same shape: a bolded **Verdict** line, then date/branch/base commit, then the evaluator and report paths that back the verdict, then (where applicable) rough wall-clock/spend. Match that shape exactly for a new decision — these files get read in isolation, out of context, so the header must be self-sufficient.
- Never write `ADOPT`/`REJECT` for a stage that didn't actually run — `query-or-respond-vs-current.md` and `semantic-router-vs-llm-safety.md` are the models for how to honestly record "blocked before measurement" as `INCONCLUSIVE`, not as a soft reject.
- A new decision record must link to a real evaluator run's output under `evals/results/` — don't hand-write numbers into a decision doc; the evaluator (`evals/pageindex_gate.py` or `evals/routing_gate.py`) is the only source of truth for the numbers.
- If you reject a decision record's premise (propose retrying a rejected retrieval arm, say), read the linked `evals/results/*.json`/`.md` first — the numbers there, not the prose summary, are what would need to change.

### Testing Requirements
No tests run against this directory. The correctness guarantee comes from the
evaluator that produced each report, not from a test on the Markdown — verify a
claim by re-running the cited command in `evals/README.md`, e.g.:
```
uv run python -m evals.pageindex_gate --json --smoke --stage 1 --arm-b weaviate   # self-check, Δ must be 0
```

### Common Patterns
- Decision records are one-directional pointers into `evals/results/` and `docs/experiments/` — they never duplicate a metrics table, only cite where it lives.

## Dependencies

### Internal
- `evals/results/` (the reports each record cites)
- `docs/experiments/` (the raw mission/iteration evidence behind the retrieval decisions)
- `evals/pageindex_gate.py`, `evals/routing_gate.py` (the evaluators that produced the verdicts)

### External
- None

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
