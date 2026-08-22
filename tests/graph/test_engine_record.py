from __future__ import annotations

from dataclasses import replace
from importlib.util import find_spec
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from healthcare_rag.graph.engine import GraphEngine, UsageRecorder, _redact_root_inputs
from healthcare_rag.graph.resources import get
from healthcare_rag.models.answers import (
    Citation,
    CitedAnswerResult,
    StatementWithCitations,
)
from healthcare_rag.models.queries import RetrievalEvaluation
from healthcare_rag.models.retrieval import QueryDocument, QueryResult, QueryResultList
from healthcare_rag.models.safety import SafetyAssessment

RESULT_KEYS = {
    "answer",
    "answered",
    "raw_answer",
    "follow_ups",
    "contexts",
    "retrieved_chunk_ids",
    "retrieved_pages",
    "retrieved_sources",
    "latency_s",
    "time_to_first_answer_s",
    "usage",
    "per_call_usage",
    "safety_outcome",
    "query_router",
    "error",
    "n_branches",
    "branch_types",
    "branch_statuses",
    "selected_branch_type",
    "selected_branch_query",
}


class _Gateway:
    async def astructured(self, stage, _model_type, **_kwargs):
        if stage == "safety_gate":
            return SafetyAssessment(category="in_scope_informational")
        if stage == "evaluate_retrieval":
            return RetrievalEvaluation(
                is_sufficient=True,
                missing_information=None,
                additional_queries=None,
            )
        if stage == "validate_answer":
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
        return None

    async def acomplete(self, _stage, **_kwargs):
        return "Lipitor information [doc_1]."

    async def aroute_tools(self, _query):
        return [
            {
                "name": "query_lipitor",
                "args": {"query": "routed"},
                "id": "call-lipitor",
                "type": "tool_call",
            }
        ]


async def _retrieve(_client, collection_name, query):
    return QueryResultList(
        results=[
            QueryResult(
                source=collection_name,
                query=query,
                docs=[
                    QueryDocument(
                        content="Lipitor information.",
                        score=0.9,
                        doc_id="doc-1",
                        source_name="Lipitor",
                        metadata={"id_": "doc_1"},
                        page_numbers=[1],
                    )
                ],
            )
        ]
    )


def _install_graph(install_resources) -> None:
    install_resources(_Gateway(), retriever=_retrieve)


@pytest.mark.asyncio
async def test_graph_engine_when_running_simple_turn_returns_legacy_key_set(
    install_resources,
) -> None:
    _install_graph(install_resources)
    engine = GraphEngine(get().settings)

    result = await engine.run_turn("thread", "What is Lipitor?")

    assert set(result) == RESULT_KEYS
    assert result["answer"] == "Lipitor information. [doc_1]"
    assert result["branch_types"] == ["initial"]
    assert result["query_router"] is None
    await engine.aclose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    find_spec("langgraph.checkpoint.sqlite") is None
    or find_spec("langgraph.checkpoint.sqlite.aio") is None,
    reason="graph-sqlite optional dependency is not installed",
)
async def test_sqlite_engine_when_reopened_preserves_turn_messages(
    install_resources,
    tmp_path,
) -> None:
    _install_graph(install_resources)
    settings = replace(get().settings, checkpoint_uri=f"sqlite:{tmp_path / 'state.db'}")
    first = GraphEngine(settings)
    await first.run_turn("durable", "What is Lipitor?")
    await first.aclose()

    second = GraphEngine(settings)
    await second.__aenter__()
    snapshot = await second.compiled.aget_state(
        {"configurable": {"thread_id": "durable"}}
    )

    assert [message.content for message in snapshot.values["messages"]] == [
        "What is Lipitor?",
        "Lipitor information. [doc_1]",
    ]
    await second.aclose()


@pytest.mark.asyncio
async def test_sqlite_engine_when_dependency_is_missing_names_install_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(get().settings, checkpoint_uri="sqlite:missing.db")

    def missing(name: str):
        if name == "langgraph.checkpoint.sqlite.aio":
            raise ModuleNotFoundError(name)
        raise AssertionError(name)

    monkeypatch.setattr("healthcare_rag.graph.engine.import_module", missing)
    with pytest.raises(RuntimeError, match=r"healthcare-rag\[graph-sqlite\]"):
        await GraphEngine(settings).__aenter__()


@pytest.mark.asyncio
async def test_usage_recorder_maps_cache_read_to_legacy_shape() -> None:
    recorder = UsageRecorder()
    run_id = uuid4()
    await recorder.on_llm_start(
        {"name": "fake"},
        ["prompt"],
        run_id=run_id,
        metadata={"ls_model_name": "model-a"},
    )
    response = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="answer",
                        usage_metadata={
                            "input_tokens": 12,
                            "output_tokens": 4,
                            "total_tokens": 16,
                            "input_token_details": {"cache_read": 7},
                        },
                    )
                )
            ]
        ]
    )

    await recorder.on_llm_end(response, run_id=run_id)

    call = recorder.calls[0]
    assert call.model == "model-a"
    assert call.prompt_tokens == 12
    assert call.completion_tokens == 4
    assert call.cached_prompt_tokens == 7


def test_redact_root_inputs_when_scrubber_fails_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_question: str) -> tuple[str, list[str]]:
        raise RuntimeError("scrubber failed")  # noqa: GENERIC_ERR_OK - synthetic fault.

    monkeypatch.setattr("healthcare_rag.graph.engine.scrub_phi", fail)
    assert _redact_root_inputs({"question": "John Smith MRN 12345"}) == {}
