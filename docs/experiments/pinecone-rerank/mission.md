# Autoresearch mission: pinecone-rerank

- Slug: `pinecone-rerank` · Branch: `autoresearch/pinecone-rerank` (off `61da91e`, the PageIndex A/B base; never push) · Created 2026-08-20
- Predecessor: `pageindex-vs-weaviate` (REJECT at stage 1; see `docs/decisions/pageindex-vs-weaviate.md`). Same methodology, new candidates.
- Max runtime: **4h**. LLM/API spend guide: ~$15.

## Mission (one sentence)
Determine, with the frozen two-stage retrieval gate, whether (a) a Pinecone hybrid index and/or (b) a reranking step over the top-12 candidates
beats the current Weaviate hybrid retrieval stage — retrieval stage only, everything downstream identical — and write a go/no-go decision record.

## Arms (arm string = `<retriever>[+rerank]`)
| arm | HC_RAG_RETRIEVER | HC_RAG_RERANKER | what differs |
|---|---|---|---|
| `weaviate` (reference) | weaviate | none | today's hybrid: limit 4, alpha 0.65, relative-score fusion, text-embedding-3-small |
| `pinecone` | pinecone | none | Pinecone serverless, one index `healthcare-rag`, namespace per collection; dense = the same text-embedding-3-small; sparse = `pinecone-sparse-english-v0`; hybrid via convex scaling alpha 0.65; top 4 |
| `weaviate+rerank` | weaviate | pinecone | Weaviate hybrid fetches 12 candidates → Pinecone Inference `bge-reranker-v2-m3` → top 4 |
| `pinecone+rerank` | pinecone | pinecone | Pinecone hybrid fetches 12 → same reranker → top 4 |
Top-k into generation is 4 per collection on every arm (no context-size confound this time).

## Gates (unchanged from evaluator.json of the predecessor; thresholds frozen)
- Stage 1 (retrieval-only, 71 eligible golden questions, page_recall via `evals.evaluators.retrieval_page_hit`): each candidate vs the reference.
  Candidates with page_recall < reference are REJECTED. Among passers, the **single best** (highest page_recall; tie → lower retrieval latency) goes to stage 2.
- Stage 2 (paired `run_baseline`, core+holdout, `--repetitions 2`, fresh reference in the same session): Δcorrectness ≥ +0.03, groundedness ≥ ref,
  holdout correctness ≥ ref, cost ≤ 1.25×, p50 ≤ 1.25× → ADOPT; quality fail → REJECT; cost/latency-only fail → INCONCLUSIVE.

## Iteration plan
1. Runtime: Pinecone store + `pinecone_search`, reranker stage, knobs, tests, smokes. 2. Gate generalised to arm strings + multi-candidate stage 1.
3. `make ingest-pinecone`; stage 1 over 4 arms. 4. Stage 2 for the best passer (if any). 5. Decision record `docs/decisions/pinecone-rerank.md`.

## Hard rules
Weaviate default path byte-identical; safety/decomposition/generation/validation untouched; reranker cost & latency count as retrieval;
secrets only in `.env`; `sampling_params()`; no tuning past a stage-1 REJECT; never compare against historical numbers.
