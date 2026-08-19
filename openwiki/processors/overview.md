---
type: component map
title: LLM processors and prompts
description: Processor responsibilities, prompt contracts, Pydantic outputs, model tiers, and extension seams.
tags: [processors, prompts, llm]
---

# LLM processors and prompts

`PromptManager` renders `prompts/<name>.yaml.j2` through Jinja and YAML-loads OpenAI chat messages; `BaseProcessor` routes parsed calls through `LLMParserService` (`healthcare_rag/processors/base.py#L49-L111`). All entries below use the common `HC_RAG_LLM_MODEL` tier unless stated **validator tier**; `MedicalRAG` wires the latter from `HC_RAG_VALIDATOR_MODEL` (`healthcare_rag/pipeline/medical_rag.py#L84-L102`). `sampling_params` makes every OpenAI call model-family compatible; see [model configuration](../configuration/models-and-runtime.md).

| Owner and method | Prompt | Output | Tier and behavior |
|---|---|---|---|
| `SafetyGate.assess` / `evaluate` | `safety_gate.yaml.j2` | `SafetyAssessment`; `evaluate` returns `SafetyDecision` | common tier at temperature 0; deterministic pre-checks OR-ed with one LLM call (two only when an `ignore_instructions` override is unpacked); LLM failure falls back to deterministic-only. See [safety gate](../safety/gate.md) |
| `QueryPreprocessor.clarify_query_async` | `clarify_query.yaml.j2` | `ClarifiedQuery` | common; skips LLM with no history and returns unchanged clear query |
| `QueryPreprocessor.decompose_query_async` | `decompose_query.yaml.j2` | `DecomposedQuery` | common; default is one original query |
| `ConversationContextProcessor.extract_relevant_context` | `context_extraction.yaml.j2` | `RelevantHistoryContext` | common; no history returns `required_context=False` and empty snippets |
| `QueryRouter.route_query_async` | no Jinja template | `QueryResultList` | common; OpenAI function tools select collection(s), then Weaviate hybrid search |
| `RetrievalEvaluator.evaluate_retrieval` | `retrieval_evaluation.yaml.j2` | `RetrievalEvaluation`, returns augmented `QueryResultList` | common; can route additional queries concurrently |
| `AnswerGenerator.generate_answer_async` | `answer_generation.yaml.j2` | `AnswerGenerationResult` | common; freeform completion plus formatted docs and temporary ID map |
| `AnswerValidator.structure_and_validate_async` | `answer_structuring.yaml.j2` | `(CitedAnswerResult | None, str | None)` | **validator tier**; resolves/checks citations |
| `FollowUpQuestionsGenerator.generate_follow_up_questions` | `follow_up_questions.yaml.j2` | `FollowUpQuestions` | common; requested three questions, default empty list |

`document_relevance.yaml.j2` names `DocumentRelevanceEvaluation`, but no matching model or call site exists in the inspected runtime; do not treat it as an active processor without adding both contract and registration.

## Contracts and change rules

* `ClarifiedQuery` forces `clarified_query` back to original when ambiguity is `clear and specific`; `DecomposedQuery` similarly normalizes `simple` to `[original]` (`healthcare_rag/models/queries.py#L4-L43`). The orchestrator creates/supersedes branches based on text difference, so preserve these invariants.
* `RelevantHistoryContext` forcibly clears snippets if `required_context=False` (`healthcare_rag/models/answers.py#L44-L68`). It runs independently of branch refinement but gates answer generation.
* Router output groups `QueryDocument`s by source/query in `QueryResultList`; evaluator appends, rather than deduplicates, additional result groups (`healthcare_rag/processors/retrieval.py#L222-L313`). See [retrieval](../retrieval/weaviate-and-ingestion.md).
* Generation maps real Weaviate UUIDs to sequential `doc_N` only for prompt/answer citation handling. The result must retain `retrieval_results`, `formatted_docs`, and `prompt_id_map` for validation (`healthcare_rag/processors/generation.py#L15-L119`).
* The answer prompt tells the model to cite every claim and say it does not know when context lacks an answer; this is a prompt instruction — the runtime refusal policy lives in the [safety gate](../safety/gate.md), upstream of generation. The structuring prompt requests verbatim answer segments and source quote evidence. See [validation](validation.md) and [safety posture](../safety/posture.md).

## Feature flags and extension surface

`HC_RAG_DISABLE_STAGES` can short-circuit safety, clarify, decompose, evaluate, validate, or followups; `safety` here is equivalent to `HC_RAG_SAFETY_GATE=false` ([runtime configuration](../configuration/models-and-runtime.md), [safety gate](../safety/gate.md)). Wrappers return unchanged/default typed values; validate uniquely passes the raw plain answer through (`healthcare_rag/orch/tasks.py#L36-L56`, `#L87-L94`, `#L151-L179`). Add a processor only by supplying its Pydantic model, template, composition-root registration, task/wrapper ordering, and an eval case—otherwise it will not participate in the orchestrated runtime.

`generate_answer_stream` is a public async-generator seam, but it is generation-only; its caller must later validate/persist if needed ([architecture](../architecture/overview.md)).

**Focused validation:** change a template/model and run `make eval-smoke`; use `make eval-nojudge PREFIX=processor-change` for stable retrieval/behavior checks, then full judge eval when changing answer/safety behavior.
