from healthcare_rag.graph.state import dump_results
from healthcare_rag.models.retrieval import QueryDocument, QueryResult, QueryResultList
from healthcare_rag.processors.generation import format_documents_for_prompt
from healthcare_rag.processors.retrieval import union_results


def _document(doc_id: str, source: str, content: str) -> QueryDocument:
    return QueryDocument(
        content=content,
        score=0.9,
        doc_id=doc_id,
        source_name=source,
        metadata={"section": "test"},
        page_numbers=[1],
    )


def test_union_results_deduplicates_first_occurrence_and_groups_by_source() -> None:
    # Given
    first = QueryResultList(
        results=[
            QueryResult(
                source="Lipitor",
                query="first query",
                docs=[
                    _document("shared", "Lipitor", "first occurrence"),
                    _document("lipitor-only", "Lipitor", "lipitor content"),
                ],
            )
        ]
    )
    second = QueryResultList(
        results=[
            QueryResult(
                source="Metformin",
                query="second query",
                docs=[
                    _document("metformin-only", "Metformin", "metformin content"),
                    _document("shared", "Metformin", "duplicate occurrence"),
                ],
            )
        ]
    )

    # When
    merged = union_results([first, second])

    # Then
    assert [result.source for result in merged.results] == ["Lipitor", "Metformin"]
    assert [doc.doc_id for doc in merged.results[0].docs] == [
        "shared",
        "lipitor-only",
    ]
    assert [doc.content for doc in merged.results[0].docs] == [
        "first occurrence",
        "lipitor content",
    ]
    assert [doc.doc_id for doc in merged.results[1].docs] == ["metformin-only"]


def test_union_results_returns_empty_for_none() -> None:
    # Given / When
    merged = union_results(None)

    # Then
    assert merged == QueryResultList(results=[])


def test_union_results_returns_empty_for_empty_lists() -> None:
    # Given
    empty = QueryResultList(results=[])

    # When
    merged = union_results([empty, empty])

    # Then
    assert merged == QueryResultList(results=[])


def test_format_documents_for_prompt_matches_object_and_state_dict() -> None:
    # Given
    results = QueryResultList(
        results=[
            QueryResult(
                source="Lipitor",
                query="What is Lipitor?",
                docs=[_document("lipitor-1", "Lipitor", "Lipitor content")],
            )
        ]
    )

    # When
    from_object = format_documents_for_prompt(results)
    from_state = format_documents_for_prompt(dump_results(results))

    # Then
    assert from_state == from_object
