# Deep Interview Spec: PageIndex vs Weaviate — retrieval-layer go/no-go (autoresearch mission)

## Metadata
- Interview ID: di-pageindex-vs-weaviate-20260820
- Rounds: 7 (+ Round 0 topology gate)
- Final Ambiguity Score: 10.0%
- Type: brownfield
- Generated: 2026-08-20
- Threshold: 0.1
- Threshold Source: ~/.claude/settings.json
- Initial Context Summarized: no
- Mode: --autoresearch (hand-off target: `oh-my-claudecode:autoresearch`, not omc-plan/autopilot/ralph/team)
- Status: PASSED

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.90 | 0.35 | 0.315 |
| Constraint Clarity | 0.90 | 0.25 | 0.225 |
| Success Criteria | 0.90 | 0.25 | 0.225 |
| Context Clarity | 0.90 | 0.15 | 0.135 |
| **Total Clarity** | | | **0.900** |
| **Ambiguity** | | | **0.100** |

## Topology
| Component | Status | Description | Coverage / Deferral Note |
|-----------|--------|-------------|--------------------------|
| PageIndex retriever integration | active | PageIndex-backed retrieval arm behind a knob; Weaviate stays default | AC-1..AC-6 |
| Head-to-head evaluation | active | Two-stage evaluator: retrieval-only gate, then paired full eval, both arms | AC-7..AC-12 |
| Go/no-go decision record | active | Written ADOPT / REJECT / INCONCLUSIVE verdict against fixed criteria | AC-13..AC-15 |

## Goal
Decide, with measurements from this repo's own eval harness, whether PageIndex's reasoning-based tree-search
retrieval should replace Weaviate hybrid search as the **retrieval stage only** of the healthcare-rag LangGraph
pipeline. Build a knob-switchable PageIndex arm that selects tree nodes with one LLM call and maps the selected
page ranges back onto the existing contextualised chunks, run a cheap retrieval-only gate and then a paired,
repeated, core+holdout comparison against a fresh Weaviate run, and write a decision record that states the
verdict and the numbers it rests on. Weaviate remains the default and is byte-for-byte unaffected.

## Constraints
- **Retrieval-only swap.** Safety gate, decomposition/merge, generation, validation, citations and follow-ups are
  untouched. Only the callable injected at `Resources.hybrid_search` (`healthcare_rag/graph/resources.py:31`)
  differs between arms.
- **Adapter = tree → pages → existing chunks.** One LLM call over the PageIndex tree (titles + summaries) picks
  ≤ K nodes; their `start_index..end_index` page ranges are mapped to chunks in `data/chunks_*.json` whose
  `page_numbers` intersect. Returned objects have the same shape as today's Weaviate results (`id_`, `text`,
  `contextualized`, `doc_source`, `page_numbers`), so `chunk_recall`/`page_recall`/citations work unchanged.
- **PageIndex local mode only** (`pip install pageindex`, MIT; `OPENAI_API_KEY`; PDF input). Source PDFs:
  `docs/lipitor.pdf` (48 p), `docs/metformin.pdf` (31 p). No PageIndex Cloud, no `chat()`.
- **Node-selection cost/latency counts** as retrieval cost/latency in every metric.
- **Paired measurement.** Never compare against historical report numbers; the Weaviate arm is re-run in the same
  session with identical settings. Judge noise is ±0.07 correctness at n≈44 (`docs/baseline-report.md:183`).
- **Effort ceiling:** ~4 h wall-clock, ~$15 LLM spend for the whole mission. Stage 1 may end the mission early.
- **Evaluator integrity:** once `evals/pageindex_gate.py` passes its own smoke test, its thresholds, the golden
  dataset (`evals/golden_dataset.json`) and metric definitions (`evals/evaluators.py`) are frozen for the mission.
- Repo rules: secrets only in `.env`; model selection via `healthcare_rag/services/models.py` + `sampling_params()`;
  log via `logging.getLogger("MedicalRAG")`; `uv` for Python; prompts as Jinja YAML in `healthcare_rag/prompts/`.
- Work on a branch off `phase-2/langgraph-port` (e.g. `autoresearch/pageindex-vs-weaviate`); do not push.

