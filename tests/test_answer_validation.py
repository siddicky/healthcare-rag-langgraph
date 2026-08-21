import pytest

from healthcare_rag.models.answers import (
    Citation,
    CitedAnswerResult,
    StatementWithCitations,
)
from healthcare_rag.models.retrieval import QueryDocument, QueryResult, QueryResultList
from healthcare_rag.processors.validation import AnswerValidator


def _results() -> QueryResultList:
    return QueryResultList(
        results=[
            QueryResult(
                source="Lipitor",
                query="What are Lipitor side effects?",
                docs=[
                    QueryDocument(
                        content="Muscle pain can occur.",
                        score=0.9,
                        doc_id="lipitor-muscle",
                        source_name="Lipitor",
                        metadata={},
                        page_numbers=[1],
                    ),
                    QueryDocument(
                        content="Nausea can occur.",
                        score=0.8,
                        doc_id="lipitor-nausea",
                        source_name="Lipitor",
                        metadata={},
                        page_numbers=[2],
                    ),
                ],
            )
        ]
    )


def _citation(doc_id: str, quote: str) -> Citation:
    return Citation(doc_id=doc_id, source_name="Lipitor", quote=quote)


async def _validate(
    plain_answer: str,
    structured_answer: CitedAnswerResult,
) -> str | None:
    async def llm_call(**_kwargs: str) -> CitedAnswerResult:
        return structured_answer

    validator = AnswerValidator(llm_call=llm_call)
    _, validated = await validator.structure_and_validate_async(
        plain_answer,
        _results(),
        "formatted documents",
        {
            "doc_1": "lipitor-muscle",
            "doc_2": "lipitor-nausea",
        },
    )
    return validated


@pytest.mark.asyncio
async def test_valid_cited_answer_is_reconstructed() -> None:
    # Given
    structured = CitedAnswerResult(
        statements=[
            StatementWithCitations(
                text="Muscle pain can occur.",
                citations=[_citation("doc_1", "Muscle pain can occur.")],
                linebreaks="",
            )
        ]
    )

    # When
    validated = await _validate("Muscle pain can occur. [doc_1]", structured)

    # Then
    assert validated == "Muscle pain can occur. [doc_1]"


@pytest.mark.asyncio
async def test_cited_list_keeps_bullets_at_their_line_boundaries() -> None:
    # Given
    structured = CitedAnswerResult(
        statements=[
            StatementWithCitations(
                text="- Muscle pain can occur.",
                citations=[_citation("doc_1", "Muscle pain can occur.")],
                linebreaks="\\n",
            ),
            StatementWithCitations(
                text="- Nausea can occur.",
                citations=[_citation("doc_2", "Nausea can occur.")],
                linebreaks="",
            ),
        ]
    )

    # When
    validated = await _validate(
        "- Muscle pain can occur. [doc_1]\n- Nausea can occur. [doc_2]",
        structured,
    )

    # Then
    assert validated == (
        "- Muscle pain can occur. [doc_1]\n- Nausea can occur. [doc_2]"
    )


@pytest.mark.asyncio
async def test_citation_before_sentence_punctuation_stays_with_its_statement() -> None:
    # Given
    structured = CitedAnswerResult(
        statements=[
            StatementWithCitations(
                text="Muscle pain can occur.",
                citations=[_citation("doc_1", "Muscle pain can occur.")],
                linebreaks="",
            )
        ]
    )

    # When
    validated = await _validate("Muscle pain can occur [doc_1].", structured)

    # Then
    assert validated == "Muscle pain can occur. [doc_1]"


@pytest.mark.asyncio
async def test_scaffolding_in_structured_text_is_not_used_for_display() -> None:
    # Given
    structured = CitedAnswerResult(
        statements=[
            StatementWithCitations(
                text=(
                    "Muscle pain can occur. [doc_1]\n"
                    "Documents for citation context:\n|\n"
                    "Document ID: [doc_2]\nContent: Nausea can occur."
                ),
                citations=[_citation("doc_1", "Muscle pain can occur.")],
                linebreaks="",
            )
        ]
    )

    # When
    validated = await _validate("Muscle pain can occur. [doc_1]", structured)

    # Then
    assert validated == "Muscle pain can occur. [doc_1]"


@pytest.mark.asyncio
async def test_missing_citation_structure_fails_closed() -> None:
    # Given
    structured = CitedAnswerResult(
        statements=[
            StatementWithCitations(
                text="Muscle pain can occur. Nausea can occur.",
                citations=[_citation("doc_1", "Muscle pain can occur.")],
                linebreaks="",
            )
        ]
    )

    # When
    validated = await _validate(
        "Muscle pain can occur. [doc_1] Nausea can occur. [doc_2]",
        structured,
    )

    # Then
    assert validated == (
        "I'm sorry, I couldn't validate the information to answer your question."
    )


@pytest.mark.asyncio
async def test_duplicate_and_misordered_citations_are_canonicalized() -> None:
    # Given
    structured = CitedAnswerResult(
        statements=[
            StatementWithCitations(
                text="untrusted",
                citations=[
                    _citation("doc_2", "Nausea can occur."),
                    _citation("doc_1", "Muscle pain can occur."),
                    _citation("doc_1", "Muscle pain can occur."),
                ],
                linebreaks="",
            )
        ]
    )

    # When
    validated = await _validate(
        "Muscle pain can occur. [doc_1]\nNausea can occur. [doc_2]",
        structured,
    )

    # Then
    assert validated == (
        "Muscle pain can occur. [doc_1]\nNausea can occur. [doc_2]"
    )


@pytest.mark.asyncio
async def test_wholly_uncited_answer_fails_closed() -> None:
    # Given
    structured = CitedAnswerResult(
        statements=[
            StatementWithCitations(
                text="Unsupported dosage advice.", citations=[], linebreaks=""
            )
        ]
    )

    # When
    validated = await _validate("Unsupported dosage advice.", structured)

    # Then
    assert validated == (
        "I'm sorry, I couldn't validate the information to answer your question."
    )


@pytest.mark.asyncio
async def test_unsupported_uncited_tail_is_not_returned() -> None:
    # Given
    structured = CitedAnswerResult(
        statements=[
            StatementWithCitations(
                text="Muscle pain can occur. Take 100 mg daily.",
                citations=[_citation("doc_1", "Muscle pain can occur.")],
                linebreaks="",
            )
        ]
    )

    # When
    validated = await _validate(
        "Muscle pain can occur. [doc_1]\nTake 100 mg daily.", structured
    )

    # Then
    assert validated == "Muscle pain can occur. [doc_1]"
