# Decision log — pageindex-vs-weaviate / run-20260820-01

- Mission: `.omc/autoresearch/pageindex-vs-weaviate/mission.md` · Evaluator: `evaluator.json`
- Branch: `autoresearch/pageindex-vs-weaviate` (off `phase-2/langgraph-port`, no push)
- Started: 2026-08-20T12:06:56Z · Max runtime: 4h → hard stop 16:06:56Z

## Iteration 0 — setup (12:07Z)
- Spec crystallised at 10.0% ambiguity (7 rounds). Three components: adapter, two-stage evaluator, decision record.
- Pre-flight facts: Weaviate READY on 127.0.0.1:8080; OPENAI/LANGSMITH keys in `.env`; source PDFs `docs/lipitor.pdf` (48 p) / `docs/metformin.pdf` (31 p);
  venv pins `openai<2` → PageIndex tree build will run in an isolated `uv run --no-project --with pageindex` env, runtime only consumes cached tree JSON.
- PageIndex local mode has no LLM retrieval primitive (only `submit_document/get_tree/get_ocr`); the one-call node selector is ours and is routed through the
  existing LangChain gateway so its tokens count in `est_cost_usd` / `llm_calls`.
- Plan: iter 1 = evaluator (`evals/pageindex_gate.py`) + self-check (arm-b = weaviate, Δ≈0); iter 2 = trees + adapter + knob + tests + smoke;
  iter 3 = stage 1 gate; iter 4 = stage 2 paired run (only if stage 1 passes); iter 5 = decision record. Evaluator thresholds freeze after iter 1.

## Iteration 1 — evaluator built + self-checked (12:18Z)
- `evals/pageindex_gate.py` + `tests/test_pageindex_gate.py` (30 tests pass; `uvx ruff check` clean). CLI per evaluator.json; `THRESHOLDS` single dict.
- Self-check `--smoke --arm-b weaviate --stage 1`: identical arms → Δpage_recall = 0.000 (0.5 vs 0.5, n=3). ✅ plumbing is unbiased.
- First real-arm smoke `--smoke --stage 1`: weaviate 0.500 vs pageindex 0.667 page_recall (n=3, plumbing evidence only), 0 errors.
  Per-question retrieval latency ≈1.4 s (weaviate) vs ≈2.5 s (pageindex, +1 selector call). 71/86 golden questions eligible (8 no expected pages, 7 multi-turn).
- Deviation accepted: a non-smoke stage-1 PASS returns pass=false / INCONCLUSIVE / exit 3 (stage 1 alone cannot authorise ADOPT). Smoke pass → exit 0.
- FLAGS: (a) LangSmith monthly trace quota exceeded → 429 on ingest; stage 2 must be confirmed to complete before spending budget.
  (b) PageIndex arm returns ≤8 chunks vs Weaviate's 4/collection (spec AC-3 assumption) — cost/latency confound; tune to 4 only if stage 2 fails on cost/latency alone.
- **Thresholds are now frozen** (evaluator.json, `THRESHOLDS` in pageindex_gate.py, golden dataset, evaluators.py).

## Iteration 2 — PageIndex arm built (12:24Z)
- New: `healthcare_rag/processors/pageindex_retrieval.py` (`pageindex_search`, same signature as `hybrid_search`), `healthcare_rag/prompts/pageindex_select.yaml.j2`,
  `healthcare_rag/storage/pageindex_index.py` + `make index-pageindex` (isolated `uv run --no-project --with pageindex`), trees cached at
  `data/pageindex_tree_{lipitor,metformin}.json` (48 p / 31 p), knobs `HC_RAG_RETRIEVER` (default weaviate), `HC_RAG_PAGEINDEX_MAX_NODES`=4, `HC_RAG_PAGEINDEX_MAX_CHUNKS`=8;
  `retrieve.py` picks the callable by backend and only connects to Weaviate on the weaviate arm; `engine.describe()` records `retriever`. `.pageindex/` gitignored.
- Verified by lead: `python -m pytest -q` → 312 passed. Smokes (n=3, plumbing only): weaviate corr 0.93 / page_recall 0.72 / $0.0154 / p50 17.4 s / 9 LLM calls / 9.1k prompt tok;
  pageindex corr 0.90 / page_recall 0.61 / $0.0160 / p50 20.4 s / 12 LLM calls / 17.2k prompt tok. Context ≈2× on the pageindex arm (8-chunk cap) — expected confound.
- LangSmith quota: run_baseline completes with 0 ingested runs; local rows authoritative → stage 2 viable without per-stage breakdown.

## Iteration 3 — stage-1 retrieval gate, full (started 12:25Z)
- `uv run python -m evals.pageindex_gate --json --stage 1 --run-dir runs/run-20260820-01` — 71 eligible questions × 2 arms. Result below when complete.
- **Result (12:30Z): REJECT, exit 2.** page_recall weaviate 0.681 vs pageindex 0.609 (Δ −0.071; core −0.048, holdout −0.095); chunk_recall 0.618 vs 0.601;
  12 wins / 21 losses / 38 ties paired; retrieval latency 1.66 s vs 3.10 s; 4.4 vs 8.2 chunks/question; 0 errors. Evaluation: `evaluations/iteration-0003.json`.
- Decision: terminal per mission rule ("do not tune past a stage-1 REJECT"). Stage 2 not run; no judge budget spent.

## Iteration 4 — decision record (12:36Z)
- Wrote `docs/decisions/pageindex-vs-weaviate.md` (verdict REJECT, gate table, per-category breakdown, confounds, what would change the verdict).
- Gate report: `evals/results/pageindex-vs-weaviate.md` / `.json` / `-stage1-items.json`.
- Mission closed: ≈28 min wall-clock of 4 h; ≈$0.30 LLM spend of ~$15. Working tree left uncommitted for review (nothing pushed).
