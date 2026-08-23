"""Regression: Weaviate connect failures must not poison the Resources singleton."""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, override

import pytest

from healthcare_rag.graph import resources as resources_module
from healthcare_rag.graph.resources import Resources

from .conftest import make_settings


@contextmanager
def _openai_response_server() -> Generator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.dumps(
                {
                    "id": "test-completion",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "fake-default",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "fresh"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            _ = self.wfile.write(body)

        @override
        def log_message(self, format: str, *_args: object) -> None:
            del format

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        yield f"http://{host}:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_aclose_rebuilds_usable_sync_and_async_model_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with _openai_response_server() as base_url:
        monkeypatch.setenv("OPENAI_BASE_URL", base_url)
        resources = Resources(make_settings())
        assert resources.gateway._privacy is resources.privacy
        model = resources.gateway.chat_model("default")

        assert model.invoke("first").content == "fresh"
        assert (await model.ainvoke("first")).content == "fresh"

        await resources.aclose()
        await resources.aclose()

        replacement_model = resources.gateway.chat_model("default")

        assert replacement_model.invoke("second").content == "fresh"
        assert (await replacement_model.ainvoke("second")).content == "fresh"
        assert replacement_model is not model
        assert resources.gateway._privacy is resources.privacy
        await resources.aclose()


@pytest.mark.asyncio
async def test_aclose_skips_injected_gateway_without_close() -> None:
    class GatewayDouble:
        pass

    resources = Resources(make_settings())
    resources.__dict__["_gateway"] = GatewayDouble()

    await resources.aclose()

    assert resources._gateway is None
