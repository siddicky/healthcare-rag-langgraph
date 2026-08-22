from __future__ import annotations

import os
from pathlib import Path

import pytest

from healthcare_rag.graph.engine import GraphEngine
from healthcare_rag.services.tracing import tracing_enabled


@pytest.mark.parametrize("tracing_variable", ["LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"])
def test_graph_engine_when_tracing_lacks_input_hiding_disables_all_tracing_aliases(
    monkeypatch: pytest.MonkeyPatch, tracing_variable: str
) -> None:
    # Given: one LangSmith-compatible tracing switch but no input redaction switch.
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGSMITH_HIDE_INPUTS", raising=False)
    monkeypatch.setenv(tracing_variable, "true")

    # When: a healthcare graph runtime is constructed.
    GraphEngine()

    # Then: every environment-driven tracing path is off before graph invocation.
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"
    assert not tracing_enabled()


def test_graph_engine_when_input_hiding_is_enabled_preserves_tracing_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an explicitly privacy-safe tracing opt-in.
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.setenv("LANGSMITH_HIDE_INPUTS", "true")

    # When: a healthcare graph runtime is constructed.
    GraphEngine()

    # Then: the opted-in tracing remains available.
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert tracing_enabled()


@pytest.mark.parametrize("hide_inputs", ["", "false", "TRUE", "yes", "invalid"])
def test_graph_engine_when_input_hiding_is_not_exact_true_disables_tracing(
    monkeypatch: pytest.MonkeyPatch, hide_inputs: str
) -> None:
    # Given: tracing and a malformed or non-canonical privacy switch.
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.setenv("LANGSMITH_HIDE_INPUTS", hide_inputs)

    # When: a healthcare graph runtime is constructed.
    GraphEngine()

    # Then: tracing cannot start with unhidden inputs.
    assert not tracing_enabled()


@pytest.mark.parametrize("tracing_flag", ["", "TRUE", "yes", "1", "not-a-boolean"])
def test_tracing_is_off_when_environment_flag_is_not_exact_true(
    monkeypatch: pytest.MonkeyPatch, tracing_flag: str
) -> None:
    # Given: no valid tracing flags.
    monkeypatch.setenv("LANGSMITH_TRACING", tracing_flag)
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "")

    # When: the tracing state is read.
    enabled = tracing_enabled()

    # Then: malformed configuration cannot activate tracing.
    assert not enabled


def test_env_example_when_copied_has_privacy_safe_tracing_defaults() -> None:
    # Given: the tracked environment template.
    example = Path(__file__).parents[1] / ".env.example"

    # When: its tracing defaults are inspected.
    variables = {
        line.split("=", maxsplit=1)[0]: line.split("=", maxsplit=1)[1]
        for line in example.read_text().splitlines()
        if line.startswith("LANGSMITH_") and "=" in line
    }

    # Then: tracing is inert unless deliberately enabled with input hiding.
    assert variables["LANGSMITH_TRACING"] == "false"
    assert variables["LANGSMITH_HIDE_INPUTS"] == "true"
