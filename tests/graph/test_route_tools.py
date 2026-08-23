"""Regression: aroute_tools binds GraphSettings collections; astructured runs concurrently."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from healthcare_rag.graph.llm import LangChainLLMGateway
from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.processors.privacy import PrivacySanitizer


def _gateway_with_fake_model() -> tuple[LangChainLLMGateway, MagicMock]:
    settings = GraphSettings.from_env()
    gateway = LangChainLLMGateway(PrivacySanitizer(), settings=settings)
    model = MagicMock()
    bound = MagicMock()
    bound.ainvoke = AsyncMock(
        return_value=MagicMock(tool_calls=[{"name": "query_lipitor"}])
    )
    model.bind_tools.return_value = bound
    gateway._models[
        (  # type: ignore[assignment]
            "default",
            settings.llm_model,
            None,
            settings.reasoning_effort,
        )
    ] = model
    return gateway, model


@pytest.mark.asyncio
async def test_aroute_tools_binds_every_configured_collection() -> None:
    gateway, model = _gateway_with_fake_model()
    calls = await gateway.aroute_tools("What are Lipitor side effects?")
    assert calls == [{"name": "query_lipitor"}]
    (tools,), _ = model.bind_tools.call_args
    names = {tool["function"]["name"] for tool in tools}
    assert names == {"query_lipitor", "query_metformin"}


class _SlowStructured:
    """Simulates a 0.2s LLM call; sync .invoke would serialize two of them."""

    def __init__(self) -> None:
        async def _slow(_messages: Any, **_: Any) -> _Outcome:
            await asyncio.sleep(0.2)
            return _Outcome(ok=True)

        runnable = MagicMock()
        runnable.ainvoke = AsyncMock(side_effect=_slow)
        self.with_structured_output = MagicMock(return_value=runnable)


class _Outcome(BaseModel):
    ok: bool = True


@pytest.mark.asyncio
async def test_astructured_calls_run_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = GraphSettings.from_env()
    gateway = LangChainLLMGateway(PrivacySanitizer(), settings=settings)
    slow_model = _SlowStructured()
    monkeypatch.setattr(
        gateway,
        "chat_model",
        lambda _tier, _temperature=None: slow_model,
    )

    started = time.perf_counter()
    results = await asyncio.gather(
        gateway.astructured(
            "decompose_query", _Outcome, temperature=0.1, user_query="slow, please"
        ),
        gateway.astructured(
            "decompose_query", _Outcome, temperature=0.1, user_query="slow, please"
        ),
    )
    elapsed = time.perf_counter() - started
    assert all(isinstance(r, _Outcome) for r in results)
    assert elapsed < 0.35, (
        f"calls serialized: {elapsed:.2f}s (expected ~0.2s concurrent)"
    )


def test_settings_carry_the_legacy_collections() -> None:
    settings = GraphSettings.from_env()
    assert list(settings.collection_names) == ["Lipitor", "Metformin"]
