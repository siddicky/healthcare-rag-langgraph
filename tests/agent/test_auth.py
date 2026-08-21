from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest


@pytest.fixture(autouse=True)
def auth_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SUPABASE_URL", "https://supabase.test")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-secret")
    monkeypatch.setenv("LANGSMITH_API_KEY", "platform-secret")
    monkeypatch.setenv("COACH_INTERNAL_TOKEN", "internal-secret")
    yield


@pytest.mark.asyncio
async def test_member_authentication_uses_supabase_user_id_without_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from healthcare_rag.agent import auth as auth_module

    async def supabase(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer member-token"
        assert request.headers["apikey"] == "service-secret"
        return httpx.Response(
            200,
            json={"id": "member-123", "email": "private@example.test"},
        )

    monkeypatch.setattr(
        auth_module,
        "SUPABASE_TRANSPORT",
        httpx.MockTransport(supabase),
    )

    result = await auth_module.supabase_bearer(
        method="GET",
        path="/threads/member-thread",
        headers={b"authorization": b"Bearer member-token"},
        authorization="Bearer member-token",
    )

    assert result == {
        "identity": "member-123",
        "is_authenticated": True,
        "role": "member",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected_sub_role"),
    [
        ({}, None),
        ({b"x-api-key": b"platform-secret"}, None),
        ({b"x-internal-token": b"internal-secret"}, None),
        (
            {
                b"x-api-key": b"platform-secret",
                b"x-internal-token": b"internal-secret",
            },
            "reservation",
        ),
    ],
)
async def test_internal_principal_requires_both_secrets(
    headers: dict[bytes, bytes],
    expected_sub_role: str | None,
) -> None:
    from healthcare_rag.agent.auth import Auth, supabase_bearer

    if expected_sub_role is None:
        with pytest.raises(Auth.exceptions.HTTPException) as raised:
            await supabase_bearer(
                method="POST",
                path="/threads",
                headers=headers,
                authorization=None,
            )
        assert raised.value.status_code == 401
        return

    result = await supabase_bearer(
        method="POST",
        path="/threads",
        headers=headers,
        authorization=None,
    )
    assert result == {
        "identity": "internal",
        "is_authenticated": True,
        "role": "internal",
        "sub_role": expected_sub_role,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "sub_role"),
    [
        ("POST", "/threads", "reservation"),
        ("POST", "/threads/search", "reservation"),
        ("GET", "/threads/00000000-0000-0000-0000-000000000001", "reservation"),
        ("DELETE", "/threads/00000000-0000-0000-0000-000000000001", "reservation"),
        (
            "POST",
            "/threads/00000000-0000-0000-0000-000000000001/runs/crons",
            "cron_ops",
        ),
        ("POST", "/runs/crons/search", "cron_ops"),
        ("PATCH", "/runs/crons/00000000-0000-0000-0000-000000000001", "cron_ops"),
        ("DELETE", "/runs/crons/00000000-0000-0000-0000-000000000001", "cron_ops"),
        ("GET", "/threads/not-a-uuid/state", None),
    ],
)
async def test_internal_sub_role_is_derived_from_native_signature(
    method: str,
    path: str,
    sub_role: str | None,
) -> None:
    from healthcare_rag.agent.auth import supabase_bearer

    result = await supabase_bearer(
        method=method,
        path=path,
        headers={
            b"x-api-key": b"platform-secret",
            b"x-internal-token": b"internal-secret",
        },
        authorization=None,
    )

    assert result["role"] == "internal"
    assert result.get("sub_role") == sub_role


@pytest.mark.asyncio
async def test_internal_owner_is_rejected_on_reservation_signature() -> None:
    from healthcare_rag.agent.auth import Auth, supabase_bearer

    with pytest.raises(Auth.exceptions.HTTPException) as raised:
        await supabase_bearer(
            method="POST",
            path="/threads",
            headers={
                b"x-api-key": b"platform-secret",
                b"x-internal-token": b"internal-secret",
                b"x-internal-owner": b"member-123",
            },
            authorization=None,
        )

    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_supabase_failure_is_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from healthcare_rag.agent import auth as auth_module

    monkeypatch.setattr(
        auth_module,
        "SUPABASE_TRANSPORT",
        httpx.MockTransport(lambda _request: httpx.Response(503)),
    )

    with pytest.raises(auth_module.Auth.exceptions.HTTPException) as raised:
        await auth_module.supabase_bearer(
            method="GET",
            path="/threads/thread-id",
            headers={b"authorization": b"Bearer invalid"},
            authorization="Bearer invalid",
        )

    assert raised.value.status_code == 401


@pytest.mark.asyncio
async def test_cors_preflight_does_not_require_member_credentials() -> None:
    from healthcare_rag.agent.auth import supabase_bearer

    result = await supabase_bearer(
        method="OPTIONS",
        path="/threads",
        headers={},
        authorization=None,
    )

    assert result == {
        "identity": "cors-preflight",
        "is_authenticated": True,
        "role": "preflight",
    }
