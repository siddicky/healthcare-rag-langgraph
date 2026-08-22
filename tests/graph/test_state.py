import operator
from typing import Annotated, TypedDict

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.channels.untracked_value import UntrackedValue
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Overwrite

from healthcare_rag.graph.state import (
    GraphInput,
    GraphOutput,
    RAGState,
    dump_results,
    load_results,
)
from healthcare_rag.models.retrieval import QueryDocument, QueryResult, QueryResultList


class ReducerState(TypedDict, total=False):
    retrievals: Annotated[list, operator.add]
    route: Annotated[list[str], operator.add]
    branch_events: Annotated[list[dict], operator.add]
    messages: Annotated[list, add_messages]


def test_overwrite_resets_append_only_channels_while_messages_accumulate() -> None:
    # Given
    def append_node(_state: ReducerState) -> ReducerState:
        return {
            "retrievals": [{"results": []}],
            "route": ["retrieve"],
            "branch_events": [{"branch": "initial"}],
        }

    def reset_node(_state: ReducerState) -> ReducerState:
        return {
            "retrievals": Overwrite([]),
            "route": Overwrite([]),
            "branch_events": Overwrite([]),
            "messages": [AIMessage(content="answer")],
        }

    builder = StateGraph(ReducerState)
    builder.add_node("append", append_node)
    builder.add_node("reset", reset_node)
    builder.add_edge(START, "append")
    builder.add_edge("append", "reset")
    builder.add_edge("reset", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "overwrite-thread"}}

    # When
    graph.invoke({"messages": [HumanMessage(content="one")]}, config)
    result = graph.invoke({"messages": [HumanMessage(content="two")]}, config)

    # Then
    assert result["retrievals"] == []
    assert result["route"] == []
    assert result["branch_events"] == []
    assert [message.content for message in result["messages"]] == [
        "one",
        "answer",
        "two",
        "answer",
    ]


class RawQuestionState(TypedDict, total=False):
    question: Annotated[str, UntrackedValue(str)]
    first_seen: str


class PlannedFailure(RuntimeError):
    pass


def _raw_question_graph(seen: list[str], *, raises: bool = False):
    def first(state: RawQuestionState) -> RawQuestionState:
        seen.append(state["question"])
        return {"first_seen": "read"}

    def second(state: RawQuestionState) -> RawQuestionState:
        seen.append(state["question"])
        if raises:
            raise PlannedFailure("planned failure")
        return {}

    builder = StateGraph(RawQuestionState)
    builder.add_node("first", first)
    builder.add_node("second", second)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)
    return builder.compile(checkpointer=InMemorySaver())


def test_untracked_question_is_run_local_and_absent_from_checkpoints() -> None:
    # Given
    seen: list[str] = []
    graph = _raw_question_graph(seen)
    config = {"configurable": {"thread_id": "raw-thread"}}

    # When
    graph.invoke({"question": "first raw question"}, config)
    graph.invoke({"question": "second raw question"}, config)

    # Then
    assert seen == [
        "first raw question",
        "first raw question",
        "second raw question",
        "second raw question",
    ]
    assert "question" not in graph.get_state(config).values
    assert all(
        "first raw question" not in repr(snapshot.values)
        and "second raw question" not in repr(snapshot.values)
        for snapshot in graph.get_state_history(config)
    )


def test_untracked_question_is_absent_from_failed_run_checkpoints() -> None:
    # Given
    seen: list[str] = []
    graph = _raw_question_graph(seen, raises=True)
    config = {"configurable": {"thread_id": "failed-raw-thread"}}

    # When
    with pytest.raises(PlannedFailure, match="planned failure"):
        graph.invoke({"question": "failed raw question"}, config)

    # Then
    assert seen == ["failed raw question", "failed raw question"]
    assert all(
        "failed raw question" not in repr(snapshot.values)
        for snapshot in graph.get_state_history(config)
    )


def test_explicit_io_schemas_exclude_raw_question_from_result() -> None:
    # Given
    def answer_node(state: RAGState) -> RAGState:
        return {
            "question": state["question"],
            "answer": "safe answer",
            "follow_ups": [],
        }

    builder = StateGraph(RAGState, input=GraphInput, output=GraphOutput)
    builder.add_node("answer", answer_node)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    graph = builder.compile()

    # When
    result = graph.invoke({"question": "raw", "user_id": "user"})

    # Then
    assert result == {"answer": "safe answer", "follow_ups": []}


def test_query_results_serialization_round_trip_is_stable() -> None:
    # Given
    results = QueryResultList(
        results=[
            QueryResult(
                source="Lipitor",
                query="What are common adverse effects?",
                docs=[
                    QueryDocument(
                        content="A monograph excerpt.",
                        score=0.91,
                        doc_id="lipitor-42",
                        source_name="Lipitor",
                        metadata={"id_": "lipitor-42", "section": "adverse effects"},
                        page_numbers=[12, 13],
                    )
                ],
            )
        ]
    )

    # When
    dumped = dump_results(results)
    round_tripped = dump_results(load_results(dumped))

    # Then
    assert round_tripped == dumped
