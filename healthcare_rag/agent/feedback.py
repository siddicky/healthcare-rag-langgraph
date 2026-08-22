from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import ClassVar, Final, TypeAlias
from uuid import UUID

import httpx
from anyio import to_thread
from langsmith import Client
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from healthcare_rag.processors.safety import scrub_phi

JSONValue: TypeAlias = JsonValue
JSON_ADAPTER: Final = TypeAdapter(dict[str, JsonValue])


class FeedbackRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    thread_id: UUID
    message_id: str = Field(min_length=1)
    score: int
    comment: str | None = Field(default=None, max_length=500)


def _message(messages: JSONValue, message_id: str) -> dict[str, JSONValue] | None:
    if not isinstance(messages, Sequence) or isinstance(
        messages, str | bytes | bytearray
    ):
        return None
    for message in messages:
        if isinstance(message, dict) and message.get("id") == message_id:
            return message
    return None


def _run_id(message: Mapping[str, JSONValue]) -> str | None:
    metadata = message.get("response_metadata")
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get("run_id")
    return value if isinstance(value, str) and value else None


async def post_feedback(request: Request) -> Response:
    from langgraph_api.store import get_store

    try:
        payload = FeedbackRequest.model_validate(await request.json())
    except (ValidationError, ValueError):
        return JSONResponse({"detail": "Invalid feedback"}, status_code=400)
    if payload.score not in {-1, 1}:
        return JSONResponse({"detail": "Invalid feedback"}, status_code=400)
    authorization = request.headers.get("authorization", "")
    async with httpx.AsyncClient(base_url=str(request.base_url), timeout=5.0) as client:
        thread = await client.get(
            f"/threads/{payload.thread_id}",
            headers={"authorization": authorization},
        )
        if thread.status_code != 200:
            return JSONResponse({"detail": "Thread not found"}, status_code=403)
        state = await client.get(
            f"/threads/{payload.thread_id}/state",
            headers={"authorization": authorization},
        )
    if state.status_code != 200:
        return JSONResponse({"detail": "Thread state unavailable"}, status_code=403)
    try:
        state_payload = JSON_ADAPTER.validate_python(state.json())
    except ValidationError:
        return JSONResponse({"detail": "Thread state unavailable"}, status_code=502)
    values = state_payload.get("values")
    messages: JSONValue = values.get("messages") if isinstance(values, dict) else None
    message = _message(messages, payload.message_id)
    if message is None:
        return JSONResponse({"detail": "Message not found"}, status_code=403)

    identity = request.user.identity
    comment = scrub_phi(payload.comment)[0] if payload.comment else None
    record: dict[str, JSONValue] = {
        "thread_id": str(payload.thread_id),
        "message_id": payload.message_id,
        "score": payload.score,
        "comment": comment,
    }
    run_id = _run_id(message)
    if run_id is not None:
        record["run_id"] = run_id
    store = await get_store()
    await store.aput(
        ("users", identity, "feedback"),
        payload.message_id,
        record,
        index=False,
    )

    api_key = os.getenv("LANGSMITH_API_KEY", "")
    project_id = os.getenv("LANGSMITH_FEEDBACK_PROJECT_ID", "")
    try:
        _ = UUID(project_id)
    except ValueError:
        return JSONResponse(
            {"detail": "Feedback project is not configured"}, status_code=503
        )
    endpoint = os.getenv("LANGSMITH_ENDPOINT") or os.getenv("LANGCHAIN_ENDPOINT")
    feedback_client = (
        Client(api_key=api_key, api_url=endpoint)
        if endpoint
        else Client(api_key=api_key)
    )
    extra: dict[str, JSONValue] = {
        "thread_id": str(payload.thread_id),
        "message_id": payload.message_id,
        "run_id_if_available": run_id,
    }
    await to_thread.run_sync(
        lambda: feedback_client.create_feedback(
            project_id=project_id,
            key="member_feedback",
            score=payload.score,
            comment=comment,
            extra=extra,
        )
    )
    return JSONResponse({"ok": True}, status_code=201)


__all__: Final = ["post_feedback"]
