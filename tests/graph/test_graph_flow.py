# noqa: SIZE_OK - the requested node-scope acceptance matrix is intentionally co-located.
from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

import pytest
from langchain_core.messages import ToolCall
from langgraph.types import Command
from weaviate.exceptions import WeaviateBaseError

from healthcare_rag.graph.history import ProcessedHistoryEntry, render_followup_history
from healthcare_rag.graph.nodes import render_display_answer
from healthcare_rag.graph.nodes.evaluate import evaluate_retrieval
from healthcare_rag.graph.nodes.generate import (
    generate_answer,
    generate_follow_ups,
    validate_answer,
)
from healthcare_rag.graph.nodes.retrieve import merge_retrievals, retrieve_documents
from healthcare_rag.graph.state import RAGState, RetrieveInput, dump_results
from healthcare_rag.models.answers import (
    Citation,
    CitedAnswerResult,
    StatementWithCitations,
)
from healthcare_rag.models.misc import FollowUpQuestions
from healthcare_rag.models.queries import ClarifiedQuery, DecomposedQuery, RetrievalEvaluation
from healthcare_rag.models.retrieval import QueryDocument, QueryResult, QueryResultList

from .conftest import FakeGateway, FakeRetriever, ResourceInstaller


class UnexpectedFollowupError(Exception):
    pass


def _result(
    query: str,
    *doc_ids: str,
    source: str = "Lipitor",
) -> QueryResultList:
    return QueryResultList(
        results=[
            QueryResult(
                source=source,
                query=query,
                docs=[
                    QueryDocument(
                        content=f"content for {doc_id}",
                        score=0.9,
                        doc_id=doc_id,
                        source_name=source,
                        metadata={"section": "test"},
                        page_numbers=[1],
                    )
                    for doc_id in doc_ids
                ],
            )
        ]
    )


def _envelope(
    query: str,
    *doc_ids: str,
    kind: str = "initial",
    index: int = 0,
    phase: int = 0,
    branch: str | None = None,
) -> dict[str, Any]:
    return {
        "phase": phase,
        "kind": kind,
        "index": index,
        "branch": branch or kind,
        "results": dump_results(_result(query, *doc_ids)),
    }


def _tool_call(collection: str, query: str) -> ToolCall:
    return {
        "name": f"query_{collection.lower()}",
        "args": {"query": query},
        "id": f"call-{collection}",
        "type": "tool_call",
    }


def _merged_state(query: str = "What is Lipitor?") -> dict[str, Any]:
    return {
        "working_query": query,
        "scrubbed_question": query,
        "merged": dump_results(_result(query, "doc-1")),
        "summary": {"relevant_snippets": "prior context"},
        "gap_round": 0,
        "safety_notices": [],
        "processed_history": [],
        "user_id": "user-1",
    }


def _valid_structured_answer() -> CitedAnswerResult:
    return CitedAnswerResult(
        statements=[
            StatementWithCitations(
                text="Lipitor information.",
                citations=[
                    Citation(
                        doc_id="doc_1",
                        source_name="Lipitor",
                        quote="content for doc-1",
                    )
                ],
                linebreaks="",
            )
        ]
    )


@pytest.mark.asyncio
async def test_parent_and_three_subqueries_produce_one_answer_pipeline(
    install_resources: ResourceInstaller,
) -> None:
    gateway = FakeGateway(
        structured_results={
            "evaluate_retrieval": RetrievalEvaluation(
                is_sufficient=True,
                missing_information=None,
                additional_queries=None,
            ),
            "validate_answer": _valid_structured_answer(),
        },
        completion_results={"generate_answer": "Lipitor information [doc_1]."},
    )
    install_resources(gateway)
    state = {
        "working_query": "parent",
        "selected_branch_type": "initial",
        "retrievals": [
            _envelope("parent", "doc-1", kind="initial"),
            _envelope("sub-1", "doc-1", "one", kind="decomposed", index=1, branch="decomposed_0"),
            _envelope("sub-2", "two", kind="decomposed", index=2, branch="decomposed_1"),
            _envelope("sub-3", "three", kind="decomposed", index=3, branch="decomposed_2"),
        ],
    }

    merged = (await merge_retrievals(state)).update
    pipeline_state = {**_merged_state("parent"), **merged}
    evaluated = (await evaluate_retrieval(pipeline_state)).update
    generated = await generate_answer({**pipeline_state, **evaluated})
    validated = await validate_answer({**pipeline_state, **evaluated, **generated})

    assert validated["validated"] == "Lipitor information. [doc_1]"
    assert [call["stage"] for call in gateway.calls if "stage" in call] == [
        "evaluate_retrieval",
        "generate_answer",
        "validate_answer",
    ]


