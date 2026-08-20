# Decision log — pinecone-rerank / run-20260820-02

## Iteration 0 — setup
- Base `61da91e` (PageIndex A/B work, committed by a sibling session); branch `autoresearch/pinecone-rerank`. Tree clean; no live local session on this checkout.
- User decisions: Pinecone hosted rerank (`bge-reranker-v2-m3`) with a PINECONE_API_KEY they will add; 4 arms through stage 1; best passer only to stage 2.
- Weaviate embeds with text-embedding-3-small (1536-d, cosine) → Pinecone arm reuses the identical embedding; the engine is the only difference (plus fusion formula).
- Lanes: runtime (Pinecone store/search + reranker + knobs + tests) ∥ gate generalisation (arm strings, multi-candidate stage 1, best-passer stage 2).
- 13:12Z: PINECONE_API_KEY added to `.env` (user supplied it in chat → recommend rotating after the experiment). Verified: auth OK, 0 indexes,
  `bge-reranker-v2-m3` rerank and `pinecone-sparse-english-v0` sparse embed respond. Blocker cleared.

## Iteration 1a — gate generalised (15:46Z)
- `evals/pageindex_gate.py`: repeatable `--arm-b`, arm grammar `<retriever>[+rerank]`, `--report-name`, `--stage2-arm`, `--min-page-recall-delta`;
  stage 1 runs reference once + every candidate; best passer (max page_recall, tie → lower latency) goes to stage 2; no passer → REJECT exit 2.
  `settings_for_arm` sets HC_RAG_RETRIEVER + HC_RAG_RERANKER; stage-2 subprocess env carries both. THRESHOLDS/exit codes unchanged (frozen).
- Verified: 61 tests pass, ruff clean; smoke self-check (ref weaviate, candidates weaviate + pageindex, n=3): weaviate candidate Δ=0.000 ✅, pageindex 0.667 selected.
- Side fix: `_rationale` crashed when `stage2` was None (stage-1-only INCONCLUSIVE report) — fixed.

## Iteration 1b — runtime arms built (15:59Z)
- New: `healthcare_rag/storage/pinecone_store.py` (+ `make ingest-pinecone`), `healthcare_rag/processors/pinecone_retrieval.py`, `healthcare_rag/processors/rerank.py`;
  knobs HC_RAG_RETRIEVER=pinecone, HC_RAG_RERANKER=none|pinecone, HC_RAG_RERANK_CANDIDATES=12, HC_RAG_RERANK_TOP_K=4, HC_RAG_RERANK_MODEL=bge-reranker-v2-m3,
  HC_RAG_PINECONE_INDEX=healthcare-rag, HC_RAG_PINECONE_ALPHA=0.65. `hybrid_search(..., limit=4)` kwarg added (default path unchanged).
- Verified by lead: `python -m pytest -q` → 395 passed; openai 1.109.1 unchanged; pinecone 9.1.0; index `healthcare-rag` dotproduct/1536: lipitor 119, metformin 54.
- Smokes (n=3, 0 errors, plumbing only): weaviate corr 0.93 / page_recall 0.72 / $0.0148 / p50 20.9 s · pinecone 0.60 / 0.50 / $0.0314 / 18.1 s · weaviate+rerank 0.88 / 0.83 / $0.0158 / 22.2 s.

