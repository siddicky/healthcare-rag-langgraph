Customized the prompt around the assignment's strongest apparent directions: production readiness, regression evaluation, AI-contributor enablement, and the required healthcare-safety work. I preserved the repository-specific routing evidence rules from your original brief.

# OpenWiki instructions for this repository

Audience: the engineer or AI coding agent who inherits this healthcare RAG system. Optimize for practical questions such as "where should I make this change?", "what could it break?", and "how do I verify it safely?"

The wiki should explain the submitted system as it exists in the repository. Inspect the implementation, tests, configuration, commit history, and decision records before making claims. Do not treat this assignment brief as evidence of implemented behavior.

Keep pages short and link to source files with paths and line references. Do not paste secrets, private assignment material, complete PDF contents, or full chunk datasets.

## Assignment context

This repository began as a RAG application that answers questions using healthcare product monographs for Lipitor and Metformin.

The submission must include healthcare safety work. It also develops the project in these directions:

- Production readiness: make the demo maintainable and usable by a small team.
- Regression protection: make changes measurable through tests and evaluations.
- AI-contributor readiness: leave useful instructions, conventions, tools, and feedback loops for the next AI-assisted contributor.

Document only work that the repository proves was completed. Keep implemented behavior, measured results, design intentions, and future ideas separate.

## Required wiki coverage

Create separate pages where that makes navigation easier.

### 1. System architecture

Document the LangGraph `StateGraph` runtime under `healthcare_rag/graph/`.

Cover:

- Graph construction in `build.py`.
- Routing decisions in `routers.py`.
- Each node under `graph/nodes/`.
- State passed between nodes.
- The exact execution order: safety gate, clarification, decomposition, retrieval, evaluation, answer generation, validation, and follow-up generation.
- Conditional branches, termination paths, and failure behavior.
- The boundary between deterministic code and model-driven decisions.

Include a diagram that matches the current implementation.

The speculative-execution orchestrator formerly under `healthcare_rag/orch/` was removed during the Phase 2 port in commit `3435caf`. Do not describe it as part of the current architecture.

### 2. Healthcare safety posture

Treat this as a first-class system concern, not a disclaimer page.

Explain:

- Which questions the application answers.
- Which requests it refuses or redirects.
- How it handles personal medical advice, diagnosis, emergencies, dosage decisions, contraindications, off-label use, out-of-scope questions, and requests unsupported by the monographs.
- What personal or sensitive data the application must not collect, retain, log, or send unnecessarily to model providers.
- Where safety checks run in the graph.
- Which checks are deterministic and which depend on a model.
- What happens when a safety component fails or returns an uncertain result.
- The exact user-facing refusal and fallback behavior.
- Known gaps and boundaries that remain.

Link every claimed safeguard to its implementation and tests. Point to the evaluation categories and fixtures that measure the behavior. Never claim that a safety property is enforced when it appears only in a prompt, proposal, or unexecuted experiment.

### 3. Processing pipeline

Create one section per processor under `healthcare_rag/processors/`.

For each processor, identify:

- Its responsibility.
- Its inputs and outputs.
- The prompt template under `prompts/*.yaml.j2`.
- The Pydantic response model.
- The configured model tier.
- Retry, timeout, parsing, and fallback behavior where present.
- The graph node or service that invokes it.
- Safety-sensitive assumptions and likely failure modes.

### 4. Retrieval and ingestion

Document:

- Weaviate hybrid-search settings, including `alpha`, fusion method, result limit, and `query_properties`.
- The collection schema in `storage/vector_store.py`, including the `id_` field.
- Document ingestion and chunking in `processors/pdf_chunker.py`.
- The purpose and structure of `data/chunks_*.json` without reproducing full records.
- Metadata used for filtering, traceability, and citations.
- How to add or replace a product monograph safely.
- How retrieval failures or weak evidence affect the final answer.

### 5. Answer construction and citation validation

Explain the complete path from retrieved evidence to the response shown to a user.

Cover:

- Answer structuring and fuzzy citation verification in `processors/validation.py`.
- Citation matching rules and thresholds.
- Which content or citations validation can remove.
- Exact fallback strings.
- What happens when no usable evidence remains.
- Limitations of fuzzy verification.
- Tests and evaluations that protect this behavior.

### 6. Model configuration and cost controls

Document `healthcare_rag/services/models.py`.

Include:

- Required and optional environment variables.
- Available model tiers and which components use them.
- Why `sampling_params` exists.
- The difference between GPT-5.x reasoning-model controls and temperature-based configuration.
- Any token, concurrency, caching, or model-routing decisions that affect cost.
- How to change a model without silently invalidating evaluations or safety assumptions.

Do not imply that a cost improvement was measured unless the repository contains the measurement.

### 7. Evaluation and regression workflow

Document the `evals/` package as the main safety net for changes.

Cover:

