from __future__ import annotations

import logging
from typing import Any

from healthcare_rag.graph.history import render_followup_history
from healthcare_rag.graph.nodes import render_display_answer
from healthcare_rag.graph.resources import get
from healthcare_rag.graph.state import load_results
from healthcare_rag.models.answers import CitedAnswerResult
from healthcare_rag.models.misc import FollowUpQuestions
from healthcare_rag.processors.generation import format_documents_for_prompt
from healthcare_rag.processors.safety import ascrub_phi
from healthcare_rag.processors.validation import AnswerValidator

logger = logging.getLogger("MedicalRAG")

_UNKNOWN_ANSWER = "I'm sorry, I don't know the answer to that question."


async def generate_answer(state: dict[str, Any]) -> dict[str, Any]:
    merged_data = state.get("merged")
    merged = load_results(merged_data) if merged_data else None
    if merged is None or not any(result.docs for result in merged.results):
        return {
            "generation": {
                "plain_answer": _UNKNOWN_ANSWER,
                "formatted_docs": "",
                "prompt_id_map": {},
            }
        }

    formatted_docs, prompt_id_map = format_documents_for_prompt(merged)
    summary = state.get("summary") or {}
    conversation_context = str(summary.get("relevant_snippets") or "")
    model_answer = await get().gateway.acomplete(
        "generate_answer",
        temperature=0.1,
        conversation_context=conversation_context,
        retrieval_results=formatted_docs,
        user_question=state["working_query"],
    )
    plain_answer = (await ascrub_phi(model_answer))[0]
    return {
        "generation": {
            "plain_answer": plain_answer,
            "formatted_docs": formatted_docs,
            "prompt_id_map": prompt_id_map,
        }
    }


async def validate_answer(state: dict[str, Any]) -> dict[str, Any]:
    resources = get()
    generation = state.get("generation") or {}
    plain_answer = str(generation.get("plain_answer") or "")
    if "validate" in resources.settings.disabled_stages:
        return {"validated": (await ascrub_phi(plain_answer))[0]}

    merged_data = state.get("merged")
    if not merged_data:
        return {"structured": None, "validated": None}

    async def structure_call(
        prompt_name: str,
        temperature: float,
        response_format: type[CitedAnswerResult],
        default_response: CitedAnswerResult | None = None,
        **prompt_args: str,
    ) -> CitedAnswerResult | None:
        del prompt_name
        return await resources.gateway.astructured(
            "validate_answer",
            response_format,
            temperature=temperature,
            default=default_response,
            **prompt_args,
        )

    validator = AnswerValidator(gateway=structure_call, temperature=0.0)
    try:
        structured, validated = await validator.structure_and_validate_async(
            plain_answer,
            load_results(merged_data),
            str(generation.get("formatted_docs") or ""),
            generation.get("prompt_id_map") or {},
            quote_match_threshold=85,
        )
    except Exception:  # noqa: BROAD_EXCEPT_OK - validation must never fail open.
        logger.error("ANSWER_VALIDATION_FAILED")
        structured, validated = None, None
    structured_data = structured.model_dump(mode="json") if structured else None
    if structured_data:
        for statement in structured_data["statements"]:
            statement["text"] = (await ascrub_phi(str(statement.get("text") or "")))[0]
            for citation in statement.get("citations", []):
                for field in ("doc_id", "source_name", "quote"):
                    citation[field] = (
                        await ascrub_phi(str(citation.get(field) or ""))
                    )[0]
    return {
        "structured": structured_data,
        "validated": (await ascrub_phi(validated or ""))[0] or None,
    }


async def generate_follow_ups(state: dict[str, Any]) -> dict[str, Any]:
    resources = get()
    if "followups" in resources.settings.disabled_stages:
        return {"follow_ups": []}
    if not state.get("validated") or not state.get("user_id"):
        return {"follow_ups": []}

    try:
        history_context = render_followup_history(state.get("processed_history", []))
        answer = render_display_answer(
            state["validated"], state.get("safety_notices", [])
        )
        default = FollowUpQuestions(questions=[])
        try:
            response = await resources.gateway.astructured(
                "generate_follow_ups",
                FollowUpQuestions,
                temperature=0.3,
                default=default,
                history_context=history_context,
                original_query=state["scrubbed_question"],
                answer=answer,
            )
        except Exception:  # noqa: BROAD_EXCEPT_OK - gateway failure is a normal empty result.
            logger.warning("FOLLOW_UP_GENERATION_FAILED")
            response = default
        return {
            "follow_ups": [
                (await ascrub_phi(question))[0]
                for question in (response or default).questions
            ]
        }
    except Exception:  # noqa: BROAD_EXCEPT_OK - preserves the legacy unexpected-error sentinel.
        logger.error("FOLLOW_UP_PIPELINE_FAILED")
        return {"follow_ups": ["Error generating follow-ups."]}
