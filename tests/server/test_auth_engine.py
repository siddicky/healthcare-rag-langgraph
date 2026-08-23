from __future__ import annotations

from typing import Any

import httpx
import pytest
from langgraph_sdk import Auth
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from server.auth import (
    AuthMiddleware,
    AuthPolicyEngine,
    ScopeUser,
    merge_scope_filter,
    require_scope_match,
)


def make_stub_auth() -> Auth:
    auth = Auth()

    @auth.authenticate
    async def authenticate(
        method: str,
        path: str,
        headers: dict[bytes, bytes],
        authorization: str | None,
    ) -> Auth.types.MinimalUserDict:
        del path, headers
        if method == "OPTIONS":
            return {"identity": "preflight", "is_authenticated": True}
        if authorization == "Bearer good":
            return {"identity": "member-1", "is_authenticated": True}
        raise Auth.exceptions.HTTPException(status_code=401)

    @auth.on
    async def deny_all(
        ctx: Auth.types.AuthContext, value: Auth.types.on.value
    ) -> Auth.types.HandlerResult:
        del value
        kind = ctx.user["kind"] if "kind" in ctx.user else None
        return None if kind == "StudioUser" else False

    @auth.on.threads.create
    async def create(
        ctx: Auth.types.AuthContext, value: Auth.types.on.threads.create.value
    ) -> Auth.types.HandlerResult:
        value["metadata"] = {"owner": ctx.user.identity}

    @auth.on.threads.read
    async def read(
        ctx: Auth.types.AuthContext, value: Auth.types.on.threads.read.value
    ) -> Auth.types.HandlerResult:
        del value
        return {"owner": ctx.user.identity}

    @auth.on.threads.search
    async def search(
        ctx: Auth.types.AuthContext, value: Auth.types.on.threads.search.value
    ) -> Auth.types.HandlerResult:
        del value
        return {"owner": ctx.user.identity}

    @auth.on.threads.delete
    async def delete(
        ctx: Auth.types.AuthContext, value: Auth.types.on.threads.delete.value
    ) -> Auth.types.HandlerResult:
        del ctx, value
        return False

    @auth.on.threads.create_run
    async def create_run(
        ctx: Auth.types.AuthContext, value: Auth.types.on.threads.create_run.value
    ) -> Auth.types.HandlerResult:
        del ctx, value

    @auth.on.assistants.read
    async def assistant_read(
        ctx: Auth.types.AuthContext, value: Auth.types.on.assistants.read.value
    ) -> Auth.types.HandlerResult:
        del ctx, value

    @auth.on.crons
    async def crons(
        ctx: Auth.types.AuthContext, value: Auth.types.on.crons.value
    ) -> Auth.types.HandlerResult:
        del ctx, value

    @auth.on.store
    async def store(
        ctx: Auth.types.AuthContext, value: dict[str, Any]
    ) -> Auth.types.HandlerResult:
        namespace = value.get("namespace") or ()
        value["namespace"] = (ctx.user.identity, *namespace)

    return auth


def make_app(auth: Auth, *, local_dev: bool = False) -> Starlette:
    async def public(request: Request) -> JSONResponse:
        del request
        return JSONResponse({"public": True})

    async def principal(request: Request) -> JSONResponse:
        user = request.scope["user"]
        return JSONResponse({"identity": user.identity, "kind": user.get("kind")})

    routes = [
        Route("/ok", public),
        Route("/info", public),
        Route("/who", principal, methods=["GET", "OPTIONS"]),
        Route("/custom", principal),
    ]
    return Starlette(routes=routes, middleware=[AuthMiddleware.as_starlette(auth, local_dev)])


@pytest.mark.anyio
async def test_public_routes_bypass_auth_and_custom_routes_do_not() -> None:
    app = make_app(make_stub_auth())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        ok = await client.get("/ok")
        info = await client.get("/info")
        custom = await client.get("/custom")
    assert (ok.status_code, info.status_code, custom.status_code) == (200, 200, 401)


@pytest.mark.anyio
async def test_authentication_maps_dict_and_preflight_principals() -> None:
    app = make_app(make_stub_auth())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        member = await client.get("/who", headers={"authorization": "Bearer good"})
        preflight = await client.options("/who")
    assert member.json()["identity"] == "member-1"
    assert preflight.json()["identity"] == "preflight"


@pytest.mark.anyio
@pytest.mark.parametrize("headers", [{}, {"x-api-key": "opaque<script>value"}])
async def test_local_dev_maps_every_401_to_studio_with_or_without_api_key(
    headers: dict[str, str],
) -> None:
    app = make_app(make_stub_auth(), local_dev=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/who", headers=headers)
    assert response.json() == {
        "identity": "langgraph-studio-user",
        "kind": "StudioUser",
    }


@pytest.mark.anyio
async def test_local_dev_is_off_by_default_and_malformed_auth_is_401() -> None:
    app = make_app(make_stub_auth())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.get("/who")
        malformed = await client.get("/who", headers={"authorization": "opaque<script>"})
    assert (missing.status_code, malformed.status_code) == (401, 401)


@pytest.mark.anyio
async def test_policy_allow_deny_mutation_and_dispatch_matrix() -> None:
    engine = AuthPolicyEngine(make_stub_auth())
    user = ScopeUser({"identity": "member-1"})
    create_value: dict[str, Any] = {}

    create_scope = await engine.run_policy("threads", "create", user, create_value)
    run_scope = await engine.run_policy("threads", "create_run", user, {})
    assistant_scope = await engine.run_policy("assistants", "read", user, {})
    cron_scope = await engine.run_policy("crons", "update", user, {})
    store_value: dict[str, Any] = {"namespace": ("reminders",)}
    store_scope = await engine.run_policy("store", "put", user, store_value)

    assert [create_scope, run_scope, assistant_scope, cron_scope, store_scope] == [
        None,
        None,
        None,
        None,
        None,
    ]
    assert create_value == {"metadata": {"owner": "member-1"}}
    assert store_value["namespace"] == ("member-1", "reminders")
    with pytest.raises(Auth.exceptions.HTTPException) as denied:
        await engine.run_policy("threads", "delete", user, {})
    assert denied.value.status_code == 403


@pytest.mark.anyio
async def test_global_policy_allows_studio_but_denies_unhandled_member() -> None:
    engine = AuthPolicyEngine(make_stub_auth())
    studio = ScopeUser(
        {"identity": "langgraph-studio-user", "kind": "StudioUser"}
    )
    assert await engine.run_policy("assistants", "search", studio, {}) is None
    with pytest.raises(Auth.exceptions.HTTPException) as denied:
        await engine.run_policy(
            "assistants", "search", ScopeUser({"identity": "member-1"}), {}
        )
    assert denied.value.status_code == 403


def test_scope_filter_merges_and_specific_resource_mismatch_is_hidden() -> None:
    merged = merge_scope_filter({"topic": "medication"}, {"owner": "member-1"})
    assert merged == {"topic": "medication", "owner": "member-1"}
    require_scope_match({"owner": "member-1", "topic": "medication"}, {"owner": "member-1"})
    with pytest.raises(Auth.exceptions.HTTPException) as hidden:
        require_scope_match({"owner": "member-2"}, {"owner": "member-1"})
    assert hidden.value.status_code == 404


def test_real_auth_loads_and_is_registered_by_app_factory() -> None:
    from healthcare_rag.agent.auth import auth
    from server.app import create_app

    app = create_app()

    assert app.state.auth_engine.auth is auth
    assert app.state.readiness.checks["auth"] is False
