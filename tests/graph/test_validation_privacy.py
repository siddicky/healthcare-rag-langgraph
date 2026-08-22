from __future__ import annotations

from typing import cast

from healthcare_rag.graph.nodes.generate import validate_answer
from healthcare_rag.graph.state import dump_results
from healthcare_rag.models.answers import Citation, CitedAnswerResult, StatementWithCitations
from healthcare_rag.models.retrieval import QueryDocument, QueryResult, QueryResultList

from .conftest import FakeGateway, ResourceInstaller


async def test_trusted_quote_is_validated_before_state_copy_is_sanitized(
    install_resources: ResourceInstaller,
) -> None:
    trusted_quote = "Calling toll-free at 1-866-234-2345."
    results = QueryResultList(
        results=[
            QueryResult(
                source="Lipitor",
                query="What is the contact information?",
                docs=[
                    QueryDocument(
                        content=trusted_quote,
                        score=0.9,
                        doc_id="doc-1",
                        source_name="Lipitor",
                        metadata={"section": "test"},
                        page_numbers=[1],
                    )
                ],
            )
        ]
    )
    structured = CitedAnswerResult(
        statements=[
            StatementWithCitations(
                text="Contact information is available.",
                citations=[
                    Citation(
                        doc_id="doc_1",
                        source_name="Lipitor",
                        quote=trusted_quote,
                    )
                ],
                linebreaks="",
            )
        ]
    )
    gateway = FakeGateway(structured_results={"validate_answer": structured})
    _ = install_resources(gateway)

    validated = cast(
        dict[str, object],
        await validate_answer(
            {
                "merged": dump_results(results),
                "generation": {
                    "plain_answer": "Contact information is available [doc_1].",
                    "formatted_docs": trusted_quote,
                    "prompt_id_map": {"doc_1": "doc-1"},
                },
            }
        ),
    )

    assert validated["validated"] == "Contact information is available. [doc_1]"
    assert "1-866-234-2345" not in repr(validated["structured"])
