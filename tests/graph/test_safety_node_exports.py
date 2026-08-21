from __future__ import annotations

import inspect
from typing import ClassVar, override

import pytest

from healthcare_rag.graph.nodes import safety, safety_classifier, safety_finalize
from healthcare_rag.models.safety import SafetyAssessment

from .conftest import FakeGateway, ResourceInstaller


@pytest.fixture(autouse=True)
def _pin_safety_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_RAG_SAFETY_GATE", "true")
    monkeypatch.setenv("HC_RAG_DISABLE_STAGES", "")
    monkeypatch.setenv("HC_RAG_REFUSAL_BOUNDARY", "false")


def test_original_safety_node_coroutine_and_signature_contracts() -> None:
    assert inspect.iscoroutinefunction(safety.LangChainSafetyGate._llm_assess)
    assert inspect.iscoroutinefunction(safety.finalize)
    assert inspect.iscoroutinefunction(safety.safety_gate)
    assert str(inspect.signature(safety.LangChainSafetyGate._llm_assess)) == (
        "(self, query: 'str', history_context: 'str' = '') -> 'SafetyAssessment'"
    )
    assert str(inspect.signature(safety.finalize)) == (
        "(state: 'RAGState') -> 'RAGState'"
    )
    assert str(inspect.signature(safety.safety_gate)) == (
        "(state: 'RAGState') -> 'Command[GateTarget]'"
    )


def test_classifier_old_and_new_import_paths_have_identical_objects() -> None:
    assert safety.LangChainSafetyGate is safety_classifier.LangChainSafetyGate
    assert str(
        inspect.signature(safety_classifier.LangChainSafetyGate._llm_assess)
    ) == ("(self, query: 'str', history_context: 'str' = '') -> 'SafetyAssessment'")


def test_finalizer_old_and_new_import_paths_have_identical_objects() -> None:
    assert safety.finalize is safety_finalize.finalize
    assert str(inspect.signature(safety_finalize.finalize)) == (
        "(state: 'RAGState') -> 'RAGState'"
    )


async def test_classifier_without_llm_uses_ambiguous_fail_soft_assessment() -> None:
    assessment = await safety.LangChainSafetyGate()._llm_assess("What about it?")

    assert assessment == SafetyAssessment(
        category="ambiguous",
        contains_phi=False,
        phi_spans=[],
        drug_mentioned="none",
        rationale="safety-gate LLM call failed; deterministic checks only",
    )


async def test_finalize_refusal_has_no_followups_and_persists_displayed_turn() -> None:
    result = await safety.finalize(
        {
            "scrubbed_question": "Should I change my metformin?",
            "working_query": "Should I change my metformin?",
            "safety_response": "Please ask your prescriber.",
            "safety_notices": ["Identifiers removed."],
            "follow_ups": ["This must be discarded."],
        }
    )

    assert result.get("answer") == "Identifiers removed.\n\nPlease ask your prescriber."
    assert result.get("follow_ups") == []
    assert result.get("selected_branch_query") is None
    assert [message.content for message in result.get("messages", [])] == [
        "Should I change my metformin?",
        "Identifiers removed.\n\nPlease ask your prescriber.",
    ]


async def test_finalize_refusal_clears_conflicting_direct_routing_state() -> None:
    result = await safety.finalize(
        {
            "scrubbed_question": "Should I change treatment?",
            "safety_response": "REFUSAL_WINS",
            "direct_response": "DIRECT_MUST_NOT_PERSIST",
            "response_action": "direct",
            "query_router": {"effective_action": "direct"},
            "follow_ups": ["STALE_FOLLOWUP"],
            "selected_branch_type": "stale",
            "selected_branch_query": "stale query",
        }
    )

    assert result.get("answer") == "REFUSAL_WINS"
    assert result.get("follow_ups") == []
    assert {
        "direct_response",
        "response_action",
        "query_router",
        "selected_branch_type",
        "selected_branch_query",
    } <= result.keys()
    assert result.get("direct_response") is None
    assert result.get("response_action") is None
    assert result.get("query_router") is None
    assert result.get("selected_branch_type") is None
    assert result.get("selected_branch_query") is None


async def test_finalize_validated_answer_persists_displayed_turn_and_followups() -> (
    None
):
    result = await safety.finalize(
        {
            "scrubbed_question": "What does the monograph say?",
            "working_query": "What does the monograph say?",
            "safety_response": "",
            "safety_notices": ["Safety notice."],
            "validated": "Supported answer.",
            "follow_ups": ["What else is listed?"],
        }
    )

    assert result.get("answer") == "Safety notice.\n\nSupported answer."
    assert result.get("follow_ups") == ["What else is listed?"]
    assert [message.content for message in result.get("messages", [])] == [
        "What does the monograph say?",
        "Safety notice.\n\nSupported answer.",
    ]


async def test_finalize_direct_response_has_priority_and_clears_medical_channels() -> (
    None
):
    result = await safety.finalize(
        {
            "scrubbed_question": "Hello",
            "direct_response": "Hello back.",
            "validated": "stale medical answer",
            "follow_ups": ["stale follow-up"],
            "merged": [{"stale": True}],
            "branch_events": [{"branch": "stale", "status": "SUCCEEDED"}],
        }
    )

    assert result.get("answer") == "Hello back."
    assert result.get("follow_ups") == []
    assert result.get("merged") is None
    assert [message.content for message in result.get("messages", [])] == [
        "Hello",
        "Hello back.",
    ]


async def test_safety_gate_resolves_live_classifier_module_global(
    monkeypatch: pytest.MonkeyPatch,
    install_resources: ResourceInstaller,
) -> None:
    _ = install_resources(FakeGateway())

    class SentinelSafetyGate(safety.LangChainSafetyGate):
        called: ClassVar[bool] = False

        @override
        async def _llm_assess(
            self,
            query: str,
            history_context: str = "",
        ) -> SafetyAssessment:
            del query, history_context
            type(self).called = True
            return SafetyAssessment(
                category="in_scope_informational",
                contains_phi=False,
                phi_spans=[],
                drug_mentioned="metformin",
                rationale="sentinel",
            )

    monkeypatch.setattr(safety, "LangChainSafetyGate", SentinelSafetyGate)

    command = await safety.safety_gate(
        {"question": "What is metformin used for?", "messages": []}
    )

    assert SentinelSafetyGate.called is True
    assert command.goto == ["clarify_query", "extract_conversation_context"]
