from __future__ import annotations

import json
from pathlib import Path

from langgraph_api.auth.studio_user import StudioUser
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from healthcare_rag.agent.perimeter_middleware import MemberPerimeterMiddleware


class _Principal:
    """ASGI shim that injects the authenticated principal the way auth_first does."""

    def __init__(self, app, user) -> None:
        self.app = app
        self.user = user

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            scope["user"] = self.user
        await self.app(scope, receive, send)


async def _echo(request: Request) -> JSONResponse:
    return JSONResponse({"path": request.url.path})


def _client(user) -> TestClient:
    app = Starlette(
        routes=[Route("/assistants/search", _echo, methods=["POST"])],
        middleware=[Middleware(MemberPerimeterMiddleware)],
    )
    return TestClient(_Principal(app, user))


def test_langgraph_config_keeps_studio_auth_enabled() -> None:
    config = json.loads(Path("langgraph.json").read_text())

    assert config["auth"]["disable_studio_auth"] is False


def test_studio_principal_bypasses_the_member_perimeter() -> None:
    client = _client(StudioUser("langsmith-user-1", is_authenticated=True))

    response = client.post("/assistants/search", json={})

    assert response.status_code == 200
    assert response.json() == {"path": "/assistants/search"}


def test_member_principal_is_still_held_to_the_contract_routes() -> None:
    client = _client({"identity": "member-1", "role": "member"})

    response = client.post("/assistants/search", json={})

    assert response.status_code == 403
    assert response.json() == {"detail": "Route is not available"}


def test_anonymous_request_is_still_unauthorized() -> None:
    client = _client(None)

    assert client.post("/assistants/search", json={}).status_code == 401
