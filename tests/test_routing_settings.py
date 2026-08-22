from __future__ import annotations

import subprocess
import sys
from typing import get_args

import pytest

from healthcare_rag.graph.engine import GraphEngine, SafetyClassifierUnavailableError
from healthcare_rag.graph.engine_record import ResultContext, TurnTiming, build_result
from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.graph.state import GraphOutput, RAGState
from healthcare_rag.models.safety import SafetyAssessment, SafetyCategory, SafetyOutcome
from healthcare_rag.services import model_sampling, models


def test_current_defaults_preserve_graph_description_and_public_output_shape() -> None:
    # Given
    settings = GraphSettings.from_env()

    # When
    description = GraphEngine(settings).describe()

    # Then
    assert settings.safety_gate_enabled is True
    assert description["engine"] == "graph"
    assert set(GraphOutput.__annotations__) == {
        "answer",
        "follow_ups",
        "safety",
        "selected_branch_type",
        "selected_branch_query",
        "error",
    }
    assert get_args(SafetyCategory) == (
        "in_scope_informational",
        "personal_medical_advice",
        "emergency_red_flag",
        "out_of_scope",
        "prompt_injection",
        "ambiguous",
    )
    assert models.sampling_params is model_sampling.sampling_params


def test_routing_arms_when_unset_default_to_current_and_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.delenv("HC_RAG_QUERY_RESPONSE_ARM", raising=False)
    monkeypatch.delenv("HC_RAG_SAFETY_CLASSIFIER", raising=False)

    # When
    settings = GraphSettings.from_env()

    # Then
    assert models.query_response_arm() == "current"
    assert models.safety_classifier_backend() == "llm"
    assert settings.query_response_arm == "current"
    assert settings.safety_classifier == "llm"


def test_engine_when_semantic_router_is_selected_fails_before_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setenv("HC_RAG_SAFETY_CLASSIFIER", "semantic_router")
    settings = GraphSettings.from_env()

    # When
    with pytest.raises(SafetyClassifierUnavailableError) as error:
        _ = GraphEngine(settings)

    # Then
    assert str(error.value) == (
        "Safety classifier backend 'semantic_router' is unavailable: "
        "the compatible optional extra is not installed. "
        "Set HC_RAG_SAFETY_CLASSIFIER=llm."
    )


@pytest.mark.parametrize(
    ("response_arm", "classifier"),
    [("deterministic", "semantic_router"), ("tool", "llm")],
)
def test_routing_arms_when_valid_round_trip_in_settings(
    monkeypatch: pytest.MonkeyPatch,
    response_arm: str,
    classifier: str,
) -> None:
    # Given
    monkeypatch.setenv("HC_RAG_QUERY_RESPONSE_ARM", response_arm)
    monkeypatch.setenv("HC_RAG_SAFETY_CLASSIFIER", classifier)

    # When
    settings = GraphSettings.from_env()

    # Then
    assert settings.query_response_arm == response_arm
    assert settings.safety_classifier == classifier


@pytest.mark.parametrize(
    ("name", "getter_name", "value"),
    [
        ("HC_RAG_QUERY_RESPONSE_ARM", "query_response_arm", ""),
        ("HC_RAG_QUERY_RESPONSE_ARM", "query_response_arm", "Current"),
        ("HC_RAG_QUERY_RESPONSE_ARM", "query_response_arm", "unknown"),
        ("HC_RAG_SAFETY_CLASSIFIER", "safety_classifier_backend", ""),
        ("HC_RAG_SAFETY_CLASSIFIER", "safety_classifier_backend", "LLM"),
        ("HC_RAG_SAFETY_CLASSIFIER", "safety_classifier_backend", "unknown"),
    ],
)
def test_routing_arms_when_malformed_raise_value_specific_error(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    getter_name: str,
    value: str,
) -> None:
    # Given
    monkeypatch.setenv(name, value)

    # When
    with pytest.raises(ValueError, match=name) as error:
        getattr(models, getter_name)()

    # Then
    assert repr(value) in str(error.value)


def test_safety_telemetry_when_classified_keeps_classifier_costs_separate() -> None:
    # Given
    assessment = SafetyAssessment(category="out_of_scope", benign_social=True)
    outcome = SafetyOutcome(
        category="out_of_scope",
        contains_phi=False,
        short_circuited=False,
        benign_social=True,
        llm_calls=1,
        classifier_backend="llm",
        classifier_calls=1,
        embedding_calls=0,
        classifier_fallback=None,
        classifier_latency_s=0.125,
    )

    # When
    telemetry = outcome.model_dump(mode="json")

    # Then
    assert assessment.benign_social is True
    assert telemetry["benign_social"] is True
    assert telemetry["llm_calls"] == 1
    assert telemetry["classifier_calls"] == 1
    assert telemetry["embedding_calls"] == 0
    assert telemetry["classifier_backend"] == "llm"
    assert telemetry["classifier_fallback"] is None
    assert telemetry["classifier_latency_s"] == 0.125


def test_engine_record_when_router_has_sensitive_input_projects_safe_telemetry() -> None:
    # Given
    canary = "SYNTHETIC-PHI-CANARY-000"
    settings = GraphSettings.from_env()
    state = {
        "query_router": {
            "backend": "tool",
            "action": "pipeline",
            "raw_question": canary,
            "nested": {"question": canary, "label": "safe"},
        }
    }
    context = ResultContext(TurnTiming(0.0, None, None, 1.0), settings, None)

    # When
    record, _ = build_result(state, [], context)

    # Then
    assert record["query_router"] == {
        "backend": "tool",
        "action": "pipeline",
        "nested": {"label": "safe"},
    }
    assert canary not in repr(record["query_router"])


@pytest.mark.asyncio
async def test_safety_gate_when_new_turn_starts_clears_routing_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    from healthcare_rag.graph.nodes import safety

    monkeypatch.setenv("HC_RAG_SAFETY_GATE", "false")
    stale: RAGState = {
        "direct_response": "old direct response",
        "response_action": "direct",
        "query_router": {"backend": "tool", "action": "direct"},
    }

    # When
    update = (await safety.safety_gate({"question": "new turn", "messages": [], **stale})).update
    assert update is not None

    # Then
    assert update["direct_response"] is None
    assert update["response_action"] is None
    assert update["query_router"] is None


def test_default_import_when_semantic_router_is_unavailable_succeeds() -> None:
    # Given
    guard = (
        "import sys; sys.modules['semantic_router'] = None; "
        "import healthcare_rag; print('default-import-ok')"
    )

    # When
    completed = subprocess.run(
        [sys.executable, "-c", guard],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "default-import-ok"
