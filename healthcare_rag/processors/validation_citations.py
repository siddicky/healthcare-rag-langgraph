import logging
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from types import ModuleType
from typing import Final, Protocol, TypeGuard, runtime_checkable

from ..models.answers import Citation, CitedAnswerResult, StatementWithCitations
from ..models.retrieval import QueryDocument, QueryResultList
from .validation_rendering import FALLBACK_MESSAGE, format_statement, join_statements

logger = logging.getLogger("MedicalRAG")


@runtime_checkable
class _FuzzyProcess(Protocol):
    def extractOne(
        self,
        query: str,
        choices: Sequence[str],
        *,
        score_cutoff: int = 0,
    ) -> tuple[str, int] | tuple[str, int, int] | None: ...


class InvalidFuzzyProcessError(RuntimeError):
    pass


def _is_fuzzy_process(module: ModuleType) -> TypeGuard[_FuzzyProcess]:
    return isinstance(module, _FuzzyProcess)


_fuzzy_module = import_module("fuzzywuzzy.process")
if not _is_fuzzy_process(_fuzzy_module):
    raise InvalidFuzzyProcessError
_fuzzy_process: Final[_FuzzyProcess] = _fuzzy_module


@dataclass(frozen=True, slots=True)
class CitationValidationContext:
    retrieval_results: QueryResultList
    original_id_to_prompt_id: dict[str, str]
    quote_match_threshold: int


@dataclass(frozen=True, slots=True)
class StatementValidation:
    rendered: str | None
    total_checked: int
    invalid_count: int


def resolve_citation_ids(
    answer: CitedAnswerResult,
    prompt_id_map: dict[str, str],
) -> CitedAnswerResult:
    for statement in answer.statements:
        for citation in statement.citations:
            prompt_id = citation.doc_id
            original_id = prompt_id_map.get(prompt_id)
            if original_id:
                citation.doc_id = original_id
            else:
                logger.warning(
                    f"Could not resolve prompt ID '{prompt_id}' during validation. "
                    + "Citation will likely fail."
                )
    return answer


def validate_citations_and_build_answer(
    structured_answer: CitedAnswerResult,
    context: CitationValidationContext,
) -> str:
    if not structured_answer.statements:
        return FALLBACK_MESSAGE

    results = [
        _process_statement(statement, index, context)
        for index, statement in enumerate(structured_answer.statements)
    ]
    validated_statements = [
        result.rendered for result in results if result.rendered is not None
    ]
    total_checked = sum(result.total_checked for result in results)
    invalid_count = sum(result.invalid_count for result in results)
    logger.info(
        f"Citation check summary: Total checked: {total_checked}, "
        + f"Individual citation failures: {invalid_count}"
    )
    return join_statements(validated_statements)


def _process_statement(
    statement: StatementWithCitations,
    statement_index: int,
    context: CitationValidationContext,
) -> StatementValidation:
    if not statement.citations:
        logger.debug(
            f"Statement {statement_index} has no citations, considered valid."
        )
        return StatementValidation(
            rendered=format_statement(statement.text, [], statement.linebreaks),
            total_checked=0,
            invalid_count=0,
        )

    valid_prompt_ids: list[str] = []
    for citation_index, citation in enumerate(statement.citations):
        prompt_id = _validate_citation(
            citation,
            CitationLocation(statement_index, citation_index),
            context,
        )
        if prompt_id:
            valid_prompt_ids.append(prompt_id)

    if valid_prompt_ids:
        return StatementValidation(
            rendered=format_statement(
                statement.text,
                valid_prompt_ids,
                statement.linebreaks,
            ),
            total_checked=len(statement.citations),
            invalid_count=len(statement.citations) - len(valid_prompt_ids),
        )
    logger.warning(
        f"Statement {statement_index} is invalid because all citations failed "
        + "validation."
    )
    return StatementValidation(
        rendered=None,
        total_checked=len(statement.citations),
        invalid_count=len(statement.citations),
    )


@dataclass(frozen=True, slots=True)
class CitationLocation:
    statement_index: int
    citation_index: int


def _validate_citation(
    citation: Citation,
    location: CitationLocation,
    context: CitationValidationContext,
) -> str | None:
    original_id = citation.doc_id
    cited_document = _find_document_by_id(original_id, context.retrieval_results)
    if cited_document is None:
        logger.warning(
            f"Validation failed: Document ID '{original_id}' not found for "
            + f"statement {location.statement_index}, citation "
            + f"{location.citation_index}."
        )
        return None

    if not _verify_quote(
        citation.quote,
        cited_document.content,
        context.quote_match_threshold,
    ):
        logger.warning(
            f"Validation failed: Quote not found in doc ID '{original_id}' for "
            + f"statement {location.statement_index}, citation "
            + f"{location.citation_index}. Quote: '{citation.quote[:100]}...'"
        )
        return None

    prompt_id = context.original_id_to_prompt_id.get(original_id)
    if not prompt_id:
        logger.error(
            f"Consistency Error: Validated original ID '{original_id}' not found "
            + "in original_id_to_prompt_id_map for statement "
            + f"{location.statement_index}."
        )
        return None
    logger.debug(
        f"Statement {location.statement_index}, citation {location.citation_index} "
        + f"(Original ID: {original_id}, Prompt ID: {prompt_id}) validated "
        + "successfully."
    )
    return prompt_id


def _find_document_by_id(
    doc_id: str,
    retrieval_results: QueryResultList,
) -> QueryDocument | None:
    return next(
        (
            document
            for result in retrieval_results.results
            for document in result.docs
            if document.doc_id == doc_id
        ),
        None,
    )


def _verify_quote(quote: str, document_content: str, threshold: int) -> bool:
    if not quote or not document_content:
        return False
    if quote in document_content:
        return True
    return (
        _fuzzy_process.extractOne(
            quote,
            [document_content],
            score_cutoff=threshold,
        )
        is not None
    )
