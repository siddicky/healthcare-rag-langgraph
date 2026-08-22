# OpenWiki instructions for this repository

Audience: the next engineer or AI coding agent working on this repo. Optimise for
"can I safely change X?" over marketing prose.

Please make sure the wiki covers, as separate pages where sensible:

1. **Architecture overview** — the speculative-execution orchestrator
   (`healthcare_rag/orch/`): branches, supersession, how the "best" answer is
   selected, and the exact stage order (clarify / decompose / retrieve /
   evaluate / answer / validate / follow-ups). Include a diagram.
2. **Processors** (`healthcare_rag/processors/`) — one section per processor,
   which prompt template it uses (`prompts/*.yaml.j2`), which Pydantic model it
   returns, and which model tier it runs on.
3. **Answer validation** — structuring + fuzzy citation verification in
   `processors/validation.py`; what gets dropped and the fallback strings.
4. **Retrieval** — Weaviate hybrid search parameters (alpha, fusion, limit,
   `query_properties`), the schema in `storage/vector_store.py` (note `id_`),
   and ingestion/chunking (`processors/pdf_chunker.py`, `data/chunks_*.json`).
5. **Model configuration** — `healthcare_rag/services/models.py`: env vars,
   why `sampling_params` exists (GPT-5.x reasoning models vs temperature).
6. **Observability & evals** — `healthcare_rag/services/tracing.py`
   (LangSmith, opt-in) and the `evals/` package: golden dataset schema,
   evaluators (deterministic vs LLM-judge), how to run a baseline and compare
   experiments, where reports land (`evals/results/`).
7. **Runbook** — local setup with `uv`, Docker/Weaviate, ingestion, CLI, the
   Makefile targets, required env vars, known gotchas (Python ≥3.11 needed;
   `requirements.txt` pins are unsatisfiable; Weaviate `restart` policy).
8. **Safety posture** — what the app currently does and does not do about
   out-of-scope questions, personal medical advice, PII; point to the eval
   categories that measure this.

## Routing-experiment truthfulness contract

Treat the two routing lanes as independent outcome records, not as a summary
of gate capability code. Production defaults remain
`HC_RAG_QUERY_RESPONSE_ARM=current` and `HC_RAG_SAFETY_CLASSIFIER=llm`.

- The query-response lane is **INCONCLUSIVE** solely because authored
  query-judge calibration passed 22 of 24 fixtures: two acceptable greetings
  scored 0.78 and 0.72, below the 0.80 minimum. No paired or paid query-arm
  measurement was attempted. This is a measured calibration observation, not
  a quality conclusion about any arm.
- The semantic-safety lane is dependency-**INCONCLUSIVE** because exact
  `semantic-router==0.1.16` is unsatisfiable with unchanged
  `openai>=1.76,<2` and `python-dotenv>=1.1`. Semantic package installation,
  import, adapter, configuration, calibration, stage 1, stage 2, runtime, and
  paid measurement were all not attempted. This is a dependency fact, not a
  runtime or quality result.

Never say either semantic candidate was evaluated, not adopted, or rejected;
never attribute its outcome to `RunnerError` or an unimplemented runtime.
Gate smoke/runtime code documents capability and untested hypotheses, not
completed experiment evidence. Keep measured observations, dependency facts,
and untested hypotheses distinct in generated pages.

For `openwiki/observability/routing-evals.md`, the `openwiki.invariants`
front-matter must keep the binding-integrity invariant but must not mention
`RunnerError`, an unimplemented runtime, paid or real runs, evaluation,
adoption, or rejection, and must not attribute either verdict to runtime. Its
outcome invariant must state the equivalent of: query evidence stops at failed
authored judge calibration and semantic evidence stops at dependency preflight;
neither lane has a completed paired or paid measurement.

Do not paste secrets, PDF contents, or full chunk JSON. Link to files with
paths and line references. Keep pages short; prefer more pages over long ones.
