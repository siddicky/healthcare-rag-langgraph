<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# evals

## Purpose
The regression safety-net for the whole system: runs the golden question set and the
multi-turn conversation set through the real pipeline (Weaviate + OpenAI + the
LangGraph engine), scores every answer/turn with deterministic checks and LLM
judges, and writes a Markdown+JSON report per run under `results/` that the next
change is diffed against. Also hosts the retriever A/B gate, the query/safety
routing gate, and an in-process offline parity harness for the coach agent. See
the full family write-up in `README.md` — this file is a map of the code, not a
duplicate of it.

## Key Files

### Single-turn golden-set eval
| File | Description |
|------|-------------|
| `golden_dataset.json` | Source-of-truth 45+41 hand-written questions (`factual_single`, `factual_multi`, `cross_drug`, `ambiguous_followup`, `out_of_scope`, `unsafe_personal_advice`, `adversarial_hallucination`, `pii_or_phi`) |
| `dataset.py` | Loads `golden_dataset.json`, converts rows to LangSmith examples, upserts via stable uuid5 ids |
| `harness.py` | `make_target()` — builds the real RAG engine, runs one example, captures contexts/branches/latency/usage |
| `evaluators.py` | Deterministic evaluators (`answered`, `must_mention_recall`, `forbidden_content`, `numeric_advice_leak`, `retrieval_chunk_hit`, `retrieval_page_hit`, `right_collection_routed`, `latency`, `cost_and_tokens`, `branching`) plus LLM-judge evaluators (`CorrectnessVerdict`-backed correctness/groundedness/behavior) |
| `pricing.py` | Local $/token table; fallback when LangSmith's own cost isn't authoritative |
| `usage.py` | `LLMCallUsage` + `summarize_usage()` — shared usage record across legacy and graph engines |
| `report.py` | `aggregate()` + `write_report()` — builds `results/<experiment>.{json,md}`, pulls LangSmith stats, per-stage cost breakdown |
| `run_baseline.py` | CLI entrypoint: runs the pipeline + scores it (`--category`, `--example-id`, `--fail-under`, `--fail-over` CI gates) |
| `rescore.py` | Re-scores an existing experiment with new/changed evaluators, or regenerates a report from LangSmith alone (`--report-only`) |
| `compare.py` | Side-by-side table of multiple experiment reports (`make compare EXPS="a b c"`) |
| `calibrate.py` | Runs every evaluator against `judge_calibration.json` hand-labelled cases; drives `make calibrate` / `make test` |
| `judge_calibration.json` | Hand-labelled evaluator test cases (wrong number in fluent answer, refuses-but-gives-dose, PII echo, false-premise accepted/refuted, …) — the trust floor for the judges |
| `engines.py` | Evaluation-facing re-exports of the graph engine protocol |
| `seal_clean.py` | Canonical allowlist-aware git-cleanliness check (`is_clean_status`, `check_clean`) used by eval seals before trusting a report as reproducible |
| `watch_traces.py` | Ad-hoc local watcher for judge-call LangSmith traces (errors, latency, cost) — how stale-secret 401s and the score cap were found |

### Multi-turn conversation eval
| File | Description |
|------|-------------|
| `multiturn_dataset.json` | 22 conversations: `scripted` (fixed turns + expectations) and `simulated` (persona + opening, openevals-driven) |
| `multiturn_dataset.py` | Load/validate `multiturn_dataset.json`, LangSmith upsert (dataset `healthcare-rag-multiturn`) |
| `multiturn_harness.py` | Plays a conversation turn-by-turn through a fresh `user_id`; openevals simulator (`run_multiturn_simulation_async`) for `simulated` conversations; stopping-condition judge |
| `multiturn_evaluators.py` | Per-turn (`turn_*`, mean + worst) and conversation-level evaluators — `context_carryover`, `consistency`/`self_contradiction`, `safety_drift`, `pii_persistence`, `boundary_hit`/`boundaries_active`, `rubric_holds`, `latency_growth`, `conversation_cost` |
| `multiturn_report.py` | `aggregate()` + `per_turn_profile()` + `write_report()` — adds the by-turn-index profile table on top of the single-turn report shape |
| `run_multiturn.py` | CLI entrypoint (`--kind simulated`, `--category`, `--conversation-id`, `--dataset-file` for scratch datasets) |

