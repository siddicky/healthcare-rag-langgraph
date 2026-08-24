"""CORS matrix — hermetic, STUB auth that does NOT admit OPTIONS."""
from __future__ import annotations

import importlib
import sys
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import httpx
import pytest
from langgraph_sdk import Auth

from server.config import ServerConfig


def _stub_auth() -> Auth:
    """Production-like stub: OPTIONS without valid bearer must 401."""
    auth = Auth()

    @auth.authenticate
    async def authenticate(
        method: str,
        path: str,
        headers: dict[bytes, bytes],
        authorization: str | None,
    ) -> dict:
        del path
        # Do NOT special-case OPTIONS — must behave like production supabase_bearer.
        if b"x-api-key" in headers and b"x-internal-token" in headers:
            return {"identity": "internal", "role": "internal"}
        if authorization == "Bearer member":
            return {"identity": "member-a", "role": "member"}
        if authorization == "Bearer internal":
            return {"identity": "internal", "role": "internal"}
        raise Auth.exceptions.HTTPException(status_code=401)

    @auth.on
    async def allow(ctx: Auth.types.AuthContext, value: Auth.types.on.value) -> Auth.types.HandlerResult:
        del ctx, value

    return auth


def _cfg() -> ServerConfig:
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
    cors_origins: str,
    coach_origins: str | None = None,
) -> AsyncGenerator[tuple[httpx.AsyncClient, object], None]:
    # Hermetic: no SERVER_LOCAL_DEV, no OPENAI key, env via monkeypatch
    monkeypatch.delenv("SERVER_LOCAL_DEV", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", cors_origins)
    if coach_origins is None:
        coach_origins = cors_origins
    monkeypatch.setenv("COACH_ALLOWED_ORIGINS", coach_origins)

    # Reload custom app so its inner CORSMiddleware captures current COACH_ALLOWED_ORIGINS
    import healthcare_rag.agent.http_app as custom
    import server.app as app_module

    # Ensure reload picks up new env
    if "healthcare_rag.agent.http_app" in sys.modules:
        importlib.reload(custom)
    else:
        importlib.import_module("healthcare_rag.agent.http_app")

    monkeypatch.setattr(custom, "validate_feedback_project", lambda: "fixture")
    monkeypatch.setattr(app_module, "load_auth_instance", lambda _p: _stub_auth())
    app = app_module.create_app(_cfg())
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client, app


# 1 — preflight POST on /threads, no credentials → 200 + ACAO echo + allow-credentials
@pytest.mark.anyio
async def test_cors_preflight_post_threads_no_credentials(monkeypatch: pytest.MonkeyPatch):
    origin = "https://coach.test"
    async with _client(monkeypatch, cors_origins=origin) as (client, _app):
        r = await client.options(
            "/threads",
            headers={
                "origin": origin,
                "access-control-request-method": "POST",
                "access-control-request-headers": "authorization,content-type",
            },
        )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == origin
    assert r.headers.get("access-control-allow-credentials") == "true"


# 2 — preflight PUT on /store/items and PATCH on /threads/{id} → 200, methods listed
@pytest.mark.anyio
async def test_cors_preflight_put_and_patch_methods(monkeypatch: pytest.MonkeyPatch):
    origin = "https://coach.test"
    async with _client(monkeypatch, cors_origins=origin) as (client, _app):
        r_put = await client.options(
            "/store/items",
            headers={
                "origin": origin,
                "access-control-request-method": "PUT",
                "access-control-request-headers": "authorization,content-type",
            },
        )
        r_patch = await client.options(
            "/threads/00000000-0000-0000-0000-000000000001",
            headers={
                "origin": origin,
                "access-control-request-method": "PATCH",
                "access-control-request-headers": "authorization,content-type",
            },
        )
    for r, method in ((r_put, "PUT"), (r_patch, "PATCH")):
        assert r.status_code == 200, f"{method} preflight failed: {r.status_code} {r.text}"
        allow_methods = r.headers.get("access-control-allow-methods", "")
        # Starlette returns the configured allow_methods list
        assert method in allow_methods
        assert r.headers.get("access-control-allow-origin") == origin


# 3 — cross-origin GET /threads/{id} with invalid bearer → 401 WITH ACAO
@pytest.mark.anyio
async def test_cors_cross_origin_get_invalid_bearer_has_acao(monkeypatch: pytest.MonkeyPatch):
    origin = "https://coach.test"
    tid = str(uuid.uuid4())
    async with _client(monkeypatch, cors_origins=origin) as (client, _app):
        r = await client.get(
            f"/threads/{tid}",
            headers={"origin": origin, "authorization": "Bearer invalid"},
        )
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") == origin
    assert r.headers.get("access-control-allow-credentials") == "true"


# 4 — cross-origin DELETE happy path with valid principal → success carries ACAO
@pytest.mark.anyio
async def test_cors_cross_origin_delete_happy_path_carries_acao(monkeypatch: pytest.MonkeyPatch):
    origin = "https://coach.test"
    async with _client(monkeypatch, cors_origins=origin) as (client, _app):
        created = await client.post(
            "/threads", json={}, headers={"authorization": "Bearer internal"}
        )
        assert created.status_code in (200, 201)
        tid = created.json().get("thread_id")
        assert tid
        r = await client.delete(
            f"/threads/{tid}",
            headers={"origin": origin, "authorization": "Bearer internal"},
        )
    # DELETE returns 204 on success
    assert r.status_code == 204
    # Even 204 must carry ACAO for browser to expose it
    assert r.headers.get("access-control-allow-origin") == origin


# 5 — disallowed origin (https://evil.test) → no ACAO anywhere
@pytest.mark.anyio
async def test_cors_disallowed_origin_no_acao(monkeypatch: pytest.MonkeyPatch):
    allowed = "https://coach.test"
    evil = "https://evil.test"
    async with _client(monkeypatch, cors_origins=allowed) as (client, _app):
        preflight = await client.options(
            "/threads",
            headers={
                "origin": evil,
                "access-control-request-method": "POST",
                "access-control-request-headers": "authorization,content-type",
            },
        )
        simple = await client.get(
            f"/threads/{uuid.uuid4()}",
            headers={"origin": evil, "authorization": "Bearer invalid"},
        )
    # Starlette CORSMiddleware returns 400 for disallowed preflight
    assert "access-control-allow-origin" not in preflight.headers
    assert "access-control-allow-origin" not in simple.headers
    # simple still 401 (auth) but without ACAO — browser will block
    assert simple.status_code == 401


# 6 — plain OPTIONS without Origin/ACRM → not preflight (still 401 without credentials)
@pytest.mark.anyio
async def test_cors_plain_options_without_origin_not_preflight(monkeypatch: pytest.MonkeyPatch):
    origin = "https://coach.test"
    async with _client(monkeypatch, cors_origins=origin) as (client, _app):
        r = await client.options("/threads")
    # No Origin + no Access-Control-Request-Method → not a CORS preflight,
    # so it falls through to auth/routing → 401 without credentials.
    # (If it were treated as preflight it would be 200)
    assert r.status_code == 401
    assert "access-control-allow-origin" not in r.headers


# 7 — /coach/uploads preflight → 200 with exactly one ACAO (do not assert Vary)
@pytest.mark.anyio
async def test_cors_coach_uploads_preflight_single_acao(monkeypatch: pytest.MonkeyPatch):
    origin = "https://coach.test"
    async with _client(monkeypatch, cors_origins=origin, coach_origins=origin) as (client, _app):
        r = await client.options(
            "/coach/uploads",
            headers={
                "origin": origin,
                "access-control-request-method": "POST",
                "access-control-request-headers": "authorization,content-type",
            },
        )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == origin
    # Exactly one ACAO header — outer and inner CORS use MutableHeaders.__setitem__
    # which deletes then appends, so no duplication.
    vals = r.headers.get_list("access-control-allow-origin")
    assert len(vals) == 1, f"expected exactly one ACAO, got {vals}"
    # Do NOT assert on Vary — it legitimately concatenates to "Origin, Origin"


# 8 — https://smith.langchain.com configured in CORS_ALLOW_ORIGINS → accepted on native routes
@pytest.mark.anyio
async def test_cors_smith_langchain_origin_accepted(monkeypatch: pytest.MonkeyPatch):
    origin = "https://smith.langchain.com"
    # Include smith plus another for realism
    cors = f"{origin},https://coach.test"
    async with _client(monkeypatch, cors_origins=cors, coach_origins=cors) as (client, _app):
        r = await client.options(
            "/threads",
            headers={
                "origin": origin,
                "access-control-request-method": "POST",
                "access-control-request-headers": "authorization,content-type",
            },
        )
        simple = await client.get(
            f"/threads/{uuid.uuid4()}",
            headers={"origin": origin, "authorization": "Bearer invalid"},
        )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == origin
    # simple CORS with smith origin must also carry ACAO even on 401
    assert simple.status_code == 401
    assert simple.headers.get("access-control-allow-origin") == origin
