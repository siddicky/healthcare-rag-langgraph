---
type: component map
title: Graph stages, prompts, and models
description: Mapping of each LangGraph stage to its prompt template, Pydantic output model, model tier, and owning node, plus the extension rules for adding or changing a stage.
tags: [processors, prompts, llm, langgraph]
openwiki:
  roles: [architecture, domain]
  change_kinds: [public-api, prompt-change]
  source_paths: [healthcare_rag/graph/prompts.py, healthcare_rag/graph/llm.py, healthcare_rag/graph/nodes, healthcare_rag/prompts]
  symbols: [PromptRegistry, STAGE_FILES, RESPONSE_MODELS, LangChainLLMGateway, astructured, acomplete, aroute_tools, sampling_params]
  test_paths: [tests/graph/test_prompt_fidelity.py, tests/graph/test_route_tools.py]
  invariants: [Every LLM stage is fail-soft: astructured/acomplete return the caller-supplied default on any exception.,validate_answer is the only stage on the validator model tier; every other stage uses the default model.]
  validation_commands: [make test, make eval-smoke]
---

# Graph stages, prompts, and models

`PromptRegistry` (`healthcare_rag/graph/prompts.py`) renders `healthcare_rag/prompts/<file>.yaml.j2` through Jinja, YAML-loads the messages, and converts them to LangChain `SystemMessage`/`HumanMessage`. `STAGE_FILES` maps stage names to template stems and `RESPONSE_MODELS` pins each structured stage's Pydantic model. `LangChainLLMGateway` (`graph/llm.py`) executes stages: `astructured` (with_structured_output, `method="json_schema"`, strict when `HC_RAG_STRUCTURED_STRICT`), `acomplete` (plain text), and `aroute_tools` (tool-calling retrieval routing). All are fail-soft — any exception returns the caller's default. `sampling_params` keeps calls model-family compatible; see [model configuration](../configuration/models-and-runtime.md).

| Stage (gateway name) | Owning node | Template | Output model | Tier / temperature |
|---|---|---|---|---|
| `safety_gate` | `safety_gate` (`nodes/safety.py`) | `safety_gate.yaml.j2` | `SafetyAssessment` | default, 0.0; wrapped by `SafetyGate.evaluate` → `SafetyDecision`. See [safety gate](../safety/gate.md) |
| `clarify_query` | `clarify_query` (`nodes/preprocess.py`) | `clarify_query.yaml.j2` | `ClarifiedQuery` | default, unset; skipped without history context or when disabled |
| `decompose_query` | `decompose_query` (`nodes/preprocess.py`) | `decompose_query.yaml.j2` | `DecomposedQuery` | default, unset; complexity-gated and capped |
| `extract_conversation_context` | `extract_conversation_context` (`nodes/preprocess.py`) | `context_extraction.yaml.j2` | `RelevantHistoryContext` | default, 0.1; no history returns `required_context=False`, empty snippets |
| retrieval routing | `retrieve_documents` (`nodes/retrieve.py`) | no template | LangChain `ToolCall[]` via `aroute_tools` + `build_routing_tools` | default; then the selected retrieval arm's search. See [retrieval](../retrieval/weaviate-and-ingestion.md) and [retrieval arms](../retrieval/retrieval-arms.md) |
| `pageindex_select` | inside `pageindex_search` (`processors/pageindex_retrieval.py`) | `pageindex_select.yaml.j2` | `PageIndexSelection` | default, unset; only on the pageindex arm; fail-soft to an empty selection |
| `query_or_respond` | `generate_query_or_respond` (`nodes/query_or_respond.py`) | `query_or_respond.yaml.j2` | tool call via `aquery_or_respond` → `QueryOrRespondDecision` (no `RESPONSE_MODELS` entry — the decision is parsed from a bound tool call, not structured output) | default; only when `HC_RAG_QUERY_RESPONSE_ARM=tool`; direct content must pass the [direct-output policy](../safety/privacy-sanitizer.md) |
| `evaluate_retrieval` | `evaluate_retrieval` (`nodes/evaluate.py`) | `retrieval_evaluation.yaml.j2` | `RetrievalEvaluation` | default, 0.1; drives the one gap-fill round |
| `generate_answer` | `generate_answer` (`nodes/generate.py`) | `answer_generation.yaml.j2` | plain string (`acomplete`) | default, 0.1; plus `formatted_docs`/`prompt_id_map` from `format_documents_for_prompt` |
| `validate_answer` | `validate_answer` (`nodes/generate.py`) | `answer_structuring.yaml.j2` | `CitedAnswerResult` (via `AnswerValidator`) | **validator**, 0.0; quote threshold 85. See [validation](validation.md) |
| `generate_follow_ups` | `generate_follow_ups` (`nodes/generate.py`) | `follow_up_questions.yaml.j2` | `FollowUpQuestions` | default, unset; runs only with a validated answer and `user_id` |

`healthcare_rag/processors/` now holds the reusable logic the nodes call (`safety.py`, `safety_responses.py`, `social_responses.py` (benign-social direct text), `privacy.py` + `privacy_patterns.py` (Presidio sanitizer), `direct_output_policy.py` (tool-arm answer gating), `refusal_boundary.py`, `validation.py`, `retrieval.py`, `pageindex_retrieval.py`, `pinecone_retrieval.py`, `rerank.py`, `generation.py`, `pdf_chunker.py`); `base.py` only provides the `log_timing` decorator. The old `PromptManager`/`LLMParserService`/processor-class layer is gone.

## Contracts and change rules

* `ClarifiedQuery` forces `clarified_query` back to original when ambiguity is `clear and specific`; `DecomposedQuery` normalizes `simple` to `[original]` (`models/queries.py`). The graph keys clarification and decomposition on those normalized differences — preserve the invariants.
* `RelevantHistoryContext` forcibly clears snippets when `required_context=False` (`models/answers.py`); its `relevant_snippets` feed the generation prompt as `conversation_context`.
* Generation maps real Weaviate UUIDs to sequential `doc_N` for the prompt (`format_documents_for_prompt`); `formatted_docs` and `prompt_id_map` must reach validation together with the answer.
* The answer prompt instructs cite-every-claim and say-when-unknown; runtime refusal policy lives in the [safety gate](../safety/gate.md), upstream of generation.

## Adding or changing a stage

To add a stage: write the template, add the stage to `STAGE_FILES` and (if structured) `RESPONSE_MODELS`, add the node in `graph/nodes/`, wire it in `graph/build.py` with a `NODE_*` constant, extend `RAGState` (and `safety_gate`'s per-turn reset), and respect `HC_RAG_DISABLE_STAGES` if the stage should be ablatable (valid stages: safety, clarify, decompose, evaluate, validate, followups — `services/models.py`). Disabled stages return pass-through values: validate returns the raw answer, follow-ups `[]`. `tests/graph/test_prompt_fidelity.py` pins stage→prompt wiring and `tests/graph/test_route_tools.py` the routing tools.

**Focused validation:** `make test` for wiring; then `make eval-smoke`, and a filtered/full eval for prompt or model changes (`make eval-nojudge PREFIX=stage-change`, judges for answer/safety behavior).