@pytest.mark.asyncio
async def test_random_completion_order_merges_documents_and_events_deterministically() -> None:
    expected_merged: dict[str, Any] | None = None
    expected_events: list[dict[str, Any]] | None = None
    envelopes = [
        _envelope("parent", "shared", "parent", kind="initial", index=0),
        _envelope("sub-1", "shared", "one", kind="decomposed", index=1, branch="decomposed_0"),
        _envelope("sub-2", "two", kind="decomposed", index=2, branch="decomposed_1"),
    ]
    events = [
        {"phase": envelope["phase"], "kind": "retrieve", "index": envelope["index"], "branch": envelope["branch"], "status": "COMPLETED"}
        for envelope in envelopes
    ]

    for seed in range(20):
        randomizer = random.Random(seed)
        shuffled_envelopes = randomizer.sample(envelopes, len(envelopes))
        shuffled_events = randomizer.sample(events, len(events))
        output = (
            await merge_retrievals(
                {
                    "working_query": "parent",
                    "selected_branch_type": "initial",
                    "retrievals": shuffled_envelopes,
                }
            )
        ).update
        ordered_events = sorted(
            [*shuffled_events, *output["branch_events"]],
            key=lambda event: (
                event["phase"],
                {"clarify": 0, "retrieve": 1, "merge": 2}[event["kind"]],
                event["index"],
            ),
        )
        expected_merged = output["merged"] if expected_merged is None else expected_merged
        expected_events = ordered_events if expected_events is None else expected_events
        assert output["merged"] == expected_merged
        assert ordered_events == expected_events


@pytest.mark.asyncio
async def test_five_proposed_subqueries_are_capped_to_parent_plus_three_retrievals(
    install_resources: ResourceInstaller,
) -> None:
    from healthcare_rag.graph.nodes.preprocess import decompose_query

    gateway = FakeGateway(
        structured_results={
            "decompose_query": DecomposedQuery(
                original_query="parent",
                query_complexity="complex",
                decomposed_query=[f"sub-{index}" for index in range(5)],
            )
        },
        tool_calls=[_tool_call("Lipitor", "routed")],
    )
    retriever = FakeRetriever(results={"Lipitor": _result("routed", "doc")})
    install_resources(gateway, retriever=retriever)
    decomposition = (await decompose_query({"working_query": "parent"})).update
    sub_queries = decomposition.get("sub_queries", [])
    inputs: list[RetrieveInput] = [
        {"query": "parent", "kind": "initial", "index": 0, "phase": 0, "branch": "initial"},
        *[
            {"query": query, "kind": "decomposed", "index": index, "phase": 0, "branch": f"decomposed_{index - 1}"}
            for index, query in enumerate(sub_queries, start=1)
        ],
    ]

    outputs = [await retrieve_documents(item) for item in inputs]

    assert len(outputs) == 4
    assert len(retriever.calls) == 4
    assert sub_queries == ["sub-0", "sub-1", "sub-2"]