## Non-Goals
- Replacing Weaviate as the default, deleting Weaviate code, or changing `make ingest`.
- PageIndex Cloud, OCR/scanned input, PageIndex `chat()`/MCP bridge, the "raw page text" adapter.
- Multi-turn evals (`make eval-multiturn`) — out of scope for the verdict; flag as follow-up in the decision record.
- Tuning Weaviate (alpha/limit) or the generation/validation stages.
- Committing to `main`, opening PRs, updating `openwiki/`.

## Acceptance Criteria
**Integration**
- [ ] AC-1 `HC_RAG_RETRIEVER=weaviate|pageindex` (default `weaviate`) read in `GraphSettings.from_env()`; with the
      default, the graph's behaviour and the Weaviate code path are unchanged (existing tests pass unmodified).
- [ ] AC-2 `make index-pageindex` builds trees for both PDFs in PageIndex local mode and caches them as
      `data/pageindex_tree_lipitor.json` / `data/pageindex_tree_metformin.json` (idempotent; skips if present).
- [ ] AC-3 A `pageindex_search(collection_name, query)` callable with the same signature/return type as
      `hybrid_search()` selects ≤ K=4 nodes with a single LLM call (model = `HC_RAG_LLM_MODEL` via
      `sampling_params()`), maps page ranges → chunks by `page_numbers`, caps at 8 chunks, preserves node order.
- [ ] AC-4 The PageIndex arm reuses the existing tool-gateway collection routing in `graph/nodes/retrieve.py`;
      only the per-collection search differs.
- [ ] AC-5 Unit tests: page-range → chunk mapping (incl. empty selection, out-of-range pages), result shape parity
      with Weaviate results, default knob leaves Weaviate path selected.
- [ ] AC-6 `make eval-smoke` runs green with `HC_RAG_RETRIEVER=pageindex`.

**Evaluation**
- [ ] AC-7 `evals/pageindex_gate.py` exists; `uv run python -m evals.pageindex_gate --json` prints one JSON object
      to stdout with required `pass: bool`, plus `score: float`, `stage: 1|2`, `verdict`, per-arm metrics; exit
      code 0 = pass, 2 = stage-1 reject, 3 = stage-2 fail, 1 = error.
- [ ] AC-8 Stage 1 (retrieval only, no generation/judges): both retrievers on all 86 golden questions
      (core + holdout); `page_recall` computed with the existing `evals/evaluators.py` definition. If
      PageIndex mean page_recall < Weaviate mean page_recall → `pass=false`, `verdict="REJECT"`, exit 2, stop.
- [ ] AC-9 Stage 2: paired `evals.run_baseline` runs for both arms with `--split core --split holdout
      --repetitions 2 --concurrency 1`, identical settings, same session; judges enabled.
- [ ] AC-10 Stage-2 pass iff ALL hold (means over repetitions, PageIndex vs paired Weaviate arm):
      Δcorrectness ≥ +0.03 · groundedness ≥ Weaviate · holdout-only correctness ≥ Weaviate ·
      cost/query ≤ 1.25× Weaviate (reference ≈ $0.036) · p50 latency ≤ 1.25× Weaviate (reference ≈ 20 s).
- [ ] AC-11 `score` = Δcorrectness (stage 2) or Δpage_recall (stage 1); `--smoke` flag runs 3 questions per arm
      to validate the script without LLM judges.
- [ ] AC-12 Writes `evals/results/pageindex-vs-weaviate.md` (per-arm, per-split, per-category table via the same
      formatting as `evals.compare`) and keeps the raw per-run JSON alongside.

**Decision record**
- [ ] AC-13 `docs/decisions/pageindex-vs-weaviate.md` states ADOPT / REJECT / INCONCLUSIVE, the stage reached,
      the gate table with both arms' numbers, total mission cost and wall-clock, and what would change the verdict.
- [ ] AC-14 It records the known confounds: small corpus (79 pages), ±0.07 judge noise, extra LLM hop in retrieval,
      PDF-only local mode, and that multi-turn was not evaluated.
