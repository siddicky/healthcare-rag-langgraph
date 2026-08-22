from __future__ import annotations

import hmac
import os
import re
from collections.abc import Mapping
from typing import Final, Literal, TypeAlias

import httpx
from langgraph_sdk import Auth

AuthDecision: TypeAlias = Auth.types.HandlerResult


class Principal(Auth.types.MinimalUserDict, total=False):
    role: Literal["member", "internal", "preflight"]
    sub_role: Literal["reservation", "cron_ops"]
    internal_owner: str


SUPABASE_TRANSPORT: httpx.AsyncBaseTransport | None = None
_UUID: Final = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_RESERVATION_ITEM: Final = re.compile(rf"^/threads/{_UUID}$")
_CRON_CREATE: Final = re.compile(rf"^/threads/{_UUID}/runs/crons$")
_CRON_ITEM: Final = re.compile(rf"^/runs/crons/{_UUID}$")
_RESERVATION_KEYS: Final = frozenset({"resource_kind", "owner", "intended_thread"})

auth = Auth()


def _header(headers: Mapping[bytes, bytes], name: bytes) -> str | None:
    value = headers.get(name) or headers.get(name.title())
    return value.decode("utf-8") if value is not None else None


def _matches_secret(candidate: str | None, expected: str) -> bool:
    return bool(candidate and expected and hmac.compare_digest(candidate, expected))


def _internal_sub_role(
    method: str, path: str
) -> Literal["reservation", "cron_ops"] | None:
    if (method, path) in {("POST", "/threads"), ("POST", "/threads/search")}:
        return "reservation"
    if method in {"GET", "DELETE"} and _RESERVATION_ITEM.fullmatch(path):
        return "reservation"
    if method == "POST" and (
        _CRON_CREATE.fullmatch(path) or path == "/runs/crons/search"
    ):
        return "cron_ops"
    if method in {"PATCH", "DELETE"} and _CRON_ITEM.fullmatch(path):
        return "cron_ops"
    return None


def _unauthorized() -> Auth.exceptions.HTTPException:
    return Auth.exceptions.HTTPException(status_code=401, detail="Unauthorized")