@pytest.mark.asyncio
async def test_simple_query_retrieves_once_and_emits_only_initial_event(
    install_resources: ResourceInstaller,
) -> None:
    gateway = FakeGateway(tool_calls=[_tool_call("Lipitor", "simple")])
    retriever = FakeRetriever(results={"Lipitor": _result("simple", "doc")})
    install_resources(gateway, retriever=retriever)

    output = await retrieve_documents(
        {"query": "simple", "kind": "initial", "index": 0, "phase": 0, "branch": "initial"}
    )

    assert retriever.calls == [("Lipitor", "simple")]
    assert output["branch_events"] == [
        {"phase": 0, "kind": "retrieve", "index": 0, "branch": "initial", "status": "COMPLETED"}
    ]


@pytest.mark.asyncio
async def test_gap_fill_retrieval_emits_no_branch_event(
    install_resources: ResourceInstaller,
) -> None:
    gateway = FakeGateway(tool_calls=[_tool_call("Lipitor", "gap")])
    retriever = FakeRetriever(results={"Lipitor": _result("gap", "doc")})
    install_resources(gateway, retriever=retriever)

    output = await retrieve_documents(
        {"query": "gap", "kind": "gap_fill", "index": 0, "phase": 1, "branch": "gap_fill"}
    )

    assert "branch_events" not in output
    assert output["route"] == ["retrieve:gap_fill:0:1"]


@pytest.mark.asyncio
async def test_insufficient_evaluation_with_queries_opens_one_gap_round(
    install_resources: ResourceInstaller,
) -> None:
    gateway = FakeGateway(
        structured_results={
            "evaluate_retrieval": RetrievalEvaluation(
                is_sufficient=False,
                missing_information="details",
                additional_queries=["gap-1", "gap-2"],
            )
        }
    )
    install_resources(gateway)

    output = (await evaluate_retrieval(_merged_state())).update

    assert output["gap_pending"] is True
    assert output["gap_round"] == 1
    assert output["evaluation"]["additional_queries"] == ["gap-1", "gap-2"]


@pytest.mark.asyncio
async def test_empty_retrieval_never_validates_or_writes_messages(
    install_resources: ResourceInstaller,
) -> None:
    gateway = FakeGateway()
    install_resources(gateway)
    merged = (await merge_retrievals(
        {
            "working_query": "unknown",
            "selected_branch_type": "initial",
            "retrievals": [_envelope("unknown", kind="initial")],
        }
    )).update
    state = {**_merged_state("unknown"), **merged}

    generated = await generate_answer(state)
    validated = await validate_answer({**state, **generated})

    assert generated["generation"]["plain_answer"] == "I'm sorry, I don't know the answer to that question."
    assert validated["validated"] is None
    assert "messages" not in generated | validated
    assert merged["branch_events"][0]["status"] == "FAILED"
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_one_routing_failure_does_not_discard_other_retrievals(
    install_resources: ResourceInstaller,
) -> None:
    failed_gateway = FakeGateway(route_error=RuntimeError("routing failed"))
    install_resources(failed_gateway)
    failed = await retrieve_documents(
        {"query": "bad", "kind": "decomposed", "index": 1, "phase": 0, "branch": "decomposed_0"}
    )
    good_gateway = FakeGateway(tool_calls=[_tool_call("Lipitor", "good")])
    retriever = FakeRetriever(results={"Lipitor": _result("good", "good-doc")})
    install_resources(good_gateway, retriever=retriever)
    good = await retrieve_documents(
        {"query": "good", "kind": "initial", "index": 0, "phase": 0, "branch": "initial"}
    )

    merged = (await merge_retrievals(
        {
            "working_query": "good",
            "selected_branch_type": "initial",
            "retrievals": [*failed["retrievals"], *good["retrievals"]],
        }
    )).update

    assert failed["branch_events"][0]["status"] == "FAILED"
    assert merged["merged"] == dump_results(_result("good", "good-doc"))


