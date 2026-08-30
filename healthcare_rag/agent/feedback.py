from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import ClassVar, Final, TypeAlias
from uuid import UUID

from anyio import to_thread
from langsmith import Client
from langsmith.utils import LangSmithError, LangSmithNotFoundError
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
from .self_call import self_client

JSONValue: TypeAlias = JsonValue
JSON_ADAPTER: Final = TypeAdapter(dict[str, JsonValue])

LC_RUN_ID_PREFIX: Final = "lc_run--"


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


def _model_run_id(message: Mapping[str, JSONValue], message_id: str) -> str | None:
    """Best-effort LangSmith run id of the model run that authored a message.

    ``to_safe_message`` reconstructs every persisted message without
    ``response_metadata``, so the durable carrier is the message id itself:
    langchain-core stamps model-authored messages ``lc_run--<run_id>``
    (``LC_ID_PREFIX`` in ``langchain_core/language_models/chat_models.py``),
    where the suffix is the LangSmith id of that model run. The metadata
    lookup stays first for any future projection that preserves it.
    """
    metadata = message.get("response_metadata")
    if isinstance(metadata, Mapping):
        value = metadata.get("run_id")
        if isinstance(value, str) and value:
            return value
    if not message_id.startswith(LC_RUN_ID_PREFIX):
        return None
    try:
        return str(UUID(message_id[len(LC_RUN_ID_PREFIX) :]))
    except ValueError:
        return None


def _resolve_trace(client: Client, run_id: str) -> tuple[str, str] | None:
    """Resolve ``(trace_id, session_id)`` for a run; ``None`` when unknown.

    A missing run (tracing off for that turn, retention expired) downgrades
    the click to store-only; every other LangSmith error propagates and the
    route answers 502.
    """
    try:
        run = client.read_run(run_id)
    except LangSmithNotFoundError:
        return None
    return str(run.trace_id), str(run.session_id)


async def post_feedback(request: Request) -> Response:
    from langgraph_api.store import get_store

    try:
        payload = FeedbackRequest.model_validate(await request.json())
    except (ValidationError, ValueError):
        return JSONResponse({"detail": "Invalid feedback"}, status_code=400)
    if payload.score not in {-1, 1}:
        return JSONResponse({"detail": "Invalid feedback"}, status_code=400)
    authorization = request.headers.get("authorization", "")
    async with self_client(request, timeout=5.0) as client:
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
    model_run_id = _model_run_id(message, payload.message_id)
    record: dict[str, JSONValue] = {
        "thread_id": str(payload.thread_id),
        "message_id": payload.message_id,
        "score": payload.score,
        "comment": comment,
    }
    if model_run_id is not None:
        record["run_id"] = model_run_id
    store = await get_store()
    await store.aput(
        ("users", identity, "feedback"),
        payload.message_id,
        record,
        index=False,
    )

    api_key = os.getenv("LANGSMITH_API_KEY", "")
    endpoint = os.getenv("LANGSMITH_ENDPOINT") or os.getenv("LANGCHAIN_ENDPOINT")
    feedback_client = (
        Client(api_key=api_key, api_url=endpoint)
        if endpoint
        else Client(api_key=api_key)
    )
    trace_id: str | None = None
    if model_run_id is not None:
        try:
            resolved = await to_thread.run_sync(
                lambda: _resolve_trace(feedback_client, model_run_id)
            )
            if resolved is not None:
                trace_id, session_id = resolved
                _ = await to_thread.run_sync(
                    lambda: feedback_client.create_feedback(
                        trace_id=trace_id,
                        session_id=session_id,
                        key="member_feedback",
                        score=payload.score,
                        comment=comment,
                    )
                )
        except LangSmithError:
            return JSONResponse(
                {"detail": "Feedback backend unavailable"}, status_code=502
            )
    body: dict[str, JSONValue] = {"ok": True, "attached": trace_id is not None}
    if trace_id is not None:
        body["trace_id"] = trace_id
    return JSONResponse(body, status_code=201)


__all__: Final = ["post_feedback"]
