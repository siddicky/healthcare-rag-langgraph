from __future__ import annotations

import logging
from typing import final, override

import pytest
from langchain_core.messages import ToolCall
from healthcare_rag.graph.engine import GraphEngine
from healthcare_rag.graph.nodes.generate import generate_answer, validate_answer
from healthcare_rag.graph.nodes.retrieve import retrieve_documents
from healthcare_rag.graph.resources import (
    Resources,
    get as get_resources,
    override as override_resources,
)
from healthcare_rag.graph.state import dump_results
from healthcare_rag.models.answers import Citation, CitedAnswerResult, StatementWithCitations
from healthcare_rag.models.retrieval import QueryDocument, QueryResult, QueryResultList
from healthcare_rag.monitor import QueryMonitor
from healthcare_rag.processors.privacy import PrivacySanitizer, PrivacyScanError, Readiness

from .conftest import FakeGateway, FakeRetriever, ResourceInstaller, make_settings


@final
class SyntheticInitializationError(Exception):
    __slots__: tuple[str, ...] = ("detail",)
    detail: str

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)

    @override
    def __str__(self) -> str:
        return self.detail


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
        "id": "privacy-route",
        "type": "tool_call",
    }


async def test_model_authored_routed_query_is_sanitized_before_retrieval(
    install_resources: ResourceInstaller,
) -> None:
    identifier = "AC-77881"
    routed = f"Patient account {identifier} Lipitor interactions"
    gateway = FakeGateway(tool_calls=[_tool_call(routed)])
    retriever = FakeRetriever(results={"Lipitor": _result(routed)})
    _ = install_resources(gateway, retriever=retriever)

    _ = await retrieve_documents(
        {
            "query": f"My name is Jane Doe. {routed}",
            "kind": "initial",
            "index": 0,
            "phase": 0,
            "branch": "initial",
        }
    )

    assert "Jane Doe" not in gateway.calls[0]["query"]
    assert identifier not in retriever.calls[0][1]


async def test_generation_is_sanitized_before_validator_and_monitor(
    install_resources: ResourceInstaller,
) -> None:
    identifier = "AC-77881"
    structured = CitedAnswerResult(
        statements=[
            StatementWithCitations(
                text=f"Patient account {identifier} Lipitor information.",
                citations=[
                    Citation(
                        doc_id="Patient account DOC-77881",
                        source_name="My name is Source Canary",
                        quote="MRN-9911223",
                    )
                ],
                linebreaks="",
            )
        ]
    )
    gateway = FakeGateway(
        completion_results={
            "generate_answer": f"Patient account {identifier} Lipitor information [doc_1]."
        },
        structured_results={"validate_answer": structured},
    )
    _ = install_resources(gateway)
    state = {
        "working_query": "What is Lipitor?",
        "merged": dump_results(_result("What is Lipitor?")),
        "summary": {"relevant_snippets": ""},
    }

    generated = await generate_answer(state)
    validated = await validate_answer({**state, **generated})
    monitor = QueryMonitor()
    generation = generated.get("generation")
    assert isinstance(generation, dict)
    plain_answer = generation.get("plain_answer")
    assert isinstance(plain_answer, str)
    monitor.set_raw_answer(plain_answer)
    validator_call = next(call for call in gateway.calls if call.get("stage") == "validate_answer")

    assert identifier not in plain_answer
    assert identifier not in validator_call["answer"]
    assert identifier not in (validated["validated"] or "")
    assert identifier not in (monitor.raw_answer or "")
    assert "DOC-77881" not in repr(validated["structured"])
    assert "Source Canary" not in repr(validated["structured"])
    assert "9911223" not in repr(validated["structured"])


def test_resources_owns_one_sticky_ready_sanitizer() -> None:
    resources = Resources(make_settings())

    first = resources.privacy
    first.initialize()

    assert resources.privacy is first
    assert first.readiness is Readiness.READY


def test_initialization_failure_is_raw_free_and_sticky(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sanitizer = PrivacySanitizer()
    attempts = 0

    def fail_build():
        nonlocal attempts
        attempts += 1
        raise SyntheticInitializationError("Jane Doe must never leave this boundary")

    monkeypatch.setattr(sanitizer, "_build_analyzer", fail_build)

    codes: list[str] = []
    for _ in range(2):
        try:
            sanitizer.initialize()
        except PrivacyScanError as exc:
            codes.append(str(exc))

    assert attempts == 1
    assert codes == ["PRIVACY_INITIALIZATION_FAILED", "PRIVACY_NOT_READY"]
    assert sanitizer.readiness is Readiness.FAILED


async def test_engine_failure_sets_stable_monitor_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sanitizer = PrivacySanitizer()

    def fail_build():
        raise SyntheticInitializationError("Jane Doe upstream detail")

    monkeypatch.setattr(sanitizer, "_build_analyzer", fail_build)
    settings = make_settings()
    previous = get_resources()
    override_resources(Resources(settings, privacy=sanitizer))
    monitor = QueryMonitor()

    try:
        result = await GraphEngine(settings).process_query(
            "My name is Jane Doe. Explain Lipitor.",
            "opaque-thread",
            monitor,
        )
    finally:
        override_resources(previous)

    assert result["error"] == "PRIVACY_INITIALIZATION_FAILED"
    assert "Jane Doe" not in repr(result)
    assert monitor.error == "PRIVACY_INITIALIZATION_FAILED"
    assert monitor.raw_answer_event.is_set()
    assert monitor.final_answer_event.is_set()


async def test_runtime_failure_logs_and_monitor_surfaces_are_raw_free(
    install_resources: ResourceInstaller,
    caplog: pytest.LogCaptureFixture,
) -> None:
    canary = "Jane Doe"
    gateway = FakeGateway(route_error=SyntheticInitializationError(canary))
    _ = install_resources(gateway)
    caplog.set_level(logging.WARNING)

    _ = await retrieve_documents(
        {
            "query": "What is Lipitor?",
            "kind": "initial",
            "index": 0,
            "phase": 0,
            "branch": "initial",
        }
    )
    monitor = QueryMonitor()
    monitor.set_final_answer(f"My name is {canary}. Lipitor information.")
    monitor.set_follow_up_questions([f"Patient account AC-77881 for {canary}"])
    monitor.set_error(f"failure for {canary}")

    assert canary not in caplog.text
    assert canary not in (monitor.final_answer or "")
    assert "AC-77881" not in repr(monitor.follow_up_questions)
    assert monitor.error == "PIPELINE_EXECUTION_FAILED"