@pytest.mark.asyncio
async def test_weaviate_errors_retry_three_times_then_fail_soft(
    install_resources: ResourceInstaller, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = FakeGateway(tool_calls=[_tool_call("Lipitor", "retry")])
    retriever = FakeRetriever(
        error_factory=lambda: WeaviateBaseError("temporary failure")
    )
    install_resources(gateway, retriever=retriever)

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("healthcare_rag.graph.nodes.retrieve.anyio.sleep", no_sleep)
    output = await retrieve_documents(
        {"query": "retry", "kind": "initial", "index": 0, "phase": 0, "branch": "initial"}
    )

    assert len(retriever.calls) == 3
    assert output["retrievals"][0]["results"] == {"results": []}
    assert output["branch_events"][0]["status"] == "FAILED"


@pytest.mark.asyncio
async def test_context_extraction_failure_keeps_default_summary_and_generation_continues(
    install_resources: ResourceInstaller,
) -> None:
    from healthcare_rag.graph.nodes.preprocess import extract_conversation_context

    gateway = FakeGateway(
        completion_results={"generate_answer": "answer"},
        structured_results={"extract_conversation_context": None},
    )
    install_resources(gateway)
    context = await extract_conversation_context(
        {
            "working_query": "question",
            "processed_history": [
                {"timestamp": None, "user_query": "prior", "answer": "answer"}
            ],
        }
    )

    generated = await generate_answer({**_merged_state("question"), **context})

    summary = context.get("summary")
    assert summary is not None
    assert summary["required_context"] is False
    assert generated["generation"]["plain_answer"] == "answer"
    assert gateway.calls[0]["stage"] == "extract_conversation_context"


@pytest.mark.asyncio
async def test_clarification_updates_working_query_and_emits_clarified_event(
    install_resources: ResourceInstaller,
) -> None:
    from healthcare_rag.graph.nodes.preprocess import clarify_query

    gateway = FakeGateway(
        structured_results={
            "clarify_query": ClarifiedQuery(
                original_query="What about it?",
                ambiguity_level="high ambiguity",
                clarified_query="What are Lipitor side effects?",
            )
        }
    )
    install_resources(gateway)

    output = await clarify_query(
        {"working_query": "What about it?", "history_context": "Lipitor context"}
    )

    assert output.get("working_query") == "What are Lipitor side effects?"
    assert output.get("branch_events") == [
        {"phase": 0, "kind": "clarify", "index": 0, "branch": "clarified", "status": "COMPLETED"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("additional_queries", [None, []])
async def test_insufficient_evaluation_without_queries_has_no_gap_dead_end(
    install_resources: ResourceInstaller, additional_queries: list[str] | None
) -> None:
    gateway = FakeGateway(
        structured_results={
            "evaluate_retrieval": RetrievalEvaluation(
                is_sufficient=False,
                missing_information=None,
                additional_queries=additional_queries,
            )
        }
    )
    install_resources(gateway)

    output = (await evaluate_retrieval(_merged_state())).update

    assert output["gap_pending"] is False
    assert output["gap_round"] == 0


@pytest.mark.asyncio
async def test_validation_structuring_failure_does_not_fail_open(
    install_resources: ResourceInstaller,
) -> None:
    gateway = FakeGateway(structured_results={"validate_answer": None})
    install_resources(gateway)
    state = _merged_state()
    generated = await generate_answer(state)
    generated["generation"]["plain_answer"] = "unsupported answer"

    output = await validate_answer({**state, **generated})

    assert output["validated"] is None
    assert output["structured"] is None
    assert "answer" not in output


@pytest.mark.asyncio
async def test_followup_gateway_failure_returns_empty_list(
    install_resources: ResourceInstaller,
) -> None:
    gateway = FakeGateway(structured_results={"generate_follow_ups": None})
    install_resources(gateway)

    output = await generate_follow_ups({**_merged_state(), "validated": "answer"})

    assert output == {"follow_ups": []}


@pytest.mark.asyncio
async def test_unexpected_followup_failure_returns_legacy_error_sentinel(
    install_resources: ResourceInstaller, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = FakeGateway()
    install_resources(gateway)

    def raise_unexpected(_history: Any) -> str:
        raise UnexpectedFollowupError

    monkeypatch.setattr(
        "healthcare_rag.graph.nodes.generate.render_followup_history",
        raise_unexpected,
    )

    output = await generate_follow_ups({**_merged_state(), "validated": "answer"})

    assert output == {"follow_ups": ["Error generating follow-ups."]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "node", "state", "expected"),
    [
        ("evaluate", evaluate_retrieval, _merged_state(), {"evaluation": {"is_sufficient": True}, "gap_pending": False}),
        ("validate", validate_answer, {**_merged_state(), "generation": {"plain_answer": "raw", "formatted_docs": "docs", "prompt_id_map": {"doc_1": "doc-1"}}}, {"validated": "raw"}),
        ("followups", generate_follow_ups, {**_merged_state(), "validated": "answer"}, {"follow_ups": []}),
    ],
)
async def test_owned_disabled_stages_are_pass_through_without_llm_calls(
    install_resources: ResourceInstaller,
    stage: str,
    node: Callable[..., Any],
    state: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    gateway = FakeGateway()
    install_resources(gateway, disabled=(stage,))

    result = await node(state)
    # evaluate_retrieval routes itself, so its update arrives inside a Command.
    output = result.update if isinstance(result, Command) else result

    assert output.items() >= expected.items()
    assert gateway.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["clarify", "decompose"])
async def test_preprocess_disabled_stages_are_pass_through_without_llm_calls(
    install_resources: ResourceInstaller,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    from healthcare_rag.graph.nodes.preprocess import clarify_query, decompose_query

    gateway = FakeGateway()
    install_resources(gateway, disabled=(stage,))
    monkeypatch.setenv("HC_RAG_DISABLE_STAGES", stage)
    state: RAGState = {
        "working_query": "question",
        "history_context": "history",
    }

    if stage == "clarify":
        assert await clarify_query(state) == {"clarified": None}
    else:
        # decompose_query routes itself, so its update travels inside a Command.
        assert (await decompose_query(state)).update.get("decomposed") is False
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_followups_receive_newest_five_history_original_query_and_display_answer(
    install_resources: ResourceInstaller,
) -> None:
    gateway = FakeGateway(
        structured_results={
            "generate_follow_ups": FollowUpQuestions(questions=["Next?"])
        }
    )
    install_resources(gateway)
    processed: list[ProcessedHistoryEntry] = [
        {"timestamp": f"2026-01-0{index}T00:00:00+00:00", "user_query": f"q-{index}", "answer": f"a-{index}"}
        for index in range(6, 0, -1)
    ]
    notices = ["Identifiers were removed."]
    state = {
        **_merged_state("clarified query"),
        "scrubbed_question": "original scrubbed query",
        "processed_history": processed,
        "safety_notices": notices,
        "validated": "validated answer",
    }

    output = await generate_follow_ups(state)

    call = gateway.calls[0]
    assert output == {"follow_ups": ["Next?"]}
    assert call["history_context"] == render_followup_history(processed)
    assert "q-6" in call["history_context"]
    assert "q-2" in call["history_context"]
    assert "q-1" not in call["history_context"]
    assert call["original_query"] == "original scrubbed query"
    assert call["answer"] == render_display_answer("validated answer", notices)


@pytest.mark.asyncio
@pytest.mark.parametrize("working_query", ["simple query", "clarified query"])
async def test_evaluation_uses_active_query_for_both_prompt_fields(
    install_resources: ResourceInstaller, working_query: str
) -> None:
    gateway = FakeGateway(
        structured_results={
            "evaluate_retrieval": RetrievalEvaluation(
                is_sufficient=True,
                missing_information=None,
                additional_queries=None,
            )
        }
    )
    install_resources(gateway)

    await evaluate_retrieval(_merged_state(working_query))

    call = gateway.calls[0]
    assert call["original_query"] == working_query
    assert call["clarified_query"] == working_query
