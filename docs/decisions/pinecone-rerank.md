# Decision: Pinecone hybrid index and/or a reranking stage vs the current Weaviate hybrid retrieval

- **Verdict: REJECT both.** Keep Weaviate hybrid search (`limit=4`, `alpha=0.65`, relative-score fusion) as the retrieval stage, with no reranker.
  Both arms stay in the repo as opt-in knobs (`HC_RAG_RETRIEVER=pinecone`, `HC_RAG_RERANKER=pinecone`; defaults `weaviate` / `none`).
- Date: 2026-08-20 · Branch: `autoresearch/pinecone-rerank` (base `61da91e`) · Predecessor: `docs/decisions/pageindex-vs-weaviate.md` (PageIndex, REJECT).
- Evaluator: `evals/pageindex_gate.py` (two-stage, thresholds frozen before any result). Reports: `evals/results/pinecone-rerank.md` (stage 1),
  `evals/results/pinecone-rerank-stage2.md` (stage 2), `evals/results/pinecone-dense-diag.md` (labelled diagnostic).
- Wall-clock ≈ 2 h 40 min (of which ~80 min was an aborted concurrency-1 stage-2 attempt) · API spend ≈ $10 (two 172-query judged runs ≈ $6.5, stage-1 routing/rerank calls, Pinecone free tier).

## What was compared (retrieval stage only; safety gate, decomposition, generation, validation, citations identical)
| arm | retrieval |
|---|---|
| `weaviate` (reference) | hybrid top-4 per collection, text-embedding-3-small, α 0.65, relative-score fusion |
| `pinecone` | Pinecone serverless (dotproduct, 1536-d), namespace per collection; dense = same text-embedding-3-small; sparse = `pinecone-sparse-english-v0`; convex hybrid α 0.65; top-4 |
| `weaviate+rerank` | Weaviate hybrid top-12 → Pinecone Inference `bge-reranker-v2-m3` → top-4 |
| `pinecone+rerank` | Pinecone hybrid top-12 → same reranker → top-4 |
Top-k into generation is 4 per collection on every arm (no context-size confound).

## Stage 1 — retrieval-only gate (71 eligible golden questions, page_recall via `evals.evaluators.retrieval_page_hit`, 0 errors on all arms)
| arm | page_recall | Δ vs ref | core / holdout | chunk_recall | retrieval latency | gate |
|---|---|---|---|---|---|---|
| `weaviate` (ref) | 0.648 | — | 0.666 / 0.629 | 0.587 | 2.32 s | |
| `pinecone` | 0.463 | −0.185 | 0.450 / 0.476 | 0.417 | 2.30 s | REJECT |
| `weaviate+rerank` | **0.698** | **+0.050** | 0.736 / 0.660 | 0.610 | 2.26 s | pass → selected |
| `pinecone+rerank` | 0.504 | −0.144 | 0.530 / 0.476 | 0.451 | 2.92 s | REJECT |

Diagnostic (labelled, not used for selection): Pinecone **dense-only** (α = 1.0) scored 0.607 vs a 0.664 reference (Δ −0.057; core 0.695 vs 0.675, holdout
0.517 vs 0.652). So ≈0.13 of the hybrid arm's loss is the convex combination of raw dot-product and raw sparse scores (Weaviate normalises each ranking
before fusing); the remainder is a dense-only index trailing a lexical+dense hybrid on holdout. Pinecone is rejected **as configured**; see "What would change the verdict".

## Stage 2 — paired judged eval, `weaviate` vs `weaviate+rerank` (core+holdout, 2 repetitions, n = 172 per arm, concurrency 8 on both arms, judges gpt-5.6-sol, 0 errors)
| gate | `weaviate` (ref) | `weaviate+rerank` | threshold | |
|---|---|---|---|---|
| correctness | 0.850 | **0.799** (Δ −0.051) | ≥ +0.030 | fail |
| groundedness | 0.925 | 0.922 | ≥ ref | fail (flat) |
| holdout correctness | 0.828 | **0.737** (Δ −0.091) | ≥ ref | fail |
| cost / query | $0.0188 | $0.0194 (+3.4%) | ≤ 1.25× | pass |
| p50 latency | 17.6 s | 18.9 s (+7.5%) | ≤ 1.25× | pass |

