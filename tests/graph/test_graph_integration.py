from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import ToolCall
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langchain_core.runnables import RunnableConfig

from healthcare_rag.graph.build import build_graph
from healthcare_rag.graph.nodes import safety
from healthcare_rag.graph.state import GraphInput, GraphOutput, RAGState
from healthcare_rag.models.answers import Citation, CitedAnswerResult, StatementWithCitations
from healthcare_rag.models.queries import DecomposedQuery, RetrievalEvaluation
from healthcare_rag.models.retrieval import QueryDocument, QueryResult, QueryResultList
from healthcare_rag.models.safety import SafetyAssessment

from .conftest import FakeGateway, FakeRetriever, ResourceInstaller


def _assessment(
    category: str = "in_scope_informational",
    *,
    reformulation: str | None = None,
    phi_spans: list[str] | None = None,
) -> SafetyAssessment:
    return SafetyAssessment.model_construct(
        category=category,
        contains_phi=bool(phi_spans),
        phi_spans=phi_spans or [],
        drug_mentioned="lipitor",
        rationale="scripted",
        safe_reformulation=reformulation,
    )


def _result(query: str = "routed") -> QueryResultList:
    return QueryResultList(
        results=[
            QueryResult(
                source="Lipitor",
                query=query,
                docs=[
                    QueryDocument(
                        content="Lipitor information.",
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


def _valid_answer() -> CitedAnswerResult:
    return CitedAnswerResult(
        statements=[
            StatementWithCitations(
                text="Lipitor information.",
                citations=[
                    Citation(
                        doc_id="doc_1",
                        source_name="Lipitor",
                        quote="Lipitor information.",
                    )
                ],
                linebreaks="",
            )
        ]
    )


def _tool_call() -> ToolCall:
    return {
        "name": "query_lipitor",
        "args": {"query": "routed"},
        "id": "call-lipitor",
        "type": "tool_call",
    }


def _install_graph(
    install_resources: ResourceInstaller,
    *,
    decomposition: DecomposedQuery | None = None,
    evaluation: RetrievalEvaluation | None = None,
    assessment: SafetyAssessment | None = None,
) -> tuple[FakeGateway, FakeRetriever, InMemorySaver, CompiledStateGraph[RAGState, None, GraphInput, GraphOutput]]:
    structured: dict[str, Any] = {
        "safety_gate": assessment or _assessment(),
        "evaluate_retrieval": evaluation
        or RetrievalEvaluation(
            is_sufficient=True,
            missing_information=None,
            additional_queries=None,
        ),
        "validate_answer": _valid_answer(),
    }
    if decomposition is not None:
        structured["decompose_query"] = decomposition
    gateway = FakeGateway(
        structured_results=structured,
        completion_results={"generate_answer": "Lipitor information [doc_1]."},
        tool_calls=[_tool_call()],
    )
    retriever = FakeRetriever(results={"Lipitor": _result()})
    _ = install_resources(gateway, retriever=retriever)
    saver = InMemorySaver()
    return gateway, retriever, saver, build_graph().compile(checkpointer=saver)


@pytest.fixture(autouse=True)
def _pin_graph_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_RAG_SAFETY_GATE", "true")
    monkeypatch.setenv("HC_RAG_DISABLE_STAGES", "")
    monkeypatch.setenv("HC_RAG_DECOMPOSE_ONLY_COMPLEX", "true")
    monkeypatch.setenv("HC_RAG_MAX_SUBQUERIES", "3")
    monkeypatch.setattr(safety, "PIPELINE", None)


@pytest.mark.asyncio
async def test_simple_turn_retrieves_once_and_writes_only_graph_output(
    install_resources: ResourceInstaller,
) -> None:
    gateway, retriever, _saver, graph = _install_graph(install_resources)
    config: RunnableConfig = {"configurable": {"thread_id": "simple"}}

    output = await graph.ainvoke(
        {"question": "What is Lipitor?", "user_id": "user"}, config
    )
    state = graph.get_state(config).values

    assert set(output) <= {
        "answer",
        "follow_ups",
        "safety",
        "selected_branch_type",
        "selected_branch_query",
        "error",
    }
    assert "question" not in output
    assert len(retriever.calls) == 1
    assert output["answer"] == "Lipitor information. [doc_1]"
    assert [message.content for message in state["messages"]] == [
        "What is Lipitor?",
        "Lipitor information. [doc_1]",
    ]
    assert sum(call.get("stage") == "evaluate_retrieval" for call in gateway.calls) == 1


@pytest.mark.asyncio
async def test_complex_turn_retrieves_parent_and_subqueries_then_evaluates_once(
    install_resources: ResourceInstaller, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HC_RAG_DECOMPOSE_ONLY_COMPLEX", "false")
    decomposition = DecomposedQuery(
        original_query="compare",
        query_complexity="complex",
        decomposed_query=["one", "two"],
    )
    gateway, retriever, _saver, graph = _install_graph(
        install_resources, decomposition=decomposition
    )
    config: RunnableConfig = {"configurable": {"thread_id": "complex"}}

    await graph.ainvoke({"question": "compare", "user_id": "user"}, config)
    state = graph.get_state(config).values

    assert state["sub_queries"] == ["one", "two"]
    assert len(retriever.calls) == 3
    assert sum(call.get("stage") == "evaluate_retrieval" for call in gateway.calls) == 1
    assert any(event.get("branch") == "synthesized" for event in state["branch_events"])


@pytest.mark.asyncio
async def test_gap_fill_routes_directly_to_generation_after_one_evaluation(
    install_resources: ResourceInstaller,
) -> None:
    evaluation = RetrievalEvaluation(
        is_sufficient=False,
        missing_information="details",
        additional_queries=["gap-1", "gap-2", "gap-3", "gap-4"],
    )
    gateway, retriever, _saver, graph = _install_graph(
        install_resources, evaluation=evaluation
    )
    config: RunnableConfig = {"configurable": {"thread_id": "gap"}}

    await graph.ainvoke({"question": "What is Lipitor?", "user_id": "user"}, config)
    state = graph.get_state(config).values

    assert len(retriever.calls) == 4
    assert sum(call.get("stage") == "evaluate_retrieval" for call in gateway.calls) == 1
    assert state["gap_filled"] is True
    assert all(event.get("branch") != "gap_fill" for event in state["branch_events"])


@pytest.mark.asyncio
async def test_insufficient_without_queries_generates_without_gap_sends(
    install_resources: ResourceInstaller,
) -> None:
    evaluation = RetrievalEvaluation(
        is_sufficient=False,
        missing_information="details",
        additional_queries=[],
    )
    _gateway, retriever, _saver, graph = _install_graph(
        install_resources, evaluation=evaluation
    )
    config: RunnableConfig = {"configurable": {"thread_id": "no-gap"}}

    output = await graph.ainvoke(
        {"question": "What is Lipitor?", "user_id": "user"},
        config,
    )

    assert len(retriever.calls) == 1
    assert output["answer"] == "Lipitor information. [doc_1]"


@dataclass(slots=True)
class _AddendumPipeline:
    answer: str

    async def ainvoke(
        self, _state: RAGState, _config: RunnableConfig | None = None
    ) -> RAGState:
        return {"validated": self.answer, "route": [], "branch_events": []}


@pytest.mark.asyncio
async def test_refusal_appends_safe_addendum_without_follow_ups(
    install_resources: ResourceInstaller, monkeypatch: pytest.MonkeyPatch
) -> None:
    reformulation = "What adverse effects are listed for Lipitor?"
    assessment = _assessment(
        "personal_medical_advice", reformulation=reformulation
    )
    _gateway, retriever, _saver, graph = _install_graph(
        install_resources, assessment=assessment
    )
    monkeypatch.setattr(
        safety,
        "PIPELINE",
        _AddendumPipeline("Fatigue is listed as an adverse reaction."),
    )
    config: RunnableConfig = {"configurable": {"thread_id": "refusal"}}

    output = await graph.ainvoke(
        {"question": "Should I stop taking Lipitor?", "user_id": "user"},
        config,
    )

    assert retriever.calls == []
    assert "Fatigue is listed as an adverse reaction." in output["answer"]
    assert output["follow_ups"] == []


@pytest.mark.asyncio
async def test_two_turn_thread_resets_pipeline_state_and_accumulates_messages(
    install_resources: ResourceInstaller,
) -> None:
    _gateway, _retriever, _saver, graph = _install_graph(install_resources)
    config: RunnableConfig = {"configurable": {"thread_id": "two-turn"}}
    await graph.ainvoke({"question": "First Lipitor question", "user_id": "user"}, config)

    await graph.ainvoke({"question": "Second Lipitor question", "user_id": "user"}, config)
    state = graph.get_state(config).values

    assert len(state["messages"]) == 4
    assert state["scrubbed_question"] == "Second Lipitor question"
    assert len(state["retrievals"]) == 1


@pytest.mark.asyncio
async def test_pii_raw_question_is_absent_from_output_state_and_checkpoint_history(
    install_resources: ResourceInstaller,
) -> None:
    raw = "I am John Smith, MRN 12345, what is Lipitor for?"
    assessment = _assessment(phi_spans=["John Smith", "12345"])
    _gateway, _retriever, _saver, graph = _install_graph(
        install_resources, assessment=assessment
    )
    config: RunnableConfig = {"configurable": {"thread_id": "pii"}}

    output = await graph.ainvoke({"question": raw, "user_id": "user"}, config)

    assert "question" not in output
    assert raw not in repr(output)
    assert raw not in repr(graph.get_state(config).values)
    assert all(raw not in repr(item.values) for item in graph.get_state_history(config))
