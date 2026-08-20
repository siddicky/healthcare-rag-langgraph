# Retrieval experiments — what was tested, what was kept, what changed (evidence report)

**Direction 5 of the exercise ("question the approach itself — none of it is sacred") applied to the retrieval layer, using the direction-3 safety net
(the eval harness) as the judge.** Three alternatives to the inherited Weaviate hybrid search were built behind opt-in knobs and measured with a frozen,
two-stage, paired gate. **All three lost. Weaviate hybrid search stays, unchanged.** The durable outputs are the gate (any future retrieval idea can be
judged the same way in ~15 min), three decision records with the numbers, and the evidence trail.

Related: `docs/decisions/pageindex-vs-weaviate.md` · `docs/decisions/pinecone-rerank.md` · `docs/experiments/` (raw trail) · `evals/README.md` ("Retriever A/B gate").

## 1. Why retrieval, and why this way
- After the safety gate, model migration and synthesis work, correctness sat at 0.85–0.90 with chunk_recall ≈ 0.83–0.87 (`docs/baseline-report.md`).
  Retrieval was the obvious remaining suspect, and three well-known alternatives were on the table: PageIndex (vectorless "reasoning retrieval"), a managed
  vector DB (Pinecone), and a reranker.
- Rule adopted before any result existed: **retrieval stage only.** Safety gate, decomposition, generation, validation and citations are byte-identical on
  every arm; the only thing that differs is the per-collection search callable injected at `Resources.hybrid_search`. Any delta is attributable to the retriever.
- Rule two: **measure before believing.** A two-stage gate, thresholds frozen before the first run (recorded in a deep-interview spec), paired against a
  *fresh* Weaviate run in the same session, never against historical report numbers.

## 2. The gate (`evals/pageindex_gate.py`)
| stage | what | cost | pass rule |
|---|---|---|---|
| 1 | retrieval-only: route + search each eligible golden question (71 of 86; 8 have no expected pages, 7 are multi-turn) on every arm; `page_recall` with the harness's own definition | ~3 min / arm, cents | candidate page_recall ≥ reference; best passer (tie → lower latency) goes to stage 2 |
| 2 | paired `evals.run_baseline`, core + holdout, 2 repetitions, LLM judges, reference and candidate under identical flags | ~15 min / arm at concurrency 8, ≈ $3 / arm | Δcorrectness ≥ +0.03 ∧ groundedness ≥ ref ∧ holdout correctness ≥ ref ∧ cost ≤ 1.25× ∧ p50 ≤ 1.25× → ADOPT; quality fail → REJECT; cost/latency-only fail → INCONCLUSIVE |

Design choices worth knowing: stage 1 is deterministic (no judge), so a stage-1 loss is terminal and no judge budget is spent on an arm that retrieves worse
pages; stage 2 exists because retrieval recall is not the same as answer quality (the reranker proved exactly that); a stage-1-only pass is reported as
INCONCLUSIVE, never ADOPT. Thresholds, the golden set and metric definitions were frozen after the evaluator's own self-check (identical arms ⇒ Δ = 0.000).

## 3. Results

### 3.1 PageIndex (VectifyAI) — tree-search retrieval · **REJECT at stage 1**
Arm: trees built with PageIndex local mode from `docs/{lipitor,metformin}.pdf` (48 + 31 pages); one structured LLM call picks ≤ 4 tree nodes; their page
ranges map back onto the *same* contextualised chunks. Finding along the way: PageIndex local mode exposes **no LLM retrieval primitive** (`submit_document`,
`get_tree`, `get_ocr` only) — its "reasoning retrieval" lives inside its chat agent — so the selector is ours.

| metric (71 q, 0 errors) | Weaviate hybrid | PageIndex |
|---|---|---|
| page_recall all / core / holdout | **0.681** / 0.685 / 0.676 | 0.609 / 0.637 / 0.581 |
| chunk_recall | 0.618 | 0.601 |
| paired page_recall | — | 12 wins · 21 losses · 38 ties |
| retrieval latency / question | 1.66 s | 3.10 s |
| chunks per question | 4.4 | 8.2 |

Category view: PageIndex trails in 5 of 6 categories (only cross_drug is flat). Mission cost ≈ 28 min, ≈ $0.30.