- [ ] AC-15 ADOPT is only written when AC-10 passes; REJECT when AC-8 fails or AC-10 fails on quality;
      INCONCLUSIVE when AC-10 fails only on cost/latency within noise or the ceiling was hit first.

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| "Use PageIndex instead of Weaviate" means swap the whole RAG | PageIndex's API is `chat()`; our graph wants a retriever | Retrieval stage only; generation/validation/safety untouched (R2) |
| PageIndex exposes a local tree-search retrieval call | Source: `LocalAPI` has `submit_document/get_tree/get_ocr` only; reasoning is done by the chat agent | We implement the one-call node selector ourselves (R3) |
| A +0.03 gain on one run is meaningful | Repo measures ±0.07 swing run-to-run | Paired fresh baseline, 2 reps, core+holdout, holdout guard (R5) |
| PageIndex can win on this corpus | Benchmarks are on 1,000-page filings; ours is 79 pages at 0.87 chunk_recall | Cheap stage-1 page_recall gate may REJECT early; 4 h / $15 ceiling (R4, Contrarian) |
| Page text must replace chunks | Breaks chunk_recall, citations, context size | Map selected pages onto existing chunks (R6, Simplifier) |
| Markdown chunks could be indexed | Local mode is PDF-only | Index `docs/*.pdf` directly |
| "Should be used" = any quality gain | Ops/cost matter for a healthcare app | Quality must rise AND cost/latency held within +25% (R1) |

## Technical Context
- Retrieval: `healthcare_rag/processors/retrieval.py:61-76` `hybrid_search()` — `limit=4`, `alpha=0.65`,
  `HybridFusion.RELATIVE_SCORE`, query property `contextualized`; result docs carry `id_`, `text`,
  `contextualized`, `doc_source`, `page_numbers`. Merge/dedup by doc id at `retrieval.py:80-84`.
- Injection seam: `Resources.hybrid_search: Callable | None` (`healthcare_rag/graph/resources.py:31`); node
  `healthcare_rag/graph/nodes/retrieve.py:31-56` (PHI scrub → gateway routing to `Lipitor`/`Metformin` → search per
  collection, 3× retry → union).
- Settings: `healthcare_rag/graph/settings.py` `GraphSettings.from_env()`; models/knobs in
  `healthcare_rag/services/models.py` (`HC_RAG_LLM_MODEL` default gpt-5.6-luna, `sampling_params()`).
- Data: `data/chunks_lipitor.json` (119) / `data/chunks_metformin.json` (54); `metadata.origin.filename` =
  `lipitor.pdf` / `metformin.pdf`; PDFs at `docs/`.
- Eval: `evals/run_baseline.py` flags `--split --repetitions --concurrency --no-judges --fail-under --fail-over
  --no-sync --limit`; metrics in `evals/evaluators.py` (correctness, groundedness, hallucinated,
  must_mention_recall = LLM judges; chunk_recall, page_recall = deterministic; latency, cost); golden items carry
  `expected_source_pages` and `expected_source_chunk_ids`; 45 core + 41 holdout; `make compare EXPS=...`.
- Latest baseline (`docs/baseline-report.md`): correctness 0.90, chunk_recall 0.87, groundedness ≈0.95, p50 ≈16 s,
  $0.0285/query, ~11.6 LLM calls/query.
