from __future__ import annotations

import importlib
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import ModuleType
from typing import Any

import httpx
import pytest
from langgraph_sdk import Auth

from server.config import ServerConfig


class Principal(Auth.types.MinimalUserDict, total=False):
    role: str


def _auth() -> Auth:
    auth = Auth()

    @auth.authenticate
    async def authenticate(
        method: str,
        path: str,
        headers: dict[bytes, bytes],
        authorization: str | None,
    ) -> Principal:
        del path
        if method == "OPTIONS":
            return {"identity": "cors-preflight", "role": "preflight"}
        if b"x-api-key" in headers and b"x-internal-token" in headers:
            return {"identity": "internal", "role": "internal"}
        if authorization == "Bearer member":
            return {"identity": "member-a", "role": "member"}
        if authorization == "Bearer internal":
            return {"identity": "internal", "role": "internal"}
        raise Auth.exceptions.HTTPException(status_code=401)

    @auth.on
    async def allow(
        ctx: Auth.types.AuthContext, value: Auth.types.on.value
    ) -> Auth.types.HandlerResult:
        del ctx, value

    return auth


def _config() -> ServerConfig:
    return ServerConfig(
        graphs={},
        auth_path="fixture:auth",
        http_app="./healthcare_rag/agent/http_app.py:app",
        http_flags={},
        store_index={},
        api_version="0.12.6",
    )


@asynccontextmanager
async def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    feedback_error: RuntimeError | None = None,
) -> AsyncGenerator[tuple[httpx.AsyncClient, Any], None]:
    import healthcare_rag.agent.http_app as custom
    import server.app as app_module

    monkeypatch.setattr(app_module, "load_auth_instance", lambda _path: _auth())
    if feedback_error is None:
        monkeypatch.setattr(custom, "validate_feedback_project", lambda: "fixture")
    else:
        monkeypatch.setattr(
            custom,
            "validate_feedback_project",
            lambda: (_ for _ in ()).throw(feedback_error),
        )
    app = app_module.create_app(_config())
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        yield client, app


@pytest.mark.anyio
async def test_custom_route_priority_auth_and_member_allow_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _client(monkeypatch) as (client, _app):
        internal = await client.get(
            "/coach/internal/version", headers={"authorization": "Bearer internal"}
        )
        member_version = await client.get(
            "/coach/internal/version", headers={"authorization": "Bearer member"}
        )
        assistant = await client.get(
            "/assistants/search", headers={"authorization": "Bearer member"}
        )
        unauthenticated = await client.get("/coach/internal/version")

    assert internal.status_code == 200
    assert member_version.status_code == 403
    assert assistant.status_code == 403
    assert unauthenticated.status_code == 401


@pytest.mark.anyio
async def test_native_routes_pass_through_perimeter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _client(monkeypatch) as (client, _app):
        created = await client.post(
            "/threads",
            headers={"authorization": "Bearer member"},
            json={},
        )
        thread_id = created.json()["thread_id"]
        denied_stream = await client.post(
            f"/threads/{thread_id}/runs/stream",
            headers={"authorization": "Bearer member"},
            json={"assistant_id": "coach", "input": {"question": "unsafe extra"}},
        )
        state = await client.get(
            f"/threads/{thread_id}/state",
            headers={"authorization": "Bearer member"},
        )

    assert created.json()["metadata"] == {"user_id": "member-a"}
    assert denied_stream.status_code == 403
    assert state.json() == {"values": {}, "interrupts": []}


@pytest.mark.anyio
async def test_native_cors_allows_only_configured_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://coach.test")
    async with _client(monkeypatch) as (client, _app):
        allowed = await client.options(
            "/threads",
            headers={
                "origin": "https://coach.test",
                "access-control-request-method": "POST",
                "access-control-request-headers": "authorization,content-type",
            },
        )
        denied = await client.options(
            "/threads",
            headers={
                "origin": "https://evil.test",
                "access-control-request-method": "POST",
            },
        )

    assert allowed.headers["access-control-allow-origin"] == "https://coach.test"
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


@pytest.mark.anyio
async def test_custom_lifespan_failure_aborts_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="invalid feedback project"):
        async with _client(
            monkeypatch, feedback_error=RuntimeError("invalid feedback project")
        ):
            pass


@pytest.mark.anyio
async def test_fallback_shim_exposes_version_and_shared_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server import _compat

    original = {
        name: module
        for name, module in sys.modules.items()
        if name in {"langgraph_api", "langgraph_api.store"}
    }
    for name in ("langgraph_api", "langgraph_api.store"):
        sys.modules.pop(name, None)

    def missing(name: str) -> ModuleType:
        raise ModuleNotFoundError(name=name)

    monkeypatch.setattr(_compat, "import_module", missing)
    try:
        async with _client(monkeypatch) as (client, app):
            response = await client.get(
                "/coach/internal/version",
                headers={"authorization": "Bearer internal"},
            )
            from langgraph_api.store import get_store

            shared = await get_store()
            await shared.aput(("compat",), "probe", {"ok": True}, ttl=1)
            assert (await app.state.storage.store.aget(("compat",), "probe")).value == {
                "ok": True
            }
        assert response.json() == {"version": "0.12.6"}
    finally:
        for name in ("langgraph_api", "langgraph_api.store"):
            sys.modules.pop(name, None)
        sys.modules.update(original)


@pytest.mark.anyio
async def test_scheduler_is_cancelled_on_lifespan_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _client(monkeypatch) as (_client_instance, app):
        scheduler = app.state.scheduler_task
        assert not scheduler.done()
    assert scheduler.cancelled()


def test_upload_reservation_uses_shared_shim_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_topology = importlib.import_module("tests.server.test_topology_upload")
    upload_topology.verify_upload_reservation_uses_shared_shim_store(monkeypatch)


def test_custom_routes_retain_priority_before_native_catch_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import healthcare_rag.agent.http_app as custom
    import server.app as app_module

    monkeypatch.setattr(app_module, "load_auth_instance", lambda _path: _auth())
    monkeypatch.setattr(custom, "validate_feedback_project", lambda: "fixture")
    app = app_module.create_app(_config())
    paths = [getattr(route, "path", "") for route in app.routes]
    assert paths.index("/coach/internal/version") < paths.index("/{path:path}")
