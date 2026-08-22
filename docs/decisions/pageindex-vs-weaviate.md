# Decision: PageIndex tree-search vs Weaviate hybrid search as the retrieval stage

- **Verdict: REJECT** — keep Weaviate hybrid search as the retrieval stage. PageIndex stays in the repo only as an opt-in A/B arm (`HC_RAG_RETRIEVER=pageindex`, default `weaviate`).
- Date: 2026-08-20 · Branch: `autoresearch/pageindex-vs-weaviate` · Base: `6bfb4ca`
- Stage reached: **1 of 2** (retrieval-only gate). Stage 2 (paired full eval with judges) was not run, by design.
- Spec: `.omc/specs/deep-interview-pageindex-vs-weaviate.md` · Evaluator: `evals/pageindex_gate.py` (`evals/results/pageindex-vs-weaviate.{md,json}`)
- Mission wall-clock ≈ 28 min of a 4 h ceiling · LLM spend ≈ $0.30 (tree build ≈ $0.08 at PageIndex's stated $0.001/page; stage 1 ≈ 71 routing + ~80 selector calls; two 3-question smokes).

## What was compared
Retrieval stage only. Same graph, same safety gate, decomposition, generation, validation and citations; the only difference is the per-collection search
callable (`Resources.hybrid_search` seam). The PageIndex arm: trees built with PageIndex local mode (flash) from `docs/lipitor.pdf` (48 p) and
`docs/metformin.pdf` (31 p); at query time one structured LLM call (`gpt-5.6-luna`, same model as the other stages) picks ≤ 4 tree nodes from the outline;
their page ranges are mapped back onto the **same contextualised chunks** (`data/chunks_*.json`) by `page_numbers`, capped at 8 chunks. Weaviate arm:
unchanged (`limit=4`, `alpha=0.65`, relative-score fusion on `contextualized`).

## Gate table (stage 1, 71 of 86 golden questions: 8 have no expected pages, 7 are multi-turn; 0 errors on either arm)
| metric | Weaviate (A) | PageIndex (B) | Δ (B−A) | gate |
|---|---|---|---|---|
| page_recall, all | **0.681** | 0.609 | −0.071 | B ≥ A → **fail** |
| page_recall, core (n=36) | 0.685 | 0.637 | −0.048 | |
| page_recall, holdout (n=35) | 0.676 | 0.581 | −0.095 | |
| chunk_recall, all | 0.618 | 0.601 | −0.017 | |
| retrieval latency / question | 1.66 s | 3.10 s | +1.44 s | |
| chunks returned / question | 4.4 | 8.2 | +3.8 | |
| paired outcome (page_recall) | — | 12 wins · 21 losses · 38 ties | | |

By category (mean page_recall A → B): factual_single 0.738 → 0.708 (n=28) · factual_multi 0.539 → 0.504 (9) · cross_drug 0.695 → 0.707 (7, the only
category PageIndex edges) · adversarial_hallucination 0.792 → 0.625 (8) · pii_or_phi 0.722 → 0.556 (6) · unsafe_personal_advice 0.559 → 0.432 (13).
Worst PageIndex misses were total (1.0 → 0.0 on `ho-lip-003`, `ho-lip-004`, `ho-unsafe-003`): the selector picked sections that do not contain the
expected pages, and because retrieval is the only stage that differs, nothing downstream can recover a page that was never retrieved.

Stage-2 thresholds that were frozen but never exercised: Δcorrectness ≥ +0.03, groundedness ≥ A, holdout correctness ≥ A, cost ≤ 1.25×A, p50 ≤ 1.25×A.

## Why REJECT rather than INCONCLUSIVE
The stage-1 gate is deterministic (no LLM judge), paired on identical questions, and the loss is consistent across both splits and five of six
categories, at roughly 2× the retrieval latency and 2× the context size. The deep-interview spec (AC-8/AC-15) defined a stage-1 loss as REJECT
precisely so that judge budget is not spent on an arm that retrieves worse pages. Tuning past a stage-1 loss was ruled out in the mission to avoid
fitting the selector to the golden set.

## Known confounds and caveats (recorded, none reverse the verdict)
- **Small corpus.** 79 pages total; PageIndex's published wins are on 1,000-page filings. Tree search has little to gain when 4 hybrid-ranked chunks already
  cover most answers — this was the contrarian hypothesis going in, and stage 1 is consistent with it.
- **Selector is ours, not PageIndex's.** PageIndex local mode exposes no LLM retrieval primitive (only `submit_document`/`get_tree`/`get_ocr`); its
  "reasoning retrieval" lives in its chat agent. Our one-call selector over titles + summaries is the simplest faithful stand-in; a multi-step agentic
  traversal might do better, at even higher cost/latency.
- **Tree coverage gap.** Flash-mode trees leave Lipitor pp. 1–2 and Metformin p. 1 unassigned. No golden item expects those pages, so this did not
  affect the gate.
- **Page → chunk mapping.** Mapping node page ranges back to the existing chunks (rather than feeding raw page text) keeps the comparison
  apples-to-apples but means PageIndex's own passage boundaries were not tested.
- **Stage 1 ≠ full pipeline recall.** Stage 1 issues one retrieval per question (no decomposition / gap-fill unions), so both arms score below the
  full-pipeline page_recall of ≈0.86 in `docs/baseline-report.md`; the comparison is paired, so this affects both arms equally.
- **Judge noise was never in play**: no LLM-judged metric was used in the verdict.
- **Multi-turn** (`make eval-multiturn`) was not evaluated (out of scope per spec).
- **LangSmith** was over its monthly trace quota during the run; stage 1 does not depend on it.

## What would change the verdict
1. A materially larger or more hierarchical corpus (dozens of monographs, or full prescribing-information PDFs with deep section trees) — re-run
   `uv run python -m evals.pageindex_gate --json` after `make index-pageindex`; the gate is frozen and reusable.
2. A selector that reads node text (not only summaries) or traverses the tree in 2–3 steps, **and** still clears the 1.25× cost/latency gates.
3. Evidence that Weaviate retrieval, not generation, is the binding constraint on correctness — today correctness is 0.90 with page_recall ≈0.86, so the
   headroom is small.

## Follow-ups
- Keep the arm and the gate; they cost nothing at runtime when `HC_RAG_RETRIEVER=weaviate`. Remove them if they rot.
- If retrieval quality is revisited, cheaper levers first: `alpha`/`limit` sweeps on the existing hybrid search, or a reranker over the top-8 — both reuse the same gate.
