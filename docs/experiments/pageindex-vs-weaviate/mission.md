# Autoresearch mission: pageindex-vs-weaviate

- Slug: `pageindex-vs-weaviate`
- Source spec: `.omc/specs/deep-interview-pageindex-vs-weaviate.md` (authoritative for constraints, non-goals, AC-1..AC-15)
- Created: 2026-08-20 from `/deep-interview --autoresearch`
- Max runtime: **4h** (hard stop). LLM spend guide: ~$15.
- Branch: `autoresearch/pageindex-vs-weaviate` off `phase-2/langgraph-port`; commit per iteration; never push.

## Mission (one sentence)
Prove or refute, with this repo's eval harness, that a PageIndex tree-search retrieval arm (retrieval stage only,
pages mapped back onto existing chunks) beats Weaviate hybrid search by ≥ +0.03 mean correctness on a paired,
2-repetition, core+holdout run while holding groundedness, holdout correctness, cost (≤1.25×) and p50 latency (≤1.25×)
— and write the go/no-go decision record either way.

## What "done" means
The evaluator (`evaluator.json`) returns `pass: true`, **or** it returns a terminal `verdict` of `REJECT` from
stage 1 / stage 2, **or** the 4h ceiling is hit. In every case `docs/decisions/pageindex-vs-weaviate.md` must exist
with the verdict (ADOPT / REJECT / INCONCLUSIVE) per AC-13..AC-15. A REJECT is a successful mission outcome, not a failure
to keep iterating on — do not tune past a stage-1 REJECT.

## Iteration plan (one change cycle per iteration)
1. **Evaluator first.** Create `evals/pageindex_gate.py` (AC-7..AC-12) with `--smoke` and `--json`; validate it
   against the Weaviate arm only (both arms = Weaviate should yield Δ≈0). After this iteration the thresholds,
   `evals/golden_dataset.json` and `evals/evaluators.py` are frozen.
2. **Index.** `make index-pageindex` → `data/pageindex_tree_{lipitor,metformin}.json` via PageIndex local mode (AC-2).
3. **Adapter.** `pageindex_search()` + `HC_RAG_RETRIEVER` knob + unit tests + `make eval-smoke` green (AC-1, AC-3..AC-6).
4. **Run the evaluator.** Stage 1 decides whether stage 2 runs. Persist the JSON.
5. If stage 2 fails **only** on cost/latency, one bounded tuning iteration on the node selector (K, prompt, model
   among those already in `services/models.py`) is allowed; then re-run. Quality failures are not tuned away.
6. **Decision record** (AC-13..AC-15) and final `evals/results/pageindex-vs-weaviate.md`.

## Hard rules
- Weaviate default path must stay byte-identical in behaviour; existing tests pass unmodified.
- Safety gate, decomposition, generation, validation untouched. Selector LLM call is counted in cost/latency.
- No PageIndex Cloud, no `chat()`, no raw-page adapter, no multi-turn eval.
- Secrets only in `.env`; go through `sampling_params()`; `uv run` for Python.
- Never compare against historical report numbers — always the paired Weaviate arm from the same run.