### 3.2 Pinecone hybrid + reranker · **REJECT at stage 1 (Pinecone) and stage 2 (reranker)**
Arms: `pinecone` = serverless index, the *same* `text-embedding-3-small` dense vectors + hosted sparse (`pinecone-sparse-english-v0`), convex hybrid α 0.65,
top-4; `weaviate+rerank` = Weaviate top-12 → `bge-reranker-v2-m3` → top-4; `pinecone+rerank` = same over Pinecone. Top-k into generation is 4 everywhere.

Stage 1 (71 q, 0 errors on all arms):
| arm | page_recall | Δ | core / holdout | chunk_recall | latency | gate |
|---|---|---|---|---|---|---|
| `weaviate` (ref) | 0.648 | — | 0.666 / 0.629 | 0.587 | 2.32 s | |
| `pinecone` | 0.463 | −0.185 | 0.450 / 0.476 | 0.417 | 2.30 s | REJECT |
| `weaviate+rerank` | **0.698** | **+0.050** | 0.736 / 0.660 | 0.610 | 2.26 s | → stage 2 |
| `pinecone+rerank` | 0.504 | −0.144 | 0.530 / 0.476 | 0.451 | 2.92 s | REJECT |

Labelled diagnostic (not used for selection): Pinecone **dense-only** scores 0.607 vs a 0.664 reference (Δ −0.057). So ≈ 0.13 of the hybrid arm's loss is the
raw-score convex fusion I configured (Weaviate normalises each ranking before fusing) and the remainder is dense-only trailing lexical+dense on holdout.
Pinecone is rejected **as configured**, with the fix path named in the decision record.

Stage 2, paired, n = 172 per arm (86 q × 2 reps), concurrency 8 on both arms, 0 errors:
| gate | `weaviate` | `weaviate+rerank` | threshold | |
|---|---|---|---|---|
| correctness | 0.850 | **0.799** (Δ −0.051) | ≥ +0.030 | fail |
| groundedness | 0.925 | 0.922 | ≥ ref | fail (flat) |
| holdout correctness | 0.828 | **0.737** (Δ −0.091) | ≥ ref | fail |
| cost / query | $0.0188 | $0.0194 (+3 %) | ≤ 1.25× | pass |
| p50 latency | 17.6 s | 18.9 s (+7 %) | ≤ 1.25× | pass |

Why a retrieval gain became an answer loss: page_recall in the full pipeline is flat (0.680 → 0.690; decomposition/gap-fill unions already recover most pages),
while chunk_recall (0.657 → 0.639) and must_mention_recall (0.603 → 0.568) drop — the reranker prefers same-page but less complete chunks, and answers lose
required facts. Paired per question: 4 wins · 11 losses · 44 ties. Category correctness: factual_single 0.888 → 0.834, ambiguous_followup 0.934 → 0.815,
adversarial_hallucination 0.859 → 0.800, factual_multi 0.836 → 0.818, cross_drug 0.742 → 0.805 (the one gain). Mission ≈ 2 h 40 min (80 min of it an
aborted single-threaded stage 2), ≈ $10.

### 3.3 Cross-cutting measurements
- **Reference drift.** The unchanged Weaviate reference scored 0.681 / 0.648 / 0.664 page_recall across three stage-1 runs in one day (routing-LLM
  nondeterminism ≈ ±0.02). Paired design absorbs it; unpaired comparisons against old reports would not.
- **Judge noise** is ±0.07 correctness at n ≈ 44 (`docs/baseline-report.md`); stage 2 uses n = 172 paired and still required Δ ≥ +0.03.
- **Retrieval is not the binding constraint on this corpus.** 79 pages, two monographs: 4 hybrid-ranked chunks already cover most answers; the losses in the
  paired analysis were answer *completeness*, not missing pages.

## 4. Kept / changed / left alone
**Kept (unchanged, verified by the untouched test suite and identical default behaviour):** Weaviate hybrid search (`limit=4`, `alpha=0.65`, relative-score
fusion on `contextualized`), `text-embedding-3-small`, the checked-in chunking, the safety gate, decomposition/merge, generation, validation, citations.

