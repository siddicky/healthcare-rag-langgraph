from __future__ import annotations

import inspect
from collections.abc import Callable

import pytest

from healthcare_rag.services import model_sampling, models


@pytest.mark.parametrize(
    ("getter", "environment_name", "default", "override"),
    [
        (
            models.default_llm_model,
            "HC_RAG_LLM_MODEL",
            "gpt-5.6-luna",
            "custom-model",
        ),
        (
            models.default_validator_model,
            "HC_RAG_VALIDATOR_MODEL",
            "gpt-5.6-terra",
            "custom-validator",
        ),
        (
            models.default_reasoning_effort,
            "HC_RAG_REASONING_EFFORT",
            "none",
            "high",
        ),
    ],
)
def test_model_getters_read_defaults_overrides_and_blank_fallback_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
    getter: Callable[[], str],
    environment_name: str,
    default: str,
    override: str,
) -> None:
    monkeypatch.delenv(environment_name, raising=False)
    assert getter() == default

    monkeypatch.setenv(environment_name, f"  {override}  ")
    assert getter() == override

    monkeypatch.setenv(environment_name, "   ")
    assert getter() == default


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-5.6-luna", True),
        ("GPT-5.6-TERRA", True),
        ("o1-mini", True),
        ("O3", True),
        ("o4-mini", True),
        ("gpt-4o-mini", False),
        ("custom", False),
    ],
)
def test_reasoning_model_detection_is_case_insensitive(
    model: str,
    expected: bool,
) -> None:
    assert models.is_reasoning_model(model) is expected


@pytest.mark.parametrize(
    ("model", "temperature", "effort", "expected"),
    [
        ("gpt-4o-mini", 0.1, None, {"temperature": 0.1}),
        ("gpt-4o-mini", None, "high", {}),
        (
            "gpt-5.6-luna",
            0.0,
            "none",
            {"reasoning_effort": "none", "temperature": 0.0},
        ),
        ("gpt-5.6-luna", 0.1, "low", {"reasoning_effort": "low"}),
        ("gpt-5.6-luna", None, "none", {"reasoning_effort": "none"}),
        ("o1-mini", 0.1, "none", {"reasoning_effort": "low"}),
        ("o3", None, "none", {"reasoning_effort": "low"}),
        ("o4-mini", 0.1, "medium", {"reasoning_effort": "medium"}),
    ],
)
def test_sampling_matrix_preserves_supported_model_family_rules(
    model: str,
    temperature: float | None,
    effort: str | None,
    expected: dict[str, float | str],
) -> None:
    assert models.sampling_params(model, temperature, effort) == expected


def test_sampling_effort_falls_back_to_environment_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HC_RAG_REASONING_EFFORT", "high")
    assert models.sampling_params("gpt-5.6-luna", 0.2) == {
        "reasoning_effort": "high"
    }

    monkeypatch.setenv("HC_RAG_REASONING_EFFORT", "none")
    assert models.sampling_params("gpt-5.6-luna", 0.2) == {
        "reasoning_effort": "none",
        "temperature": 0.2,
    }


def test_original_model_sampling_signatures_are_characterized() -> None:
    assert str(inspect.signature(models.default_llm_model)) == "() -> 'str'"
    assert str(inspect.signature(models.default_validator_model)) == "() -> 'str'"
    assert str(inspect.signature(models.default_reasoning_effort)) == "() -> 'str'"
    assert str(inspect.signature(models.is_reasoning_model)) == (
        "(model: 'str') -> 'bool'"
    )
    assert str(inspect.signature(models.sampling_params)) == (
        "(model: 'str', temperature: 'float | None' = None, "
        "reasoning_effort: 'str | None' = None) -> 'dict[str, Any]'"
    )


def test_original_model_api_is_an_identity_preserving_facade() -> None:
    assert models.DEFAULT_LLM_MODEL is model_sampling.DEFAULT_LLM_MODEL
    assert models.DEFAULT_VALIDATOR_MODEL is model_sampling.DEFAULT_VALIDATOR_MODEL
    assert models.DEFAULT_REASONING_EFFORT is model_sampling.DEFAULT_REASONING_EFFORT
    assert models.default_llm_model is model_sampling.default_llm_model
    assert models.default_validator_model is model_sampling.default_validator_model
    assert models.default_reasoning_effort is model_sampling.default_reasoning_effort
    assert models.is_reasoning_model is model_sampling.is_reasoning_model
    assert models.sampling_params is model_sampling.sampling_params