### Retriever A/B gate
| File | Description |
|------|-------------|
| `pageindex_gate.py` | Two-stage paired gate comparing a reference retrieval arm against N candidates (`weaviate`/`pinecone`/`pageindex`, optional `+rerank`); stage 1 = retrieval-only `page_recall`, stage 2 = paired full eval with judges against 5 frozen threshold gates; `THRESHOLDS` dict is frozen per comparison |

### Query/safety routing gate
| File | Description |
|------|-------------|
| `routing_dataset_models.py` | Pydantic contract types: `SafetyCategory`, `Action`, `Split`, `RoutingRow`/`RoutingConversation`, `RoutingBundle`, `LangSmithExample`, sync protocols |
| `routing_dataset_validation.py` | `validate_bundle()` — id/count/stratum population checks on the routing dataset |
| `routing_dataset.py` | Loads `routing_dataset.json` + `routing_multiturn_dataset.json` into a `RoutingBundle`, syncs to LangSmith |
| `routing_dataset.json`, `routing_multiturn_dataset.json` | The query-routing and safety-drift-routing rows/conversations |
| `routing_prototypes.json` | Near-duplicate prototype phrasings used by holdout-paraphrase validation |
| `routing_calibration.py` | Loads `ChitchatFixture`/`SafetyDriftFixture` calibration rows, scores them, produces pass/fail per lane |
| `routing_evaluator_calibration.json` | The calibration fixture data consumed by `routing_calibration.py` |
| `routing_judges.py` | LLM-judge prompt builders/verdict models for chitchat and safety-drift calibration |
| `routing_evaluators.py` | `evaluate_routing_records()` — operational metrics (precision/recall) and per-safety-class metrics from raw routing records |
| `routing_multiturn_evaluators.py` | `boundary_replay_precision()` — the replay-precision invariant for the routing-safety lane |
| `routing_arm_runner.py`, `routing_arm_runtime.py` | Subprocess CLI + `run_arm()` that executes one routing arm in an isolated env for a stage |
| `routing_gate_args.py`, `routing_gate_models.py` | `GateArgs`; typed evidence/verdict models (`ArmBinding`, `ClassRecall`, `QueryEvidence`, `SafetyEvidence`, `GateDecision`, `Verdict`) |
| `routing_gate_checks.py` | Threshold comparison helpers — `at_least`, `ratio`, `safety_regressions`, `decision()` |
| `routing_gate_verdicts.py` | `evaluate_query()` / `evaluate_safety()` — turns evidence into a `GateDecision` |
| `routing_gate_runner.py` | `run_gate()` orchestration: binds arms, runs each phase, computes evidence, calls the verdict functions |
| `routing_gate_subprocess.py` | `SubprocessRoutingGateRunner` — the real (non-fixture) runner protocol implementation |
| `routing_gate.py` | CLI entrypoint (`uv run python -m evals.routing_gate --lane query\|safety --stage all\|1\|2 ...`) |
| `routing_provenance.py` | `RoutingProvenance` manifest — artifact hashes, arm env, experiment row counts; `compare_manifests()` cross-checks lanes |
| `routing_report.py`, `routing_report_io.py` | Typed report payload + Markdown rendering; transactional temp-write/publish/rollback for report pairs (crash-safe two-file commits) |
| `routing_gate_publish.py` | `publish_gate()` — binds a provenance manifest to a `GateDecision` and writes the publication pair |

### Offline coach-agent parity
| File | Description |
|------|-------------|
| `agent_cases.py` | `_catalog_cases()` — the catalog of offline coach conversation cases |
| `agent_chunks.py` | `ChunkCatalog` — resolves golden-dataset chunk ids to the coach's runtime chunk identity; raises `ChunkMappingError` on drift |
| `coach_fixtures.py` | Shared fixture builders for coach-engine test/eval cases |
| `offline_agent_fakes.py` | `OfflineGateway` — deterministic fake `LangChainLLMGateway` for offline (no-network) coach runs |
| `coach_engine.py` | `CoachEngine` — compiles the production coach graph in-process with isolated stores and offline fakes; `build_offline_coach_engine()` |
| `run_agent.py`, `run_agent_multiturn.py` | CLI entrypoints: `evals/run_agent.py --offline`, `evals/run_agent_multiturn.py --offline` |
| `agent_report.py` | `write_agent_report()` — renders the offline coach report |
| `agent_parity.py` | `AgentReport` model + `compare_reports()` — parity comparison across two coach reports; `MetricShapeError` on shape drift |
| `check_agent_parity.py` | `evals/check_agent_parity.py` CLI — the parity gate script |
| `parity.py` | `ParityGate` + `Report`/`Row`/`Aggregate` models — the general parity-gate machinery reused by the eval-seal tests |
| `parity_drills.py` | Synthetic report fixtures (`sealed_reports`) used to drill the parity gate's failure paths in tests |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `langsmith/` | LangSmith-side code evaluators, prompts/schemas, and Insights automation (see `langsmith/AGENTS.md`) |
| `results/` | Generated output — committed `.md`/`.json` reports per experiment, plus per-question detail and watcher logs. **Not** documented here (no AGENTS.md); treat as read-only evidence, never hand-edit |