**Changed / added (all inert with default knobs):**
| area | change | evidence |
|---|---|---|
| knobs | `HC_RAG_RETRIEVER=weaviate\|pageindex\|pinecone`, `HC_RAG_RERANKER=none\|pinecone`, `HC_RAG_RERANK_CANDIDATES` (12), `HC_RAG_RERANK_TOP_K` (4), `HC_RAG_RERANK_MODEL`, `HC_RAG_PINECONE_*`, `HC_RAG_PAGEINDEX_*` — `healthcare_rag/services/models.py`, `GraphSettings` | defaults tested; `make eval-smoke` green on the default arm |
| arms | `processors/pageindex_retrieval.py`, `processors/pinecone_retrieval.py`, `processors/rerank.py`; `storage/pageindex_index.py` (+ `make index-pageindex`), `storage/pinecone_store.py` (+ `make ingest-pinecone`); `Resources.pinecone()`; `hybrid_search(..., limit=4)` kwarg; arm dispatch in `graph/nodes/retrieve.py`; `engine.describe()` records retriever/reranker | 23 + 39 + 14 unit tests, no network |
| gate | `evals/pageindex_gate.py` (multi-arm, two-stage, JSON + exit codes, `--concurrency`), `tests/test_pageindex_gate.py` (61 tests) | self-check Δ = 0.000 on identical arms |
| docs | `docs/decisions/*.md`, this report, `docs/experiments/` trail, `AGENTS.md` knobs + gotchas, `evals/README.md` gate section, `.env.example` Pinecone block | — |
| deps | `pinecone>=7,<10` (resolves with `openai<2`); PageIndex is **not** a dependency (tree build runs in an isolated `uv run --with pageindex` env) | `uv pip` dry-run recorded in the decision log |

**Deliberately left alone, and why:** tuning Weaviate's `alpha`/`limit` (cheapest lever, but out of scope for "replace the engine?"; the gate is ready for it);
normalised fusion / RRF for Pinecone (would be tuning a rejected arm after the fact — recorded as the path to reopen it instead); generation and validation
(the validator is the hallucination backstop, see D15); multi-turn evals (retrieval verdict hinges on single-turn); PageIndex Cloud / `chat()` (would have
replaced generation too and confounded the comparison).

## 5. AI-coding process for this slice (what worked, what did not, what is left for the next teammate)
**How it was driven.** A Socratic *deep interview* (7 rounds, ambiguity gate 10 %) turned "should we use PageIndex instead of Weaviate?" into a spec with
frozen pass/fail thresholds *before* anything was built; an *autoresearch* mission then iterated: evaluator first, self-check, arms, gate, decision record —
one change per iteration, each with a machine-readable evaluation and a timestamped log (`docs/experiments/*/decision-log.md`). Implementation ran as two
parallel executor lanes with a strict file split (runtime code vs `evals/`), with the lead independently re-running tests, smokes and index stats rather than
trusting lane reports.

**Patterns that worked:** freezing thresholds before results (the reranker's stage-1 win would otherwise have been tempting to ship); a cheap deterministic
stage before the expensive judged stage; the Δ = 0 self-check with identical arms; pairing every candidate against a fresh reference (reference drift of
±0.02 would have produced false wins); a contrarian question in the interview ("what if the corpus is too small for this to matter?") that produced the
effort ceiling and the early-exit rule; labelling a diagnostic as such instead of quietly tuning.

**Patterns that did not:** my fusion configuration for Pinecone (raw-score convex combination) was a fairness bug that cost a diagnostic run; stage-2 time was
estimated from p50 latency and missed the serialised judge calls (38 s/iteration, not 17) — fixed by adding `--concurrency` to the gate; a background job
was first launched under the tool's timeout and had to be relaunched detached; `uv run pytest` does not put the repo root on `sys.path` here (`make test` /
`python -m pytest` does); lane agents' plain-text replies never reach the lead — reports must be sent explicitly.

**Artefacts for the next AI teammate:** `evals/pageindex_gate.py --arm-b <retriever>[+rerank]` is an eval loop a tool can call and parse (`pass`, `score`,
exit codes); `docs/decisions/*.md` each end with "what would change the verdict" so no one re-runs a settled question blind; `docs/experiments/` holds the
spec, mission, evaluator contract and per-iteration JSON; `AGENTS.md` documents every knob and the gotchas (PDF-only PageIndex local mode, Pinecone string
page_numbers, `openai<2` pin); a memory note records that LangSmith is over its monthly trace quota (runs still work on local rows).

## 6. Second-pass notes
Try `alpha`/`limit` sweeps and a larger-pool rerank with a completeness-aware reranker through the same gate; if Pinecone is ever wanted for operational
reasons, implement normalised fusion and re-run — the index and knobs are in place. Rotate the Pinecone key and delete the `healthcare-rag` index if no
reruns are planned.
