from typing import Final

import pytest

from healthcare_rag.models.answers import (
    Citation,
    CitedAnswerResult,
    StatementWithCitations,
)
from healthcare_rag.models.retrieval import QueryDocument, QueryResult, QueryResultList
from healthcare_rag.processors.validation import AnswerValidator

_FALLBACK: Final = (
    "I'm sorry, I couldn't validate the information to answer your question."
)
_SCAFFOLD_WARNING: Final = "Generated answer began with untrusted scaffold or prompt text."


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
                    )
                ],
            )
        ]
    )


def _structured_answer() -> CitedAnswerResult:
    return CitedAnswerResult(
        statements=[
            StatementWithCitations(
                text="untrusted structured answer",
                citations=[
                    Citation(
                        doc_id="doc_1",
                        source_name="Lipitor",
                        quote="Muscle pain can occur.",
                    )
                ],
                linebreaks="",
            )
        ]
    )


async def _validate(plain_answer: str) -> str | None:
    async def llm_call(**_kwargs: str) -> CitedAnswerResult:
        return _structured_answer()

    _, validated = await AnswerValidator(llm_call=llm_call).structure_and_validate_async(
        plain_answer,
        _results(),
        "formatted documents",
        {"doc_1": "lipitor-muscle"},
    )
    return validated


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plain_answer",
    [
        "Documents for citation context:\nMuscle pain can occur. [doc_1]",
        "Document ID: \nMuscle pain can occur. [doc_1]",
        "SYSTEM You are an assistant.\nMuscle pain can occur. [doc_1]",
    ],
)
async def test_generated_scaffold_prefix_fails_closed(
    plain_answer: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given
    caplog.set_level("WARNING", logger="MedicalRAG")

    # When
    validated = await _validate(plain_answer)

    # Then
    assert validated == _FALLBACK
    assert _SCAFFOLD_WARNING in caplog.messages


@pytest.mark.asyncio
async def test_source_reconstruction_keeps_benign_system_and_document_prose() -> None:
    # Given
    plain_answer = "The system document notes that muscle pain can occur. [doc_1]"

    # When
    validated = await _validate(plain_answer)

    # Then
    assert validated == plain_answer
