"""Regression: Weaviate connect failures must not poison the Resources singleton."""

from __future__ import annotations

from typing import Any

import pytest

from healthcare_rag.graph import resources as resources_module
from healthcare_rag.graph.resources import Resources

from .conftest import make_settings


async def test_failed_connect_does_not_poison_the_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    class FailingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.connected = False

        async def connect(self) -> None:
            nonlocal attempts
            attempts += 1
            raise ConnectionError("weaviate unavailable")

        def is_connected(self) -> bool:
            return self.connected

    monkeypatch.setattr(resources_module, "WeaviateAsyncClient", FailingClient)
    resources = Resources(make_settings())

    with pytest.raises(ConnectionError):
        await resources.weaviate()
    assert attempts == 1
    assert resources._weaviate is None

    with pytest.raises(ConnectionError):
        await resources.weaviate()
    assert attempts == 2
    assert resources._weaviate is None


async def test_aclose_nulls_weaviate_so_resources_is_not_reused_half_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connects = 0

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.connected = False

        async def connect(self) -> None:
            nonlocal connects
            connects += 1
            self.connected = True

        def is_connected(self) -> bool:
            return self.connected

        async def close(self) -> None:
            self.connected = False

    monkeypatch.setattr(resources_module, "WeaviateAsyncClient", FakeClient)
    resources = Resources(make_settings())

    first = await resources.weaviate()
    assert first.is_connected()

    await resources.aclose()
    assert resources._weaviate is None

    second = await resources.weaviate()
    assert connects == 2
    assert second is not first
    assert second.is_connected()
