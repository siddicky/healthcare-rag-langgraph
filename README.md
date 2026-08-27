# Hybrid RAG Agent with Answer Validation
[![Python Version](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![GitHub last commit](https://img.shields.io/github/last-commit/siddicky/healthcare-rag-langgraph)](https://github.com/siddicky/healthcare-rag-langgraph/commits) [![Release](https://img.shields.io/github/v/tag/siddicky/healthcare-rag-langgraph?label=release)](https://github.com/siddicky/healthcare-rag-langgraph/tags)

This project answers questions grounded in the Lipitor and Metformin product monographs. A LangGraph `StateGraph` runs the safety gate, retrieval, generation, and citation validation pipeline.

Instead of a rigid sequential pipeline (or racing multiple answer paths), the graph branches at runtime: every query is first classified by a safety gate, then conditionally clarified, decomposed into parallel retrieval fan-outs, and merged into a single validated answer. Key capabilities include:

*   **Intelligent Query Handling:** Utilizes conversation history for query clarification and decomposes complex questions into simpler sub-queries.
*   **Targeted Hybrid Retrieval:** Employs OpenAI function calling to route queries to the correct Weaviate vector store (Lipitor or Metformin) and retrieves relevant document chunks using Weaviate's hybrid search (BM25 + OpenAI embeddings with `relativeScoreFusion`).
*   **Context Enhancement:** Summarizes relevant snippets from conversation history and evaluates retrieval sufficiency, performing gap-filling via additional sub-queries if necessary.
*   **Validated Answer Synthesis:** Generates a freeform answer incorporating retrieved context and history summary, followed by a rigorous multi-step validation process detailed further below. This validation ensures the final answer is factually grounded in the source documents by checking cited evidence.
*   **Dialogue Promotion:** Suggests relevant follow-up questions based on the interaction.

The runtime has per-thread conversation memory and streams `GraphEngine` updates. LangGraph Studio and Agent Server remain development surfaces for non-sensitive synthetic input.

The RAG graph is one of two graphs in this repo. The second, the **coach agent** (`healthcare_rag/agent/`), is the member-facing product on [nymble.site](https://www.nymble.site/chat): it wraps the whole RAG graph as its `medical_lookup` tool, so a drug question is answered only by the grounded pipeline and relayed verbatim, while schedules, reminders, metrics, document intake and erasure stay useful around it.

## Submission record and evidence

Everything measurable in this repo is written down and linked from one place:

*   **Submission record** — an eight-tab artifact (submission page, findings deep-dive, production architecture, vendor-access evidence, live coach, Fly metrics, LangSmith, the repo wiki): [claude.ai/code/artifact/c3176b99](https://claude.ai/code/artifact/c3176b99-18fc-4e7d-8e20-de1365613c03). Sources and build live in [`artifacts/take-home-record/`](artifacts/take-home-record/).
*   **Technical write-up** — [`docs/writeup.md`](docs/writeup.md): what was inherited, what changed, the measured trade-offs, and a day-by-day appendix. Every number comes from a committed report under `evals/results/` (73 of them, each with per-query raw JSON) or a decision record under `docs/decisions/`.
*   **Safety posture** — [`docs/safety.md`](docs/safety.md). **Deploy runbook** — [`docs/deploy.md`](docs/deploy.md). **Security policy** — [`SECURITY.md`](SECURITY.md).

Headline deltas against the inherited baseline (`baseline-gpt4o-mini-25edbd33`, Aug 18, before any change), all on the same frontier judge:

| metric | baseline | now | note |
|---|---|---|---|
| `safe_redirect` (refuse-expected) | 0.00 | 0.64 | safety gate, D09 |
| `numeric_advice_leak` (lower is better) | 0.52 | 0.04 | no number with a clinical unit in any refusal |
| correctness (all 86) | 0.75 | 0.86 | synthesis + graph port, D08/D10; 4 of 59 answerable questions now refused |
| cost per query | $0.028 | $0.017 | validation is ~90% of it and stays (D15) |
| multi-turn `safety_drift` | 0.45 | 0.36 | refusal boundary persisted as thread state |
| PII persistence (27 conversations) | 0.31 | 0.10 | Presidio + deterministic scrub before every sink |

Three alternative retrievers (PageIndex, Pinecone hybrid, bge reranker) were built as opt-in arms and all lost to the Weaviate hybrid on a frozen two-stage paired gate ([`docs/retrieval-experiments.md`](docs/retrieval-experiments.md)). Run-to-run judge noise is ±0.05 on this set, so nothing here claims a smaller delta than that.

## Table of Contents
- [Submission record and evidence](#submission-record-and-evidence)
- [Core Pipeline Components](#core-pipeline-components)
- [Technology Stack](#technology-stack)
- [Conditional Pipeline Orchestration (LangGraph)](#conditional-pipeline-orchestration-langgraph)
- [Coach Agent Platform](#coach-agent-platform)
- [Privacy Sanitizer](#privacy-sanitizer)
- [Retrieval Engine Details](#retrieval-engine-details)
- [Setup & Execution](#setup--execution)
- [Deploying](#deploying)
- [Production Architecture](#production-architecture)
- [Tests and Evals](#tests-and-evals)
- [Routing Experiment Status](#routing-experiment-status)
- [Example Query Flow](#example-query-flow)
- [Detailed Answer Validation and Hallucination Handling](#detailed-answer-validation-and-hallucination-handling)

---

## Core pipeline components

**Clarification & Decomposition:**
*   **Clarification:** Uses conversation history to interpret follow-up questions containing ambiguous references (like pronouns) that depend on previous turns in the dialogue.
*   **Decomposition:** Breaks down complex questions into multiple, focused sub-queries specifically for retrieval. This process operates independently of conversation history.
*(Query refinement logic in the `clarify_query` / `decompose_query` graph nodes, `healthcare_rag/graph/nodes/preprocess.py`)*

**Conversation Context Summarization:** Before generating an answer, this component analyzes the current user query and the preceding conversation history. It identifies and extracts key snippets from the history that are relevant for providing context or answering the current question. This summary is then passed along to the answer generation step. *(The `extract_conversation_context` node)*

**Document Retrieval (Weaviate Hybrid RAG):** An LLM function call first analyzes the user query to determine the relevant medication, routing the request to the specific Weaviate vector store for either "Lipitor" or "Metformin". The system then retrieves relevant **document chunks** using Weaviate's hybrid search capabilities. The specifics of this hybrid search (combining dense and sparse methods with Relative Score Fusion) are detailed in the "Retrieval Engine Details" section below. *(Routing and search in `healthcare_rag/processors/retrieval.py`, driven by the `retrieve_documents` node)*

**Retrieval Evaluation & Gap-Filling:** After the merged retrieval, this component assesses whether the collected document chunks contain sufficient information to answer the user's query thoroughly. If the context is deemed insufficient, the evaluator generates new, targeted sub-queries to fetch additional relevant document chunks (one gap-fill round, phase-0 only). *(The `evaluate_retrieval` node)*

**Answer Generation:** This component receives the working query and the final set of retrieved document chunks (potentially augmented by gap-filling). Using this context, an LLM generates a freeform answer, aiming to include citations pointing back to the source documents. *(The `generate_answer` node)*

**Answer Validation:** The initial freeform answer undergoes a rigorous validation process to check for factual grounding and handle potential hallucinations. See the "Detailed Answer Validation and Hallucination Handling" section below for specifics. *(The `validate_answer` node, `healthcare_rag/processors/validation.py`)*

**Follow-Up Question Generation:** Based on the final answer and conversation context, the system can also generate relevant follow-up questions to guide the user or explore related topics. *(The `generate_follow_ups` node)*

**Prompt Management (Jinja2 Templates):** LLM interactions for various tasks (clarification, decomposition, evaluation, generation, validation, follow-ups) are driven by prompts defined in Jinja2 template files located inside the package at `healthcare_rag/prompts/` (shipped in the wheel as package data), allowing for dynamic prompt construction based on runtime data. *(Rendered by `healthcare_rag/graph/prompts.py`)*

**Actual Data Flow:**

The following diagram is the real graph shape (kept in sync by a mermaid drift test; see `docs/graph.mmd`):

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	safety_gate(safety_gate)
	finalize(finalize)
	clarify_query(clarify_query)
	extract_conversation_context(extract_conversation_context)
	decompose_query(decompose_query)
	retrieve_documents(retrieve_documents)
	merge_retrievals(merge_retrievals)
	evaluate_retrieval(evaluate_retrieval)
	generate_answer(generate_answer)
	validate_answer(validate_answer)
	generate_follow_ups(generate_follow_ups)
	__end__([<p>__end__</p>]):::last
	__start__ --> safety_gate;
	clarify_query --> decompose_query;
	decompose_query -.-> retrieve_documents;
	evaluate_retrieval -.-> generate_answer;
	evaluate_retrieval -.-> retrieve_documents;
	extract_conversation_context --> decompose_query;
	generate_answer --> validate_answer;
	generate_follow_ups --> finalize;
	merge_retrievals -.-> evaluate_retrieval;
	merge_retrievals -.-> generate_answer;
	retrieve_documents --> merge_retrievals;
	safety_gate -.-> clarify_query;
	safety_gate -.-> extract_conversation_context;
	safety_gate -.-> finalize;
	validate_answer -.-> finalize;
	validate_answer -.-> generate_follow_ups;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
```

## Technology stack

This project utilizes the following core technologies:

*   [![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)](https://www.python.org/) - Core programming language.
*   [![Weaviate](https://img.shields.io/badge/Weaviate-Vector_Database-green?logo=weaviate&logoColor=white)](https://weaviate.io/) - Vector database for hybrid search.
*   [![LangGraph](https://img.shields.io/badge/LangGraph-StateGraph_Runtime-blue)](https://github.com/langchain-ai/langgraph) - Conditional-pipeline orchestration, checkpointed threads, streaming.
*   [![OpenAI](https://img.shields.io/badge/OpenAI-LLMs_&_Embeddings-412991?logo=openai&logoColor=white)](https://openai.com/) - Language models for generation, embeddings, and function calling.
*   [![Docker](https://img.shields.io/badge/Docker-Containerization-2496ED?logo=docker&logoColor=white)](https://www.docker.com/) - Used via Docker Compose for running Weaviate.
*   [![Jinja2](https://img.shields.io/badge/Jinja2-Templating-B41717?logo=jinja&logoColor=white)](https://jinja.palletsprojects.com/) - For managing LLM prompts.
*   Docling - Python library for document parsing and chunking (especially PDFs).
*   [Pydantic](https://docs.pydantic.dev/latest/) - Structured model output and runtime data validation.
*   [![Mermaid](https://img.shields.io/badge/Mermaid-Diagrams-007F7F?logo=mermaid&logoColor=white)](https://mermaid.js.org/) - Used for rendering diagrams in this README.

---

## Conditional pipeline orchestration

The runtime is a custom LangGraph `StateGraph` (`healthcare_rag/graph/`) whose node names are the pipeline stages and whose conditional edges are the runtime self-evaluators:

*   **`safety_gate` →** the process-owned Presidio/deterministic sanitizer scans the current query and history before safety classification. Emergency / personal-advice / out-of-scope / prompt-injection messages short-circuit straight to `finalize` with a terminal templated response (see `docs/safety.md`). Identifier sanitization remains active during safety-classification ablations.
*   **`decompose_query` →** simple queries retrieve once; complex queries fan out via LangGraph `Send` — the parent (possibly clarified) query **plus** up to `HC_RAG_MAX_SUBQUERIES` sub-queries retrieve in parallel, and `merge_retrievals` de-duplicates their documents by `doc_id` into one merged set. There is deliberately **no speculative racing**: one answer and one validation per turn, on the original query (the measured "synthesis" behaviour).
*   **`evaluate_retrieval` →** one sufficiency check on the merged documents; an insufficient round fans out ≤3 gap-fill retrievals, after which the graph routes straight to generation (no second evaluation).
*   **`validate_answer` →** citation validation (below); a structuring failure writes no answer rather than failing open.
*   **`generate_follow_ups` →** answer-neutral UX feature; `finalize` persists the (scrubbed) question and the final answer to the thread's checkpointed history.

Conversation memory lives in the graph's checkpointer keyed by an opaque `thread_id` (in-memory by default; opt into SQLite via `HC_RAG_CHECKPOINT=sqlite:<path>` for threads that survive restarts). The supported identifier-bearing surface is `GraphEngine` with `updates` streaming, `durability="exit"`, and LangSmith tracing disabled. See `docs/safety.md` for the precise boundary and limitations.

`make dev` serves the graph on the local LangGraph Agent Server (Studio-compatible) and `scripts/langgraph_smoke.py` exercises threads, two-turn history carry-over, streaming and queued-run cancellation against it.
`langgraph.json` loads the gitignored `.env` for `langgraph dev`; `langgraph deploy`
uploads those variables as deployment secrets rather than baking them into the
image. The generated deployment image installs the pinned Presidio/spaCy model
through the root package declared in `dependencies`. Both Agent Server surfaces
remain limited to synthetic, non-sensitive input as described in
`docs/safety.md`.

---

## Coach agent platform

The member-facing coach is a second LangGraph application under
`healthcare_rag/agent/`, with its Next.js client under `frontend/`. A short,
model-free gate handles server-originated reminder wakes, attachments, safety
regexes, and erasure requests. Every other turn goes to one top-level
`create_agent` coach. Medical questions must use its `medical_lookup` tool,
which returns the RAG graph's validated answer directly. The coach must not
answer from its own medical knowledge or paraphrase the tool result.

The member chat uses the CopilotKit v2 headless transport. `useAgent` carries
the AG-UI stream, `useRenderTool` registers tool cards, and `useInterrupt`
renders approval requests. Member run inputs contain `question` and, for an
upload turn, an optional `attachment_id`. Interrupt resumes contain `accept`
and optional `fields`.

```mermaid
graph TD;
    __start__([__start__]) --> coach_gate;
    coach_gate -.->|valid cron wake| reminder_delivery;
    coach_gate -.->|attachment| claim_document;
    coach_gate -.->|red flag, injection, identifier recall| short_circuit;
    coach_gate -.->|erasure request| erase_my_data;
    coach_gate -.->|everything else| coach_agent;
    claim_document -.-> review_document;
    reminder_delivery --> finalize; short_circuit --> finalize; erase_my_data --> finalize; coach_agent --> finalize; review_document --> finalize;
    finalize --> __end__([__end__]);
```

The coach gate is deterministic and model-free. It deliberately does **not** carry the RAG graph's refusal mechanics (no LLM classifier, no persisted refusal boundary on the thread): a member who has just been refused a dosing question can still log an injection or move a reminder. The medical surface stays as strict as the RAG graph makes it because the only way a drug answer leaves the coach is `medical_lookup`, which runs the full safety gate and boundary, `return_direct`, output relayed verbatim. The failure mode to watch is the model answering a drug question from its own knowledge instead of calling the tool (`make eval-agent`, multi-turn `safety_drift`).

Generative UI is data-bound rather than prose-bound. Tools emit DATA envelopes,
`compose_ui` may emit only the catalog tree, and every fact-bearing component prop
must be a `{__ref: {turn_scope_id, block_id, pointer}}` object. The frontend
hydrates those references against same-turn envelopes and rejects literal facts,
unknown components, unresolved references, and unknown dispatch actions.

Recurring reminders are owner-scoped store records paired with Agent Server cron
runs. Cron creation and reconciliation use server-held internal credentials; a
member can create, edit, or cancel reminders only through natural-language coach
turns. A valid `cron_wake` is delivered model-free after the server revalidates its
owner, thread, reminder, and rotating token.

Document upload is a two-step reservation pipeline. The server atomically reserves
the client upload id for 15 minutes, reads supported content into a request-lifetime
buffer, performs extraction, scrubs the proposal, and stores only the bounded
proposal for review. The original bytes are cleared and never written to disk.

The member perimeter authenticates Supabase bearer tokens and exposes a fixed
allow-list: own threads, constrained update streams, branch copy, uploads, feedback,
and unified interrupt resumes. Cron administration, arbitrary state mutation,
MCP, and A2A are not member surfaces. Deploy the `coach` graph and HTTP app from
`langgraph.json`, configure the deployment secrets and allowed frontend origin,
then use `make deployed-smoke LANGGRAPH_DEPLOYMENT_URL=https://…` for the live
acceptance checks.

Self-erasure follows the same path as a real chat turn. `make forget-member
LANGGRAPH_DEPLOYMENT_URL=https://…` signs in as the member, asks the graph to erase
owner-scoped records, crons, and upload reservations, waits for the durable marker,
then snapshots and deletes all owned threads with the marker thread last. Use
`FORGET_ARGS=--dry-run` to list the thread phase without mutation. The safety and
privacy boundaries, residuals, and data-handling posture are documented in the
[coach platform safety addendum](docs/safety.md#coach-agent-platform-addendum).

---

## Privacy Sanitizer

Identifier scrubbing is in-process, not a sidecar. `healthcare_rag/processors/privacy.py` builds one Presidio `AnalyzerEngine` over `SpacyNlpEngine(en_core_web_sm)` per process (`presidio-analyzer==2.2.364`, spaCy 3.8.15, model 3.8.0, all exact-pinned) and unions its spans with deterministic healthcare-identifier patterns (`privacy_patterns.py`: health card, MRN, DOB, postal code, phone, email, vehicle id). Either detector firing is enough; clinical codes (RxCUI, NDC, DIN, LOINC, SNOMED) are carved out so dosing facts stay answerable.

*   **Sinks:** the safety-gate input (even when the gate is ablated), the checkpointed history, every model-authored query at its next sink, and the answer plus follow-ups at `finalize`.
*   **Fail-closed readiness:** `UNINITIALIZED → INITIALIZING → READY | FAILED`; startup validates the version pins, that the recognizer inventory covers all 17 entity types, and a sentinel probe. A mismatch raises a `PrivacyScanError` with a raw-free code (`PRIVACY_VERSION_MISMATCH`, `MODEL_MISMATCH`, `INVENTORY_MISMATCH`, `SENTINEL_FAILED`) rather than degrading. Input is capped at 16 KB.
*   **Image:** the model ships as a sha256-pinned wheel, `PRESIDIO_DEVICE=cpu`, no download at boot, non-root, read-only container.

HIPAA Safe Harbor is used as a coverage inventory (15 of 18 categories covered or deliberately diverged, documented in `docs/safety.md`). It is not a compliance claim; the app is Canadian-context and says so.

---

## Retrieval Engine Details

**Weaviate Hybrid Retrieval:** Document chunks are indexed in Weaviate collections (e.g., `Lipitor`, `Metformin`). Each chunk includes:
*   An OpenAI embedding vector (dense vector) used for semantic search, compared using cosine similarity.
*   Preparation for Weaviate's sparse keyword indexing via **BM25**. BM25 calculates relevance by considering both the frequency of query terms within a chunk (**Term Frequency**) and how unique those terms are across the entire dataset (**Inverse Document Frequency**). It also includes parameters to **normalize for document length**, preventing longer chunks from having an unfair advantage simply due to size. This results in higher scores for chunks where query terms appear relatively often and are distinctive across the corpus.
*   Associated metadata (source, page numbers, etc.).

Queries utilize Weaviate's `hybrid` search function, specifically configured with:
*   **Fusion Strategy:** `relativeScoreFusion`. This method prepares the scores from the vector search and keyword search for combination. It independently normalizes each set of scores using a min-max scaling approach: within each result set (vector or keyword), the highest score is mapped to 1, the lowest score is mapped to 0, and all other scores are scaled proportionally in between. These normalized scores (ranging from 0 to 1) are then combined based on the alpha parameter. This approach preserves more information about the relative differences between scores compared to the older rank-based `rankedFusion` method.
*   **Alpha Parameter:** Set to `0.65`. This gives slightly more weight to the vector search results (semantic similarity) compared to the keyword search results (exact matches) in the final ranking.

The system utilizes OpenAI's function calling capability to route the query to the appropriate Weaviate collection(s). Predefined function descriptions, one for each collection (e.g., "query_lipitor", "query_metformin"), are provided to the LLM along with the user query. The LLM analyzes the query and selects the relevant function(s) to call, thereby determining which specific collection(s) (Lipitor or Metformin) should be targeted for the subsequent hybrid search.

Weaviate remains the default retriever. PageIndex and Pinecone retrieval, plus
the Pinecone reranker, are opt-in experiment arms controlled by environment
variables. They require their own indexes and credentials. The measured
experiments rejected all three alternatives; see
[`docs/retrieval-experiments.md`](docs/retrieval-experiments.md) before proposing
another retrieval change, and use `evals/pageindex_gate.py` for paired tests.

---

## Setup and execution

**Requirements:** Python **3.11+** (the code uses `typing.Self`), [uv](https://docs.astral.sh/uv/), Docker (for Weaviate), an OpenAI API key. A LangSmith API key is optional but recommended (tracing + evals).

```bash
cp .env.example .env            # fill in OPENAI_API_KEY (+ LANGSMITH_API_KEY, LANGSMITH_TRACING=true)
make venv                       # uv venv (Python 3.12) + editable install of the app, evals and dev deps
make weaviate                   # docker compose up + wait for /v1/.well-known/ready
make ingest                     # load data/chunks_*.json into the Lipitor / Metformin collections
make run                        # interactive CLI  (python -m healthcare_rag)
```

To run the same GraphEngine CLI entirely from containers, keep the API keys in
the required, gitignored `.env` and use the opt-in `app` Compose profile:

```bash
make container-build            # build app + baked Presidio/spaCy model
make container-ingest           # start Weaviate and load the checked-in chunks
make container-run              # interactive CLI in the app container
```

The image installs the locked `presidio-analyzer==2.2.364`, spaCy 3.8.15 and
`en_core_web_sm` 3.8.0 during the build, then verifies the privacy analyzer can
initialize. Runtime model downloads are neither needed nor permitted by the
read-only, non-root container. The Compose app profile forces LangSmith tracing
off for identifier-bearing CLI input and reaches Weaviate over the internal
Compose network; the existing `make weaviate` workflow remains unchanged.

> Dependencies live in `pyproject.toml`, resolved through `uv.lock`. Re-chunking the PDFs
> (`healthcare_rag/processors/pdf_chunker.py`) needs the optional `ingest` extra (docling).

**Configuration.** See `.env.example`. Model selection is centralised in
`healthcare_rag/services/models.py` (`HC_RAG_LLM_MODEL`, `HC_RAG_VALIDATOR_MODEL`,
`HC_RAG_REASONING_EFFORT`; defaults `gpt-5.6-luna` / `gpt-5.6-terra`).
`HC_RAG_DISABLE_STAGES` short-circuits pipeline stages for ablation experiments.
The routing defaults remain `HC_RAG_QUERY_RESPONSE_ARM=current` and
`HC_RAG_SAFETY_CLASSIFIER=llm`.

**Observability.** For synthetic/non-sensitive development input, set `LANGSMITH_TRACING=true` and every query is traced to LangSmith as a
tree of named stages (clarify / decompose / retrieve / evaluate / answer / validate / follow-ups)
with per-call token usage and cost. See `healthcare_rag/services/tracing.py`.

**Evals.** `make eval PREFIX=<change>` runs the golden question set (`evals/golden_dataset.json`)
through the real pipeline as a LangSmith experiment and writes `evals/results/<experiment>.md`
(correctness, groundedness, safety behaviour, retrieval recall, latency p50/p95, cost per stage).
`make eval-multiturn` does the same for multi-turn conversations. Details in `evals/README.md`.

**Docs for humans and agents.** `AGENTS.md` contains conventions. `openwiki/` is
the generated repo wiki. Run `make wiki-update` to refresh it.

---

## Deploying

Production runs the clean-room OSS Agent Server (`server/`) on Fly.io — prod-only,
no staging. Releases are tag-driven with a human approval click:

1. `make release TAG=vX.Y.Z` — hermetic validation only; it prints the exact
   `git tag` / `git push` commands, the human runs them.
2. The pushed tag triggers `.github/workflows/deploy.yml`: build the image to
   GHCR, then the `deploy-prod` job waits for the GitHub `production`
   environment approval (required reviewer + `v*.*.*` tag policy, verified via API).
3. On approval it deploys the immutable image digest to Fly, waits on `/ok`,
   and runs the bounded, LLM-free smoke gate (isolation, perimeter, and disabled
   protocols; tracing off; redacted log artifact). The full ten-check acceptance
   smoke runs on demand.

Rollback is human-approved by design (a red post-deploy smoke leaves the
running version in place, pinned by a test): `make rollback TAG=vX.Y.Z
REASON=...` validates the tag and prints the dispatch that redeploys that
release's digest and `fly.prod.toml` together, shares the deploy concurrency
lock, skips secret resync, and re-runs the smoke against the rollback target.
Tag taxonomy and the rollback contract are in
[`docs/decisions/release-tags-and-rollback.md`](docs/decisions/release-tags-and-rollback.md);
the full runbook in [`docs/deploy.md`](docs/deploy.md) covers the one-time
bootstrap, secret seeding, ingest, and the post-first-deploy rollback exercise
(designed and implemented, not yet exercised end to end in production).

Caveats: server state (threads/store/runs/crons) is persisted in an unmanaged,
single-node Fly Postgres deployment with no automatic backups. Infrastructure
cost is ~$23–35/month, and the on-demand full acceptance smoke adds synthetic AI
usage. The credential-less Studio path
(`SERVER_LOCAL_DEV=1`, `make server-dev`) is a **development-only** convenience —
it is provably off in the Fly image and production environments.

---

## Production Architecture

Captured from `flyctl` on 2026-08-26 (release v25). The member never talks to the Agent Server directly; the Next.js app proxies the CopilotKit runtime route, and the server reaches its stores only over Fly's private 6PN.

| piece | where | what |
|---|---|---|
| member frontend | Vercel, `nymble.site` | Next.js 16, Supabase login, CopilotKit v2 headless `useAgent` over `/api/copilotkit` → `LANGGRAPH_DEPLOYMENT_URL` |
| `hc-rag-server-prod` | Fly `iad`, 2 × shared-1x 2 GB | the clean-room Agent Server (`server/`), digest-deployed from GHCR, `/ok` readiness, `SERVER_STORAGE=postgres`, Presidio in-process |
| `hc-rag-weaviate-prod` | Fly `iad`, 1 × 256 MB, 1 GB volume | Weaviate 1.30.2, `hc-rag-weaviate-prod.internal:8080`, anonymous access on the private network only |
| `hc-rag-server-prod-db` | Fly `iad`, 10 GB volume, no public IP | Postgres 17.9 + pgvector 0.8.6 (`hc-rag-pgvector:17`), checkpointer, store and `hc_threads`/`hc_runs`/`hc_crons` |
| OpenAI, Supabase, LangSmith | rented | one env-overridable seam each (`services/models.py`, the JWT verifier, opt-in tracing) |

Only the server has a public address. Weaviate and Postgres are reachable solely over `*.internal`. Secrets sync from the GitHub `production` environment by name; nothing here prints a value. The interactive version of this table, with the request path inside the server and the sanitizer figure, is the Architecture tab of the [submission record](https://claude.ai/code/artifact/c3176b99-18fc-4e7d-8e20-de1365613c03).

---

## Tests and Evals

*   **Unit and contract:** `make test` collects 1,956 backend tests (169 server-parity, 414 agent, 625 graph) and runs them in CI without any API key. `make parity` holds `server/` to the LangGraph platform contract through a pinned 0.12.6 oracle and ten characterised fixtures; CI also proves by SBOM that the vendor's `langgraph-api` package is absent from the production image. The frontend has 313 unit tests across 31 files plus a hermetic e2e spec; those run locally and are not yet in CI.
*   **Behaviour:** `make eval PREFIX=<change>` (86 golden examples, 45 core + 41 held out, eight categories) and `make eval-multiturn` (27 conversations, 131 turns: drift, carry-over, PII persistence). A calibrated `gpt-5.6-sol` judge must pass 21 hand-labelled cases; deterministic checks (`numeric_advice_leak`, `forbidden_content`, chunk and page recall) need no model. `make compare` diffs two reports.
*   **Coach:** `make eval-agent` and `make eval-agent-multiturn` run the coach graph in-process, offline.
*   **Retrieval changes:** judge them with `evals/pageindex_gate.py` (paired, two-stage, frozen thresholds) against a fresh reference, never against a historical number; the unchanged reference drifts ±0.02 page recall run to run.
*   **Production:** `make deployed-smoke` runs the ten-check acceptance suite; the LLM-free four-check gate runs after every deploy and rollback in about seven seconds.

Every report in `evals/results/` records the git SHA it ran at, and a seal script refuses to trust one produced from a dirty checkout.

---

## Routing Experiment Status

The query-response and safety-classifier controls are experimental and do not
change the production defaults: `HC_RAG_QUERY_RESPONSE_ARM=current` and
`HC_RAG_SAFETY_CLASSIFIER=llm`.

`HC_RAG_QUERY_RESPONSE_ARM` has three behaviors. `current` preserves the
existing out-of-scope scope response for a benign social turn. `deterministic`
returns fixed, scrubbed text only for a standalone greeting, thanks, goodbye, or
capability/scope question. `tool` lets the query-or-respond node choose only a
benign-social direct response; a malformed or non-direct social decision falls
back to fixed social text, while non-social turns continue through retrieval.
Direct response is never medical:
in-scope medical, mixed social/medical, ambiguous clinical, out-of-scope
knowledge, personal-advice, emergency, PHI-recall, and prompt-injection turns
remain on their safety/refusal/retrieval paths. Medical answers continue through
retrieval and citation validation.

The safety category enum is unchanged: `in_scope_informational`,
`personal_medical_advice`, `emergency_red_flag`, `out_of_scope`,
`prompt_injection`, and `ambiguous`. `benign_social` is a separate annotation,
not a seventh category; it may be true only for the four standalone social
intents above.

The query lane is **INCONCLUSIVE**. Its sealed judge calibration passed 22 of
24 fixtures, so no paired or paid run was attempted and it has no query-arm
metrics, deltas, cost, latency, or experiment URLs. See
[`evals/results/query-or-respond.md`](evals/results/query-or-respond.md) and
[`evals/results/query-or-respond.json`](evals/results/query-or-respond.json).

The Semantic Router safety lane is separately **INCONCLUSIVE** because the
exact `semantic-router==0.1.16` dependency conflicts with the unchanged project
bounds `openai>=1.76,<2` and `python-dotenv>=1.1`. It is not a selectable working
configuration. No adapter, configuration, calibration, stage, or paid semantic
run was attempted; it was not installed, imported, or exercised, and there are
no semantic metrics. See
[`evals/results/semantic-safety.md`](evals/results/semantic-safety.md) and
[`evals/results/semantic-safety.json`](evals/results/semantic-safety.json).

---

## Example query flow

Consider the query "What are the side effects of Lipitor?". The safety gate classifies it as in-scope informational (scrubbing any identifiers). The graph runs clarification and context extraction in parallel; the query is clear and simple, so decomposition produces a single retrieval branch. An LLM routes the query to the `Lipitor` collection using a function call. Weaviate performs hybrid retrieval. The merged results are evaluated; if sufficient, answer generation proceeds using the retrieved context and the history summary. A validation LLM checks the answer against the sources. Follow-up questions might be generated. The `finalize` node persists the scrubbed question and the validated answer to the thread and returns the final result. If the merged retrieval had been insufficient, the evaluation step would have triggered gap-filling sub-queries before answer generation.


---

## Answer validation and hallucination handling

To ensure the generated answers are factually grounded in the provided documents and to mitigate hallucinations, the system *(primarily via the `AnswerValidator` class, invoked by the `validate_answer` graph node)* employs a multi-step validation process after the initial answer generation:

1.  **Initial Generation with Attempted Citations:** The first step involves an LLM generating a freeform answer *(the `generate_answer` node)* based on the query, retrieved document chunks, and conversation context. This generation prompt encourages the LLM to include citations referencing the source documents.

2.  **Structured Parsing:** The raw, freeform answer (with attempted citations) is then processed using a structured output method (e.g., an LLM call constrained by a specific format or using a tool like Pydantic). This converts the answer into a structured list of individual "statement" objects. *(Parsing logic within `AnswerValidator`)*

3.  **Statement & Citation Objects:** Each statement object in the list typically contains:
    *   The text of the individual claim or statement being made.
    *   A corresponding "citation" object.
    The citation object itself contains:
    *   An identifier for the specific document chunk referenced.
    *   The exact quote from that document chunk which supposedly supports the statement.

4.  **Quote Verification Loop:** A validation function *(within `AnswerValidator`)* iterates through each structured statement object. For each statement, it performs a check:
    *   It retrieves the content of the document chunk referenced in the citation object.
    *   It verifies if the quote provided in the citation object can be found within that document chunk's content using **fuzzy matching**. This allows for minor variations and doesn't require an exact string match, succeeding if the match score exceeds a predefined threshold.

5.  **Statement Filtering:** If the quote verification fails for a statement (i.e., the provided quote is not found in the referenced document chunk, indicating a potential hallucination or mis-citation), that specific statement may be removed from the list. *(Filtering logic within `AnswerValidator`)*

6.  **Final Validated Output:** The final output consists of the remaining, verified statements, ensuring that the answer presented to the user is directly supported by evidence found within the source documents.

## Contact

For questions or support, contact Abdullah Siddique at [abdullah.siddique94@gmail.com](mailto:abdullah.siddique94@gmail.com).

You can also find me on [LinkedIn](https://www.linkedin.com/in/a-sdq/).

---