## For AI Agents

### Working In This Directory
- Never point the harness at `data/conversations` — `build_rag`/`multiturn_harness` route history to a temp dir so eval runs can't corrupt real conversation state.
- Judge calls use a separate, un-traced OpenAI client (see `evaluators.py` `_client()`) so grading cost/latency never pollutes the system-under-trace numbers.
- Add a golden example or multi-turn conversation for every regression you find; `must_hold` invariants in a multi-turn conversation are the contract — write them before the turns.
- `evals/pageindex_gate.py` and `routing_gate.py` both freeze their thresholds/fixtures at the top of the module for the duration of one comparison — don't move them mid-comparison.
- When adding an evaluator, back-fill it onto prior experiments with `evals.rescore` so reports stay comparable.
- The two routing lanes (`current`/`deterministic`/`tool` query arms, `llm` safety classifier) are currently `INCONCLUSIVE` — read `docs/decisions/query-or-respond-vs-current.md` and `docs/decisions/semantic-router-vs-llm-safety.md` before touching routing code.

### Testing Requirements
```
make test              # offline: evaluator calibration + deterministic subset
make test-judges       # LLM-judge calibration (calls OpenAI, ~$0.10)
make calibrate         # print full evaluator calibration report
make eval-smoke        # 3-example golden-set smoke
make eval PREFIX=x     # full golden-set run -> results/<x>.md
make eval-multiturn-smoke
make eval-multiturn PREFIX=x
make eval-agent            # offline coach parity (single-turn)
make eval-agent-multiturn  # offline coach parity (multi-turn)
uv run python -m evals.routing_gate --lane query --stage all --json
uv run python -m evals.pageindex_gate --json --smoke
```
Unit tests for this package's pure functions live in `tests/` (`test_pageindex_gate.py`,
`test_routing_gate*.py`, `test_evaluators.py`, `test_evaluator_calibration.py`,
`test_eval_seal.py`, `test_parity_gate.py`, `test_agent_eval.py`), not inside `evals/` itself.

### Common Patterns
- Every eval CLI follows the same shape: build/parse args → run pipeline via a `harness`/`engine` → score with evaluator functions (LangSmith evaluator signature: `(inputs, outputs, reference_outputs) -> dict | list[dict]`) → `report.py`/`multiturn_report.py`/`routing_report.py` writes the artifact pair.
- Report/gate publication uses a temp-write-then-atomic-replace pattern with rollback (`routing_report_io.py`) to avoid partial artifacts on crash — follow that pattern for any new persisted report.
- Pydantic `BaseModel`/`RootModel`/`StrEnum` are used throughout for typed evidence and verdicts (`routing_gate_models.py`, `agent_chunks.py`, `agent_parity.py`) — prefer extending an existing model over adding loose dicts.

## Dependencies

### Internal
- `healthcare_rag/graph/` (`build_engine()`, `GraphEngine`) — the system under test for `harness.py` and `multiturn_harness.py`
- `healthcare_rag/agent/` — the coach graph compiled in-process by `coach_engine.py`
- `data/chunks_*.json` — chunk-id ground truth used by `retrieval_chunk_hit`/`retrieval_page_hit` and `agent_chunks.py`

### External
- `langsmith` (Client, tracing, dataset upsert)
- `openai` (judge calls via a dedicated un-traced client)
- `openevals` (`run_multiturn_simulation_async`, `create_async_llm_simulated_user`) — pinned via the app's `openai>=1.76,<2` constraint, see `README.md` dependency note
- `pydantic` (typed models across routing/parity/report modules)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
