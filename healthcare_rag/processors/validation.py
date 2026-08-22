import logging
import time
from collections.abc import Awaitable, Callable
from typing import assert_never

from ..models.answers import CitedAnswerResult
from ..models.retrieval import QueryResultList
from ..services.models import default_llm_model
from .validation_citations import (
    CitationValidationContext,
    resolve_citation_ids,
    validate_citations_and_build_answer,
)
from .validation_rendering import FALLBACK_MESSAGE
from .validation_source import (
    SourceCitations,
    UnsafeSourceScaffold,
    find_source_citations,
    reconstruct_source_answer,
)

logger = logging.getLogger("MedicalRAG")

ValidatorLLMCall = Callable[..., Awaitable[CitedAnswerResult | None]]


class AnswerValidator:
    """Structures generated answers and verifies their document citations."""

    def __init__(
        self,
        gateway: ValidatorLLMCall | None = None,
        temperature: float = 0.0,
        llm_call: ValidatorLLMCall | None = None,
        *,
        llm_model: str | None = None,
    ) -> None:
        self.gateway: ValidatorLLMCall | None = gateway
        self.temperature: float = temperature
        self.llm_model: str = llm_model or default_llm_model()
        self._llm_call: ValidatorLLMCall | None = llm_call or gateway

    async def structure_and_validate_async(
        self,
        plain_answer: str,
        retrieval_results: QueryResultList,
        formatted_docs: str,
        prompt_id_map: dict[str, str],
        quote_match_threshold: int = 85,
    ) -> tuple[CitedAnswerResult | None, str | None]:
        started_at = time.time()
        result = await self._structure_and_validate(
            plain_answer,
            retrieval_results,
            formatted_docs,
            prompt_id_map,
            quote_match_threshold,
        )
        elapsed = time.time() - started_at
        logger.info(f"structure_and_validate_async completed in {elapsed:.2f}s")
        return result

    async def _structure_and_validate(
        self,
        plain_answer: str,
        retrieval_results: QueryResultList,
        formatted_docs: str,
        prompt_id_map: dict[str, str],
        quote_match_threshold: int,
    ) -> tuple[CitedAnswerResult | None, str | None]:
        if (
            not plain_answer
            or not retrieval_results
            or not retrieval_results.results
            or not formatted_docs
            or not prompt_id_map
        ):
            logger.warning("Missing required inputs for structuring and validation.")
            return None, None

        logger.info("Attempting to structure the plain text answer.")
        if self._llm_call is None:
            logger.error("AnswerValidator has no LLM call configured")
            return None, None
        structured_answer = await self._llm_call(
            prompt_name="answer_structuring",
            answer=plain_answer,
            retrieval_results=formatted_docs,
            temperature=0.0,
            response_format=CitedAnswerResult,
            default_response=None,
        )
        if structured_answer is None:
            logger.error("Failed to structure the answer using LLM.")
            return None, None

        logger.info("Answer structured successfully. Proceeding to validation.")
        source_citations = find_source_citations(plain_answer)
        match source_citations:
            case UnsafeSourceScaffold():
                logger.warning(
                    "Generated answer began with untrusted scaffold or prompt text."
                )
                return structured_answer, FALLBACK_MESSAGE
            case None:
                logger.warning("Generated answer contained no citation markers.")
                return structured_answer, FALLBACK_MESSAGE
            case SourceCitations():
                pass
            case _:
                assert_never(source_citations)

        source_answer = reconstruct_source_answer(
            plain_answer,
            structured_answer,
            source_citations,
        )
        if source_answer is None:
            return structured_answer, FALLBACK_MESSAGE

        resolved_answer = resolve_citation_ids(source_answer, prompt_id_map)
        context = CitationValidationContext(
            retrieval_results=retrieval_results,
            original_id_to_prompt_id={
                original_id: prompt_id
                for prompt_id, original_id in prompt_id_map.items()
            },
            quote_match_threshold=quote_match_threshold,
        )
        validated_answer = validate_citations_and_build_answer(
            resolved_answer,
            context,
        )
        logger.info("Validation and final answer string construction complete.")
        return resolved_answer, validated_answer
