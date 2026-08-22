from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Mapping
from typing import Final, Protocol, TypeAlias, override, runtime_checkable
from uuid import UUID

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .cleanup import clear_cleanup_marker, prepare_thread_deletion
from .perimeter import PerimeterDenied, project_state, validate_member_request
from .uploads import UPLOAD_TTL_MINUTES, reservation_id

JSONValue: TypeAlias = JsonValue
JSONBody: TypeAlias = dict[str, JSONValue] | list[JSONValue] | None
JSON_ADAPTER: Final = TypeAdapter(JsonValue)


@runtime_checkable
class _BodyStream(Protocol):
    body_iterator: AsyncIterator[bytes]


async def _read_json_body(request: Request) -> JSONBody:
    body = await request.body()
    if not body:
        return None
    try:
        value = JSON_ADAPTER.validate_json(body)
    except ValidationError:
        raise PerimeterDenied("Malformed JSON", status_code=400) from None
    if isinstance(value, dict | list):
        return value
    raise PerimeterDenied("JSON object or array required", status_code=400)


async def _consume_attachment(request: Request, body: JSONBody) -> None:
    from langgraph_api.store import get_store

    if not isinstance(body, Mapping):
        return
    run_input = body.get("input")
    if not isinstance(run_input, Mapping):
        return
    upload_id = run_input.get("attachment_id")
    if not isinstance(upload_id, str):
        return
    identity = request.user.identity
    item_key = reservation_id(str(UUID(upload_id)))
    store = await get_store()
    namespace = ("users", identity, "upload_registry")
    item = await store.aget(namespace, item_key, refresh_ttl=False)
    thread_id = request.url.path.split("/")[2]
    valid = (
        item is not None
        and item.value.get("owner") == identity
        and item.value.get("intended_thread") == thread_id
        and item.value.get("status") == "done"
        and item.value.get("admitted") is not True
        and isinstance(item.value.get("expires_at"), int | float)
        and item.value["expires_at"] > time.time()
    )
    if not valid:
        raise PerimeterDenied("Attachment is unavailable")
    assert item is not None
    updated = dict(item.value)
    # `admitted`, not `consumed`: claim_document rejects consumed records,
    # and this middleware runs before the graph, so an eager consumed write
    # would make every first claim fail. Re-admission stays a 403 here.
    updated["admitted"] = True
    await store.aput(namespace, item_key, updated, index=False, ttl=UPLOAD_TTL_MINUTES)


async def _owns_copy_source(request: Request) -> bool:
    thread_id = request.url.path.split("/")[2]
    async with httpx.AsyncClient(base_url=str(request.base_url), timeout=5.0) as client:
        response = await client.get(
            f"/threads/{thread_id}",
            headers={"authorization": request.headers.get("authorization", "")},
        )
    return response.status_code == 200


async def _thread_exists_for_member(request: Request) -> bool:
    thread_id = request.url.path.split("/")[2]
    async with httpx.AsyncClient(base_url=str(request.base_url), timeout=5.0) as client:
        response = await client.get(
            f"/threads/{thread_id}",
            headers={"authorization": request.headers.get("authorization", "")},
        )
    return response.status_code == 200


class MemberPerimeterMiddleware(BaseHTTPMiddleware):
    @override
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path == "/ok":
            return await call_next(request)
        user = request.scope.get("user")
        if user is None:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        role = user.get("role")
        if role == "internal":
            if request.url.path == "/coach/internal/version":
                return await call_next(request)
            if request.url.path.startswith("/coach/"):
                return JSONResponse({"detail": "Forbidden"}, status_code=403)
            return await call_next(request)
        body: JSONBody = None
        if (
            request.method in {"POST", "PATCH", "PUT"}
            and request.url.path != "/coach/uploads"
        ):
            try:
                body = await _read_json_body(request)
            except PerimeterDenied as exc:
                return JSONResponse({"detail": exc.reason}, status_code=exc.status_code)
        try:
            validate_member_request(
                request.method,
                request.scope.get("raw_path", request.url.path.encode()).decode(
                    "latin-1"
                ),
                request.url.query,
                body,
            )
            if request.method == "POST" and request.url.path == "/threads":
                body = {"metadata": {"user_id": request.user.identity}}
                request._body = json.dumps(body).encode()
            if request.url.path.endswith("/copy") and not await _owns_copy_source(
                request
            ):
                return JSONResponse({"detail": "Forbidden"}, status_code=403)
            deleting_thread = (
                request.method == "DELETE" and request.url.path.count("/") == 2
            )
            if deleting_thread:
                if not await _thread_exists_for_member(request):
                    return JSONResponse({"detail": "Forbidden"}, status_code=403)
                cleanup = await prepare_thread_deletion(request)
                if not cleanup.ready:
                    return JSONResponse(
                        {"detail": cleanup.notice},
                        status_code=503,
                    )
            if request.url.path.endswith("/runs/stream"):
                await _consume_attachment(request, body)
        except PerimeterDenied as exc:
            return JSONResponse({"detail": exc.reason}, status_code=exc.status_code)
        except ValueError:
            return JSONResponse({"detail": "Invalid identifier"}, status_code=403)
        response = await call_next(request)
        if deleting_thread:
            if not await _thread_exists_for_member(request):
                await clear_cleanup_marker(request)
                return Response(status_code=204)
            return response
        if response.status_code >= 400:
            return response
        if request.method == "GET" and request.url.path.endswith("/state"):
            if not isinstance(response, _BodyStream):
                return JSONResponse(
                    {"detail": "Unsafe state response"}, status_code=500
                )
            raw = b"".join([chunk async for chunk in response.body_iterator])
            try:
                projected = project_state(json.loads(raw))
            except (json.JSONDecodeError, PerimeterDenied):
                return JSONResponse(
                    {"detail": "Unsafe state response"}, status_code=500
                )
            return JSONResponse(projected, status_code=response.status_code)
        if request.method == "POST" and request.url.path.endswith("/copy"):
            if not isinstance(response, _BodyStream):
                return JSONResponse(
                    {"detail": "Invalid copy response"}, status_code=502
                )
            raw = b"".join([chunk async for chunk in response.body_iterator])
            try:
                copied = json.loads(raw)
            except json.JSONDecodeError:
                return JSONResponse(
                    {"detail": "Invalid copy response"}, status_code=502
                )
            metadata = copied.get("metadata") if isinstance(copied, Mapping) else None
            if (
                not isinstance(metadata, Mapping)
                or metadata.get("user_id") != request.user.identity
            ):
                return JSONResponse(
                    {"detail": "Copied thread ownership mismatch"}, status_code=502
                )
            return JSONResponse(copied, status_code=response.status_code)
        return response


__all__ = ["MemberPerimeterMiddleware"]
