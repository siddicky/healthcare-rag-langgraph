<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# docs

## Purpose
Human-facing documentation: the safety policy write-up, the deploy runbook, the
retrieval/routing experiment reports and the decision records they summarize, a
rendered project-journey timeline, and the take-home assignment's own PDF and
source monographs. Structural/generated docs (the auto-generated `openwiki/`
repo wiki) live outside this directory — see the root `AGENTS.md` map.

## Key Files
| File | Description |
|------|-------------|
| `safety.md` | The safety policy in full: why a runtime gate exists (vs. prompt-only), the terminal-refusal policy, the graph's PHI posture, and known limits — read before changing any safety-gate behaviour |
| `deploy.md` | The single followable prod deploy runbook (Fly.io, prod-only, tag-triggered) — secrets source of truth, the immutable-image-digest deploy primitive, exact copy-pastable steps |
| `retrieval-experiments.md` | Evidence report for the retrieval layer: three alternatives to Weaviate hybrid search (PageIndex, Pinecone hybrid, a bge reranker) were built behind opt-in knobs and measured with the frozen two-stage gate — all three lost, Weaviate stays |
| `routing-experiments.md` | Evidence report for the two routing evaluation lanes (query-response arm selection, semantic-safety classifier) — both currently `INCONCLUSIVE`, with the exact blocking reason for each |
| `baseline-report.md` | The baseline & migration report: what was measured (answer quality, safety, retrieval, latency/cost, multi-turn) and how, with every claim naming its LangSmith experiment |
| `journey.json` | Append-only-ish structured record of the project timeline — what was done, found, decided, and why, with evidence links; source of truth for `journey.html` |
| `journey.html` | Rendered, self-contained timeline view of `journey.json` (timeline, findings, experiments with comparison bars, decisions, artefacts, open items) |
| `build_journey_html.py` | `uv run python docs/build_journey_html.py` — regenerates `journey.html` from `journey.json`; re-run after editing the JSON, no network needed |
| `graph.mmd` | The committed mermaid diagram of the compiled LangGraph `StateGraph` — `tests/graph/test_graph_build.py::test_compiled_graph_mermaid_matches_committed_artifact` fails if this drifts from the actual compiled graph |
| `lipitor.pdf`, `metformin.pdf` | The two source drug monographs the RAG system is grounded in — the actual retrieval corpus source, chunked into `data/chunks_*.json` |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `decisions/` | Short, dated decision records (verdict + evidence pointers) for each retrieval/routing experiment (see `decisions/AGENTS.md`) |
| `experiments/` | Raw evidence trail behind the decision records — mission specs, iteration logs, evaluator configs, copied verbatim from the (git-excluded) autoresearch working directories (see `experiments/AGENTS.md`) |
| `assignment/` | The original take-home exercise PDF this whole repo responds to (see `assignment/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- `graph.mmd` is a checked artifact with a test asserting it matches the compiled graph — after any `build.py`/`routers.py` change, regenerate it (see the graph module for the export step) rather than hand-editing, or `test_graph_build.py` will fail.
- Every claim in `baseline-report.md` names the LangSmith experiment it came from; when adding a new claim, do the same — an unnamed number here is not trustworthy per the root `AGENTS.md` "Measure before/after" rule.
- `journey.json` is meant to be append-only-ish with stable ids — prefer adding a new entry over rewriting an old one, and keep `journey.html` regenerated (`make journey`) after any edit.
- `safety.md` and `retrieval-experiments.md`/`routing-experiments.md` are the required pre-reads named directly in the root `AGENTS.md` before changing safety behaviour or proposing a new retrieval/routing approach — don't duplicate their content elsewhere, link to them instead.
- `lipitor.pdf`/`metformin.pdf` are the ground truth for every `expected_source_chunk_ids`/`expected_source_pages` value in `evals/golden_dataset.json` and `evals/multiturn_dataset.json` — if the PDFs ever change, the chunk data and every golden example anchored to them need re-validation.

### Testing Requirements
No tests run against `docs/` content directly, except the one structural check:
```
uv run pytest tests/graph/test_graph_build.py -k mermaid -q   # docs/graph.mmd vs the compiled graph
make journey                                                   # regenerate journey.html after editing journey.json
```

### Common Patterns
- Decision records in `decisions/` follow a fixed header shape (verdict, date/branch, evaluator pointer, spend) — match it exactly when adding a new one; see `decisions/AGENTS.md`.
- Long-form experiment write-ups (`retrieval-experiments.md`, `routing-experiments.md`) always cross-link to their decision record(s) and to `evals/README.md`'s relevant section rather than re-explaining the gate mechanics inline.

## Dependencies

### Internal
- `evals/results/` (every report/decision here cites a specific experiment file there)
- `evals/pageindex_gate.py`, `evals/routing_gate.py` (the evaluators behind the retrieval/routing decision records)
- `docs/graph.mmd` ↔ `healthcare_rag/graph/build.py` (must stay in sync, enforced by test)

### External
- None beyond standard Markdown/HTML/PDF viewers — `build_journey_html.py` runs fully offline

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