## Iteration 2 — stage-1 gate, 4 arms (started 16:00Z)
- `pageindex_gate --stage 1 --arm-a weaviate --arm-b pinecone --arm-b weaviate+rerank --arm-b pinecone+rerank --report-name pinecone-rerank`
- Runtime-lane notes for the decision record: (a) Pinecone arm embeds `contextualized` only, whereas Weaviate's text2vec-openai embeds class name + all five
  properties → dense vectors are close but not identical (confound, arguably in Pinecone's favour). (b) `page_numbers` stored as string lists in Pinecone
  metadata, converted back on read (parity asserted in tests). (c) Sync Pinecone/OpenAI-embeddings clients via `anyio.to_thread.run_sync` because the lazy
  `Resources` singleton outlives event loops. (d) Rerank measured 367 ms for 12→4; surfaced via a nested `@traceable` run + `RERANK_APPLIED` log line.
  (e) Pinecone smoke `pipeline_error 0.33` = one runaway answer hitting the pre-existing 16 KB privacy guard; re-runs clean — arm-agnostic.
- Cost/latency gates read run_baseline's LOCAL aggregates (`est_cost_usd`, `latency_p50_s`) — LangSmith quota irrelevant to the verdict.
- **Result (16:12Z, 695 s):** ref weaviate page_recall 0.648 (core .666 / holdout .629) · pinecone 0.463 (Δ −0.185) ❌ · weaviate+rerank 0.698 (Δ +0.050; .736/.660) ✅ selected ·
  pinecone+rerank 0.504 (Δ −0.144) ❌. 0 errors on all arms. Retrieval latency 2.3 / 2.3 / 2.3 / 2.9 s. Evaluation: `evaluations/iteration-0002.json`, report `stage1-report.md`.
- Reference drift vs this morning's run (0.681 → 0.648) = routing-LLM nondeterminism; paired design absorbs it, but stage-1 deltas < ~0.03 are noise.
- Pinecone confound: convex combination of raw dot-product + raw sparse scores vs Weaviate's normalised relative-score fusion. Not tuned (mission rule); a
  labelled diagnostic (dense-only, HC_RAG_PINECONE_ALPHA=1.0, stage 1 only, report `pinecone-dense-diag`) runs before stage 2 to characterise the confound only.

## Iteration 3 — diagnostic + stage 2 for `weaviate+rerank` (started 16:14Z)
- Stage 2: `--stage 2 --skip-stage1 --arm-a weaviate --arm-b weaviate+rerank --stage2-arm weaviate+rerank --report-name pinecone-rerank-stage2` (core+holdout, 2 reps, concurrency 1).
- 16:13Z: relaunched detached (nohup) to avoid the tool timeout; monitor armed.
- **Diagnostic (16:18Z, 294 s, labelled, not used for selection):** Pinecone dense-only (α=1.0) page_recall 0.607 vs ref 0.664 → Δ −0.057 (core 0.695 vs 0.675; holdout 0.517 vs 0.652).
  Hybrid arm was −0.185 ⇒ ~0.13 of the loss is the convex raw-score fusion; the rest is dense-only vs Weaviate's lexical+dense hybrid. Verdict on Pinecone: REJECT as configured; confound quantified.
- Reference samples today: 0.681 / 0.648 / 0.664 → routing nondeterminism ≈ ±0.02 page_recall.
- Stage 2 started 16:18Z: reference arm `pi-gate-weaviate` (86 × 2 reps, judges gpt-5.6-sol), then `pi-gate-weaviate-rerank`.
- 17:31Z status: reference arm ~99/172 iterations at ~38 s/it (judges serialised per query; my 17 s/it estimate used p50 latency only).
  Projected: reference done ≈18:07Z, candidate ≈19:55Z → ~20 min past the 19:35Z ceiling. Decision: extend ceiling to 20:05Z rather than discard
  ~1.7 h of paired data; user informed and can hard-stop. LangSmith rate-limit warnings (928 lines) are noise; 0 pipeline errors so far.
- 17:36Z: **user requested speed** → aborted the concurrency-1 stage 2 (reference arm at 122/172; log kept as `stage2-c1-aborted.stderr`, not used).
  Added `--concurrency` passthrough to `evals/pageindex_gate.py` (61 tests, ruff clean). Relaunched 17:37Z at `--concurrency 8` for BOTH arms.
  Consequence for the record: absolute p50 latency is inflated by contention on both arms equally; the 1.25× latency gate is relative and remains valid,
  but the p50 numbers must not be compared with the concurrency-1 baseline report.
- 17:51Z: stage-2 reference arm done (14 min @ concurrency 8, 0 errors, n=172): corr 0.850 (core .870 / holdout .828), groundedness .925, page_recall .680,
  $0.0188/q, p50 17.6 s. Candidate must reach corr ≥ .880, grounded ≥ .925, holdout ≥ .828, cost ≤ $0.0235, p50 ≤ 22.0 s. Report `evals/results/pi-gate-weaviate-89bc4ed3.json`.
- **Stage 2 result (18:05Z, 28 min @ concurrency 8, n=172/arm, 0 errors): REJECT, exit 3.** weaviate+rerank corr 0.799 vs 0.850 (Δ −0.051), holdout 0.737 vs 0.828,
  groundedness flat (.922 vs .925), cost +3.4 %, p50 +7.5 % (both pass). page_recall .690 vs .680, chunk_recall .639 vs .657, must_mention .568 vs .603;
  paired 4 wins / 11 losses / 44 ties. Reranker verified applied (bge scores on contexts). Evaluation: `evaluations/iteration-0003.json`.

## Iteration 4 — decision record (18:08Z)
- Wrote `docs/decisions/pinecone-rerank.md`: REJECT both candidates; Pinecone as-configured (fusion confound quantified), reranker fails quality gates.
- Mission closed inside the extended ceiling. Working tree uncommitted for review; nothing pushed. Follow-ups: delete Pinecone index, rotate key.