Supporting metrics (ref → rerank): page_recall 0.680 → 0.690 (the stage-1 +0.050 shrinks to noise once decomposition / gap-fill unions are in play);
chunk_recall 0.657 → 0.639; must_mention_recall 0.603 → 0.568; hallucinated (both-answered) 0.44 → 0.50; core correctness 0.870 → 0.855.
By category (correctness): factual_single 0.888 → 0.834 (n=56) · ambiguous_followup 0.934 → 0.815 (14) · adversarial_hallucination 0.859 → 0.800 (16) ·
factual_multi 0.836 → 0.818 (18) · cross_drug 0.742 → 0.805 (14, the one gain). Paired per question (mean over reps, |Δ| > 0.1): **4 wins · 11 losses · 44 ties** (n = 59).
Mechanism: the reranker returns chunks from the right *pages* but swaps in less complete chunks (chunk_recall and must_mention down), so answers lose
required facts; retrieval scores are not exposed to any prompt, so score scale is not the cause. Verified the reranker ran: candidate contexts carry
bge relevance scores (top ≈ 0.23, mean 0.40) rather than fusion scores (mean 0.70).

## Why REJECT rather than INCONCLUSIVE
Quality gates fail in the same direction on both splits with a paired n of 172, and the loss (−0.051 overall, −0.091 holdout) is outside the ±0.07 judge
noise measured at n≈44 (`docs/baseline-report.md`) once averaged over 2 repetitions. Cost and latency were fine; quality was not. Per the mission, quality
failures are not tuned away after the fact.

## Confounds and caveats (recorded; none reverse the verdict)
- **Pinecone fusion configuration** (above) is the author's choice, not Pinecone's; a normalised fusion or Pinecone's own hybrid recipe was not tested.
- Pinecone embeds `contextualized` only; Weaviate's text2vec-openai embeds class name + all properties. Dense vectors are close but not identical.
- **Reference drift**: the same Weaviate reference scored 0.681 / 0.648 / 0.664 page_recall across three stage-1 runs today (routing-LLM nondeterminism, ≈ ±0.02).
  The design is paired within a run, so this does not bias deltas, but stage-1 deltas below ≈0.03 are noise.
- **Concurrency 8** was used for stage 2 at the user's request (after an aborted concurrency-1 attempt). Both arms ran at the same level; the relative latency gate
  holds, but absolute p50s are not comparable with the single-threaded baseline report (which is also on a different code base).
- LangSmith was over its monthly trace quota: all numbers come from `run_baseline`'s local rows (authoritative); no experiment URLs or per-stage cost breakdown.
- 15 of 86 golden questions are excluded from stage 1 (8 without expected pages, 7 multi-turn); all 86 run in stage 2. Multi-turn evals were not run.
- `bge-reranker-v2-m3` was the only reranker tested, at candidates=12 → top-4, reranking the `contextualized` text.

## What would change the verdict
1. **Pinecone**: re-run with min-max-normalised (or RRF) fusion, or Pinecone's integrated hybrid, and clear the stage-1 gate — then the frozen stage 2 decides.
   `HC_RAG_PINECONE_ALPHA` is the only knob today; the fusion formula lives in `healthcare_rag/processors/pinecone_retrieval.py`.
2. **Reranker**: a reranker that scores completeness as well as relevance (or reranking over larger candidate pools with top-k > 4 — which then reopens the
   context-size confound), or a stronger model (cohere-rerank-3.5), evaluated the same way. Given stage 1 showed only +0.05 page_recall headroom, gains are capped.
3. Evidence that retrieval — not generation/validation — is the binding constraint: today correctness is 0.85–0.90 with page_recall ≈ 0.68–0.86 depending on
   pipeline stage; the binding errors in the per-question losses were answer completeness, not missing pages.

## Follow-ups
- Keep both arms and the multi-arm gate (`evals/pageindex_gate.py --arm-b <retriever>[+rerank]`); they are inert with default knobs.
- Delete the Pinecone index `healthcare-rag` if no further runs are planned (free tier, but it is a live copy of the corpus); rotate `PINECONE_API_KEY` (it transited chat).
- Cheapest remaining retrieval lever is still tuning the existing hybrid (`alpha`, `limit`) through the same gate.