- The golden-dataset schema.
- Evaluation categories, especially healthcare safety, refusal behavior, groundedness, citations, and out-of-scope requests.
- Deterministic evaluators versus model-judged evaluators.
- How to run a baseline.
- How to compare experiments.
- Where reports are written under `evals/results/`.
- What constitutes a valid comparison.
- Known calibration limits and sources of nondeterminism.
- How a contributor should decide whether a change made the system better or worse.

Measured observations must include their fixture counts, thresholds, configuration, and execution status. Never turn a calibration result into a broader product-quality conclusion.

### 8. Observability

Document `healthcare_rag/services/tracing.py`.

Explain:

- How LangSmith tracing is enabled.
- Why tracing is opt-in.
- What data can appear in traces.
- How to avoid exposing personal information, secrets, or source-document content.
- Which graph stages and model calls are observable.
- What remains invisible during failure investigation.

### 9. Local development and operations runbook

Provide a tested path for running the project from a clean checkout.

Include:

- Python 3.11 or newer.
- Dependency installation with `uv`.
- The role of `pyproject.toml` and `uv.lock`.
- Required environment variables.
- Docker and Weaviate startup.
- The Weaviate restart policy.
- Product-monograph ingestion.
- CLI usage.
- Relevant Makefile targets.
- Test, lint, type-check, and evaluation commands.
- Common startup and runtime failures with concrete recovery steps.

The obsolete root `requirements.txt` was removed. Follow `docs/decisions/dependabot-requirements-txt.md` and do not tell contributors to restore or use that file.

### 10. Guidance for AI-assisted contributors

Make the repository easier for the next engineer using an AI coding tool.

Document:

- Repository rules and contributor instructions an AI agent must read first.
- Safe commands for setup, tests, evaluations, and local execution.
- Files or generated data that should not be edited casually.
- Architectural invariants that must survive changes.
- A change checklist covering code, tests, safety, evaluations, documentation, and secrets.
- Reliable patterns discovered during the project.
- Approaches that failed or produced misleading results.
- Skills, hooks, agent instructions, memory notes, conventions, or evaluation loops left in the repository.
- How an AI agent can verify a change through the real application instead of relying only on source inspection.

The page should give the next contributor an operational advantage, not merely state that AI tools were used.

### 11. Engineering decisions and scope

Create a concise decision record explaining:

- Why production readiness, regression protection, and AI-contributor readiness were chosen alongside the required safety work.
- The largest trade-off made during the submission.
- What was deliberately left unchanged and why.
- What evidence supports each completed direction.
- What deserves a second pass.
- What should be attempted with another week of work.

Do not invent motives. Derive them from pull-request descriptions, commits, decision records, and the implementation. Mark anything else as an inference.

## Routing-experiment truthfulness contract

Treat the query-response and semantic-safety lanes as independent outcome records. Do not infer experimental results from the presence of gate or runtime code.

Production defaults remain:

- `HC_RAG_QUERY_RESPONSE_ARM=current`
- `HC_RAG_SAFETY_CLASSIFIER=llm`

The query-response lane is `INCONCLUSIVE` because authored query-judge calibration passed 22 of 24 fixtures. Two acceptable greetings scored `0.78` and `0.72`, below the `0.80` minimum. No paired or paid query-arm measurement was attempted. This is a calibration result, not a quality result for either arm.

The semantic-safety lane is dependency-`INCONCLUSIVE` because exact `semantic-router==0.1.16` cannot be installed with the unchanged constraints `openai>=1.76,<2` and `python-dotenv>=1.1`. Package installation, import, adapter configuration, calibration, both experiment stages, runtime execution, and paid measurement were not attempted.

Never say that either semantic candidate was evaluated, rejected, or not adopted. Do not attribute the result to `RunnerError` or an unimplemented runtime. Gate and smoke-test code show capability or an untested hypothesis. They do not prove that an experiment ran.

Keep these categories distinct:

- Measured observations.
- Dependency facts.
- Implemented capabilities.
- Untested hypotheses.
- Proposed future work.

For `openwiki/observability/routing-evals.md`, preserve the binding-integrity invariant in the `openwiki.invariants` front matter. The outcome invariant must state that query evidence stops at failed authored-judge calibration, semantic evidence stops at dependency preflight, and neither lane has a completed paired or paid measurement.

The invariant must not mention `RunnerError`, an unimplemented runtime, adoption, rejection, or a runtime-based verdict.

## Documentation quality rules

- Prefer evidence over interpretation.
- Link claims to implementation, tests, evaluation reports, commits, or decision records.
- Include commands only after checking that they match the current project.
- State whether a command was run or merely documented.
- Distinguish current behavior from recommendations.
- Use diagrams only when they make control flow or ownership easier to understand.
- Keep confidential assignment material out of the repository.
- Never include secrets, complete monographs, full chunk datasets, personal information, or private submission links.

The assignment source was reviewed in full: :codex-file-citation{path="/Users/siddicky/Downloads/nymble - Technical Exercise - Senior Python Engineer.pdf" purpose="source"}
