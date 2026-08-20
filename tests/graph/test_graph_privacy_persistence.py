from __future__ import annotations

from langchain_core.messages import ToolCall
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from healthcare_rag.graph.build import build_graph
from healthcare_rag.models.answers import Citation, CitedAnswerResult, StatementWithCitations
from healthcare_rag.models.queries import RetrievalEvaluation
from healthcare_rag.models.retrieval import QueryDocument, QueryResult, QueryResultList
from healthcare_rag.models.safety import SafetyAssessment

from .conftest import FakeGateway, FakeRetriever, ResourceInstaller


def _result(query: str) -> QueryResultList:
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


def _tool_call(query: str) -> ToolCall:
    return {
        "name": "query_lipitor",
        "args": {"query": query},
        "id": "citation-route",
        "type": "tool_call",
    }


async def test_structured_citation_canaries_are_absent_from_updates_and_checkpoints(
    install_resources: ResourceInstaller,
) -> None:
    canaries = ("DOC-77881", "Source Canary", "9911223")
    structured = CitedAnswerResult(
        statements=[
            StatementWithCitations(
                text="Lipitor information.",
                citations=[
                    Citation(
                        doc_id=f"Patient account {canaries[0]}",
                        source_name=f"My name is {canaries[1]}",
                        quote=f"MRN-{canaries[2]}",
                    )
                ],
                linebreaks="",
            )
        ]
    )
    gateway = FakeGateway(
        structured_results={
            "safety_gate": SafetyAssessment(
                category="in_scope_informational",
                contains_phi=False,
                phi_spans=[],
                drug_mentioned="lipitor",
                rationale="informational",
                safe_reformulation=None,
            ),
            "evaluate_retrieval": RetrievalEvaluation(
                is_sufficient=True,
                missing_information=None,
                additional_queries=None,
            ),
            "validate_answer": structured,
        },
        completion_results={"generate_answer": "Lipitor information [doc_1]."},
        tool_calls=[_tool_call("Lipitor")],
    )
    retriever = FakeRetriever(results={"Lipitor": _result("Lipitor")})
    _ = install_resources(gateway, retriever=retriever, disabled=("followups",))
    saver = InMemorySaver()
    graph = build_graph().compile(checkpointer=saver)
    config: RunnableConfig = {"configurable": {"thread_id": "citation-privacy"}}
    updates = [
        update
        async for update in graph.astream(
            {"question": "What is Lipitor?", "user_id": "opaque"},
            config,
            stream_mode="updates",
            durability="exit",
        )
    ]
    state = graph.get_state(config).values
    checkpoints = [item.values for item in graph.get_state_history(config)]
    rendered = repr({"updates": updates, "state": state, "checkpoints": checkpoints})

    assert all(canary not in rendered for canary in canaries)
