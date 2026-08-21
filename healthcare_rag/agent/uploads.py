from __future__ import annotations

import os
import time
from typing import Final, TypeAlias
from uuid import UUID, uuid5

import httpx
from pydantic import JsonValue
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .documents import (
    DOCUMENT_EXTRACTOR,
    UploadRejected,
    read_multipart_upload,
    scrub_proposal,
)

JSONValue: TypeAlias = JsonValue
RESERVATION_NS: Final = UUID("6f503f96-3957-4c48-8554-32beec98fca2")
UPLOAD_TTL_MINUTES: Final = 15
UPLOAD_TTL_SECONDS: Final = UPLOAD_TTL_MINUTES * 60


def reservation_id(upload_id: str) -> str:
    return str(uuid5(RESERVATION_NS, upload_id))


def internal_headers() -> dict[str, str]:
    return {
        "x-api-key": os.getenv("LANGSMITH_API_KEY", ""),
        "x-internal-token": os.getenv("COACH_INTERNAL_TOKEN", ""),
    }


async def _member_get(request: Request, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        base_url=str(request.base_url), timeout=10.0
    ) as client:
        return await client.get(
            path,
            headers={"authorization": request.headers.get("authorization", "")},
        )


def _valid_file_signature(mime_type: str, content: bytearray) -> bool:
    signatures = {
        "application/pdf": b"%PDF",
        "image/jpeg": b"\xff\xd8\xff",
        "image/png": b"\x89PNG\r\n\x1a\n",
    }
    return content.startswith(signatures[mime_type])


async def post_upload(request: Request) -> Response:
    from langgraph_api.store import get_store

    try:
        upload = await read_multipart_upload(request)
    except UploadRejected as exc:
        return JSONResponse({"detail": exc.reason}, status_code=exc.status_code)
    identity = request.user.identity
    buffer = upload.content
    reserved = False
    reservation = reservation_id(upload.upload_id)
    namespace = ("users", identity, "upload_registry")
    record: dict[str, JSONValue] = {
        "owner": identity,
        "intended_thread": upload.thread_id,
        "expires_at": time.time() + UPLOAD_TTL_SECONDS,
        "status": "uploading",
    }
    store = await get_store()
    try:
        owned = await _member_get(request, f"/threads/{upload.thread_id}")
        if owned.status_code != 200:
            return JSONResponse({"detail": "Thread not found"}, status_code=403)
        reserve_payload: dict[str, JSONValue] = {
            "thread_id": reservation,
            "if_exists": "raise",
            "ttl": {"strategy": "delete", "ttl": UPLOAD_TTL_MINUTES},
            "metadata": {
                "resource_kind": "upload_reservation",
                "owner": identity,
                "intended_thread": upload.thread_id,
            },
        }
        async with httpx.AsyncClient(
            base_url=str(request.base_url), timeout=10.0
        ) as client:
            response = await client.post(
                "/threads", headers=internal_headers(), json=reserve_payload
            )
            if response.status_code == 409:
                existing_thread = await client.get(
                    f"/threads/{reservation}", headers=internal_headers()
                )
                metadata = (
                    existing_thread.json().get("metadata", {})
                    if existing_thread.status_code == 200
                    else {}
                )
                if metadata != reserve_payload["metadata"]:
                    return JSONResponse(
                        {"detail": "Upload id belongs to another request"},
                        status_code=409,
                    )
                existing = await store.aget(namespace, reservation, refresh_ttl=False)
                stage = (
                    existing.value.get("status", "uploading")
                    if existing is not None
                    else "uploading"
                )
                return JSONResponse({"stage": stage})
            if response.status_code != 200:
                return JSONResponse(
                    {"detail": "Upload reservation failed"},
                    status_code=response.status_code,
                )
        reserved = True
        await store.aput(
            namespace, reservation, record, index=False, ttl=UPLOAD_TTL_MINUTES
        )
        record["status"] = "scanning"
        await store.aput(
            namespace, reservation, record, index=False, ttl=UPLOAD_TTL_MINUTES
        )
        if not _valid_file_signature(upload.mime_type, buffer):
            raise UploadRejected(
                "File content does not match its media type", status_code=415
            )
        record["status"] = "extracting"
        await store.aput(
            namespace, reservation, record, index=False, ttl=UPLOAD_TTL_MINUTES
        )
        proposal = await DOCUMENT_EXTRACTOR(bytes(buffer), upload.mime_type)
        record["proposal"] = scrub_proposal(proposal, upload.extension, len(buffer))
        record["status"] = "done"
        record["consumed"] = False
        await store.aput(
            namespace, reservation, record, index=False, ttl=UPLOAD_TTL_MINUTES
        )
        return JSONResponse({"stage": "done"}, status_code=201)
    except UploadRejected as exc:
        if reserved:
            record["status"] = "error"
            _ = record.pop("proposal", None)
            await store.aput(
                namespace, reservation, record, index=False, ttl=UPLOAD_TTL_MINUTES
            )
        return JSONResponse(
            {"detail": exc.reason, "stage": "error"}, status_code=exc.status_code
        )
    except (httpx.HTTPError, RuntimeError, TypeError, ValueError):
        if reserved:
            record["status"] = "error"
            _ = record.pop("proposal", None)
            await store.aput(
                namespace, reservation, record, index=False, ttl=UPLOAD_TTL_MINUTES
            )
        return JSONResponse(
            {"detail": "Document extraction failed", "stage": "error"},
            status_code=502,
        )
    finally:
        buffer.clear()


async def get_upload_status(request: Request) -> Response:
    from langgraph_api.store import get_store

    try:
        canonical = str(UUID(request.path_params["upload_id"]))
    except ValueError:
        return JSONResponse({"detail": "Upload not found"}, status_code=404)
    identity = request.user.identity
    store = await get_store()
    item = await store.aget(
        ("users", identity, "upload_registry"),
        reservation_id(canonical),
        refresh_ttl=False,
    )
    if item is None or item.value.get("owner") != identity:
        return JSONResponse({"detail": "Upload not found"}, status_code=404)
    expires_at = item.value.get("expires_at")
    if not isinstance(expires_at, int | float) or expires_at <= time.time():
        return JSONResponse({"detail": "Upload expired"}, status_code=410)
    return JSONResponse({"stage": item.value.get("status")})


__all__ = [
    "RESERVATION_NS",
    "UPLOAD_TTL_MINUTES",
    "get_upload_status",
    "internal_headers",
    "post_upload",
    "reservation_id",
]