- PageIndex (`VectifyAI/PageIndex`, MIT): `PageIndexClient(index_model=..., chat_model=...)`,
  `submit_document(pdf)` → tree `{title,node_id,start_index,end_index,summary,nodes}`; `get_tree(doc_id,
  node_summary=True)`; `get_ocr(doc_id, format="page")` → per-page text (PyPDF2); ≈$0.001/page to index;
  LiteLLM model naming; local mode PDF-only; no LLM retrieval primitive — `agent_tools.py` only exposes
  structure/page-content tools for the chat agent.

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| WeaviateHybridRetriever | core domain | alpha, limit, fusion, collection | implements RetrieverCallable; default arm |
| PageIndexRetriever | core domain | tree cache path, K, selector model, chunk cap | implements RetrieverCallable; maps TreeNode → Chunk |
| RetrieverCallable | supporting | (collection_name, query) → QueryResultList | injected at Resources.hybrid_search |
| TreeIndex / TreeNode | external system | title, node_id, start_index, end_index, summary, nodes | built per Monograph by PageIndex |
| NodeSelector | supporting | prompt, model, K | one LLM call per query; selects TreeNodes |
| Monograph | core domain | pdf path, collection name, pages | has Chunks; has TreeIndex |
| Chunk | core domain | id_, text, contextualized, page_numbers, doc_source | belongs to Monograph |
| GoldenDataset | supporting | question, split, expected_source_pages, expected_source_chunk_ids | drives both stages |
| EvalMetric | supporting | correctness, groundedness, page_recall, chunk_recall, cost, p50 | produced by evals.run_baseline / gate |
| RetrievalGate (stage 1) | supporting | page_recall per arm | may REJECT early |
| PairedEval (stage 2) | supporting | 2 reps, core+holdout, both arms | produces EvalReport |
| EvalReport | supporting | per-arm/per-split tables | feeds DecisionRecord |
| DecisionRecord | core domain | verdict, stage, gate table, confounds, cost | terminal artifact |
| EffortCeiling | supporting | 4 h, ~$15 | bounds the mission |

## Ontology Convergence
| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|----------------|
| 1 | 8 | 8 | - | - | - |
| 2 | 10 | 2 | 0 | 8 | 80% |
| 3 | 11 | 1 | 0 | 10 | 91% |
| 4 | 13 | 2 | 0 | 11 | 85% |
| 5 | 13 | 0 | 0 | 13 | 100% |
| 6 | 13 | 0 | 0 | 13 | 100% |
| 7 | 13 | 0 | 0 | 13 | 100% |
(14 rows above because RetrieverCallable was split out of the two retrievers at crystallisation for precision.)

## Interview Transcript
<details>
<summary>Full Q&A (7 rounds + topology)</summary>

### Round 0 — Topology
**Q:** 3 components: PageIndex retriever integration / Head-to-head evaluation / Go/no-go decision record?
**A:** Looks right.

### Round 1 — decision-record / Success Criteria
**Q:** What would make you say "yes, switch to PageIndex"?
**A:** Beat on quality, hold cost/latency (within a tolerance).
**Ambiguity:** 48.5% (Goal 0.60, Constraints 0.40, Criteria 0.40, Context 0.70)

### Round 2 — integration / Goal
**Q:** What exactly gets replaced — retrieval only, or retrieval + generation via `chat()`?
**A:** Retrieval stage only.
**Ambiguity:** 35.8% (0.80 / 0.50 / 0.50 / 0.75)

### Round 3 — eval / Success Criteria
**Q:** Which numeric pass condition?
**A:** correctness +0.03, groundedness ≥ baseline, cost and p50 ≤ +25%.
**Ambiguity:** 27.5% (0.85 / 0.55 / 0.65 / 0.85)

### Round 4 — decision-record / Constraints (CONTRARIAN)
**Q:** What if the corpus is too small for tree-search to matter — how much may autoresearch spend before concluding that?
**A:** Cheap retrieval-only gate first, then full eval; ceiling ~4 h / ~$15.
**Ambiguity:** 23.0% (0.85 / 0.60 / 0.75 / 0.90)

### Round 5 — eval / Constraints
**Q:** How to handle ±0.07 judge noise?
**A:** Paired fresh baseline, both arms, core+holdout, --repetitions 2, holdout guard.
**Ambiguity:** 20.5% (0.85 / 0.65 / 0.80 / 0.90)

### Round 6 — integration / Constraints (SIMPLIFIER)
**Q:** Simplest integration that is still a fair comparison?
**A:** Tree picks pages → map to existing contextualised chunks by page_numbers.
**Ambiguity:** 12.5% (0.90 / 0.85 / 0.85 / 0.95)

### Round 7 — eval / Success Criteria (evaluator gate)
**Q:** Which evaluator command should autoresearch be given?
**A:** New `evals/pageindex_gate.py`, two stages, JSON + exit code.
**Ambiguity:** 10.0% (0.90 / 0.90 / 0.90 / 0.90) — threshold met.
</details>