@auth.authenticate
async def supabase_bearer(
    method: str,
    path: str,
    headers: dict[bytes, bytes],
    authorization: str | None,
) -> Principal:
    if method.upper() == "OPTIONS":
        return {
            "identity": "cors-preflight",
            "is_authenticated": True,
            "role": "preflight",
        }
    platform_key = os.getenv("LANGSMITH_API_KEY", "")
    internal_token = os.getenv("COACH_INTERNAL_TOKEN", "")
    supplied_platform = _header(headers, b"x-api-key")
    supplied_internal = _header(headers, b"x-internal-token")
    has_internal_header = supplied_platform is not None or supplied_internal is not None
    if has_internal_header:
        if not (
            _matches_secret(supplied_platform, platform_key)
            and _matches_secret(supplied_internal, internal_token)
        ):
            raise _unauthorized()
        sub_role = _internal_sub_role(method.upper(), path)
        owner = _header(headers, b"x-internal-owner")
        if sub_role == "reservation" and owner is not None:
            raise Auth.exceptions.HTTPException(status_code=403, detail="Forbidden")
        principal: Principal = {
            "identity": "internal",
            "is_authenticated": True,
            "role": "internal",
        }
        if sub_role is not None:
            principal["sub_role"] = sub_role
        if sub_role == "cron_ops" and owner:
            principal["internal_owner"] = owner
        return principal

    if authorization is None or not authorization.startswith("Bearer "):
        raise _unauthorized()
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not service_key:
        raise _unauthorized()
    try:
        async with httpx.AsyncClient(
            transport=SUPABASE_TRANSPORT,
            timeout=httpx.Timeout(5.0),
        ) as client:
            response = await client.get(
                f"{supabase_url}/auth/v1/user",
                headers={"authorization": authorization, "apikey": service_key},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        raise _unauthorized() from None
    identity = payload.get("id") if isinstance(payload, Mapping) else None
    if not isinstance(identity, str) or not identity:
        raise _unauthorized()
    return {
        "identity": identity,
        "is_authenticated": True,
        "role": "member",
    }


@auth.on
async def deny_all(
    ctx: Auth.types.AuthContext, value: Auth.types.on.value
) -> AuthDecision:
    del value
    return None if _is_studio(ctx) else False


def _is_studio(ctx: Auth.types.AuthContext) -> bool:
    """LangSmith Studio principal (only issued while disable_studio_auth is false)."""
    kind = ctx.user["kind"] if "kind" in ctx.user else None  # noqa: SIM401
    return kind == "StudioUser"


def _role(ctx: Auth.types.AuthContext) -> str | None:
    role = ctx.user["role"] if "role" in ctx.user else None  # noqa: SIM401
    return role if isinstance(role, str) else None


def _sub_role(ctx: Auth.types.AuthContext) -> str | None:
    sub_role = ctx.user["sub_role"] if "sub_role" in ctx.user else None  # noqa: SIM401
    return sub_role if isinstance(sub_role, str) else None


@auth.on.threads.create
async def create_thread(
    ctx: Auth.types.AuthContext, value: Auth.types.on.threads.create.value
) -> AuthDecision:
    if _is_studio(ctx):
        return None
    if _role(ctx) == "member":
        value["metadata"] = {"user_id": ctx.user.identity}
        return None
    if _role(ctx) != "internal" or _sub_role(ctx) != "reservation":
        return False
    metadata = value.get("metadata")
    if not isinstance(metadata, Mapping) or frozenset(metadata) != _RESERVATION_KEYS:
        return False
    return bool(
        metadata.get("resource_kind") == "upload_reservation"
        and isinstance(metadata.get("owner"), str)
        and isinstance(metadata.get("intended_thread"), str)
    )


async def _thread_scope(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.threads.read.value | Auth.types.on.threads.search.value,
) -> AuthDecision:
    if _is_studio(ctx):
        return None
    del value
    if _role(ctx) == "member":
        return {"user_id": ctx.user.identity}
    if _role(ctx) == "internal" and _sub_role(ctx) == "reservation":
        return {"resource_kind": "upload_reservation"}
    if _role(ctx) == "internal" and _sub_role(ctx) == "cron_ops":
        owner = ctx.user["internal_owner"] if "internal_owner" in ctx.user else None  # noqa: SIM401
        return {"user_id": owner} if isinstance(owner, str) and owner else False
    return False


auth.on.threads.read(_thread_scope)
auth.on.threads.search(_thread_scope)


@auth.on.threads.delete
async def delete_thread(
    ctx: Auth.types.AuthContext, value: Auth.types.on.threads.delete.value
) -> AuthDecision:
    if _is_studio(ctx):
        return None
    del value
    if _role(ctx) == "member":
        return {"user_id": ctx.user.identity}
    if _role(ctx) == "internal" and _sub_role(ctx) == "reservation":
        return {"resource_kind": "upload_reservation"}
    return False


@auth.on.threads.create_run
async def create_run(
    ctx: Auth.types.AuthContext, value: Auth.types.on.threads.create_run.value
) -> AuthDecision:
    if _is_studio(ctx):
        return None
    run_input = value.get("kwargs", {}).get("input")
    if _role(ctx) == "member":
        if isinstance(run_input, Mapping) and "cron_wake" in run_input:
            return False
        return {"user_id": ctx.user.identity}
    if ctx.user.identity != "internal" or _role(ctx) is not None:
        return False
    if not isinstance(run_input, Mapping) or frozenset(run_input) != {"cron_wake"}:
        return False
    wake = run_input.get("cron_wake")
    if not isinstance(wake, Mapping) or frozenset(wake) != {
        "reminder_id",
        "user_id",
        "thread_id",
        "wake_token",
    }:
        return False
    if str(value.get("thread_id")) != wake.get("thread_id"):
        return False
    user_id = wake.get("user_id")
    return {"user_id": user_id} if isinstance(user_id, str) and user_id else False


@auth.on.assistants.read
async def read_coach_assistant(
    ctx: Auth.types.AuthContext, value: Auth.types.on.assistants.read.value
) -> AuthDecision:
    if _is_studio(ctx):
        return None
    del ctx, value
    return {"graph_id": "coach"}


def _cron_owner(ctx: Auth.types.AuthContext) -> str | None:
    if _role(ctx) != "internal" or _sub_role(ctx) != "cron_ops":
        return None
    owner = ctx.user["internal_owner"] if "internal_owner" in ctx.user else None  # noqa: SIM401
    return owner if isinstance(owner, str) and owner else None


@auth.on.crons.create
async def create_cron(
    ctx: Auth.types.AuthContext, value: Auth.types.on.crons.create.value
) -> AuthDecision:
    if _is_studio(ctx):
        return None
    owner = _cron_owner(ctx)
    payload = value.get("payload")
    metadata = payload.get("metadata") if isinstance(payload, Mapping) else None
    if (
        owner is None
        or not isinstance(metadata, Mapping)
        or metadata.get("user_id") != owner
    ):
        return False
    return {"user_id": owner}


async def _cron_scope(
    ctx: Auth.types.AuthContext,
    value: Auth.types.on.crons.read.value
    | Auth.types.on.crons.search.value
    | Auth.types.on.crons.update.value
    | Auth.types.on.crons.delete.value,
) -> AuthDecision:
    if _is_studio(ctx):
        return None
    del value
    owner = _cron_owner(ctx)
    return {"user_id": owner} if owner is not None else False


auth.on.crons.read(_cron_scope)
auth.on.crons.search(_cron_scope)
auth.on.crons.update(_cron_scope)
auth.on.crons.delete(_cron_scope)


__all__ = ["SUPABASE_TRANSPORT", "Auth", "auth", "supabase_bearer"]
