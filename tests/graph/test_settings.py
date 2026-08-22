from __future__ import annotations

import pytest

from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.services.models import refusal_boundary_enabled


def test_refusal_boundary_defaults_to_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HC_RAG_REFUSAL_BOUNDARY", raising=False)

    assert refusal_boundary_enabled() is True


def test_refusal_boundary_accepts_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_RAG_REFUSAL_BOUNDARY", "false")

    assert refusal_boundary_enabled() is False


def test_refusal_boundary_rejects_invalid_boolean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_RAG_REFUSAL_BOUNDARY", "maybe")

    with pytest.raises(ValueError) as exc_info:
        refusal_boundary_enabled()

    message = str(exc_info.value)
    assert "HC_RAG_REFUSAL_BOUNDARY" in message
    for accepted in ("0", "1", "false", "no", "off", "on", "true", "yes"):
        assert accepted in message


def test_graph_settings_default_refusal_boundary_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HC_RAG_REFUSAL_BOUNDARY", raising=False)

    assert GraphSettings.from_env().refusal_boundary_enabled is True


def test_graph_settings_reads_disabled_refusal_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HC_RAG_REFUSAL_BOUNDARY", "false")

    assert GraphSettings.from_env().refusal_boundary_enabled is False
