"""Regression: route_tools must bind the collections declared in GraphSettings."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from healthcare_rag.graph.llm import LangChainLLMGateway
from healthcare_rag.graph.settings import GraphSettings


def _gateway_with_fake_model() -> tuple[LangChainLLMGateway, MagicMock]:
    settings = GraphSettings.from_env()
    gateway = LangChainLLMGateway(settings=settings)
    model = MagicMock()
    bound = MagicMock()
    bound.invoke.return_value = MagicMock(tool_calls=[{"name": "query_lipitor"}])
    model.bind_tools.return_value = bound
    gateway._models[("default", settings.llm_model, None, settings.reasoning_effort)] = model  # type: ignore[assignment]
    return gateway, model


def test_route_tools_binds_every_configured_collection() -> None:
    gateway, model = _gateway_with_fake_model()
    calls = gateway.route_tools("What are Lipitor side effects?")
    assert calls == [{"name": "query_lipitor"}]
    (tools,), _ = model.bind_tools.call_args
    names = {tool["function"]["name"] for tool in tools}
    assert names == {"query_lipitor", "query_metformin"}


def test_settings_carry_the_legacy_collections() -> None:
    settings = GraphSettings.from_env()
    assert list(settings.collection_names) == ["Lipitor", "Metformin"]
