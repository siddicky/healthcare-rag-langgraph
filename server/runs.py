from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping

from langgraph_sdk import Auth
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from server.auth import require_scope_match
from server.run_engine import (
    CancelRequest,
    JSONValue,
    QueueFull,
    RunConflict,
    RunEngine,
    RunMissing,
    RunRequest,
    RunRuntime,
)


def _engine(request: Request) -> RunEngine:
    return request.app.state.run_engine


def _auth_user(request: Request) -> dict[str, JSONValue]:
    user = request.scope.get("user")
    if isinstance(user, Mapping):
        return dict(user)
    identity = getattr(user, "identity", None)
    return {"identity": identity} if isinstance(identity, str) else {}


def _lookup(request: Request) -> tuple[RunEngine, str, str]:
    engine = _engine(request)
    thread_id = request.path_params["thread_id"]
    run_id = request.path_params["run_id"]
    if thread_id not in engine.storage.threads or run_id not in engine.runtime:
        raise RunMissing
    return engine, thread_id, run_id


async def _parse(request: Request) -> RunRequest | JSONResponse:
    try:
        return RunRequest.model_validate(await request.json())
    except (ValidationError, json.JSONDecodeError) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)


async def create_run(request: Request) -> Response:
    engine = _engine(request)
    thread_id = request.path_params["thread_id"]
    if thread_id not in engine.storage.threads:
        return JSONResponse({"detail": "Thread not found"}, status_code=404)
    parsed = await _parse(request)
    if isinstance(parsed, JSONResponse):
        return parsed
    if parsed.assistant_id not in engine.graphs:
        return JSONResponse({"detail": "Assistant not found"}, status_code=404)
    policy_value = {
        "thread_id": thread_id,
        "assistant_id": parsed.assistant_id,
        "kwargs": {
            "input": parsed.input,
            "command": parsed.command.model_dump(mode="json") if parsed.command else None,
            "config": parsed.config,
        },
    }
    try:
        scope_filter = await request.app.state.auth_engine.run_policy(
            "threads", "create_run", request.user, policy_value
        )
        if scope_filter is not None:
            require_scope_match(engine.storage.threads[thread_id], scope_filter)
        record = await engine.submit(thread_id, parsed, auth_user=_auth_user(request))
    except Auth.exceptions.HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    except RunConflict:
        return JSONResponse(
            {"detail": "Thread already has an active run"}, status_code=409
        )
    except QueueFull:
        return JSONResponse(
            {"detail": "Run queue is full"},
            status_code=503,
            headers={"Retry-After": "1"},
        )
    return JSONResponse(record)


async def wait_run(request: Request) -> Response:
    response = await create_run(request)
    if response.status_code != 200:
        return response
    record = json.loads(bytes(response.body))
    runtime = _engine(request).runtime[record["run_id"]]
    await runtime.done.wait()
    return JSONResponse(runtime.output, headers={"X-Run-Id": record["run_id"]})


async def get_run(request: Request) -> Response:
    try:
        engine, _, run_id = _lookup(request)
    except RunMissing:
        return JSONResponse({"detail": "Run not found"}, status_code=404)
    return JSONResponse(engine.storage.runs[run_id])


async def list_runs(request: Request) -> Response:
    engine = _engine(request)
    thread_id = request.path_params["thread_id"]
    if thread_id not in engine.storage.threads:
        return JSONResponse({"detail": "Thread not found"}, status_code=404)
    records = [
        record
        for record in engine.storage.runs.values()
        if record["thread_id"] == thread_id
    ]
    records.sort(key=lambda record: str(record["created_at"]), reverse=True)
    return JSONResponse(records)


async def join_run(request: Request) -> Response:
    try:
        engine, _, run_id = _lookup(request)
    except RunMissing:
        return JSONResponse({"detail": "Run not found"}, status_code=404)
    runtime = engine.runtime[run_id]
    await runtime.done.wait()
    return JSONResponse(runtime.output)


def _frame(event: str, data: JSONValue) -> bytes:
    try:
        compact = json.dumps(data, separators=(",", ":"), default=str)
    except Exception:
        compact = json.dumps(str(data), separators=(",", ":"))
    return f"event: {event}\ndata: {compact}\n\n".encode()


async def _stream(runtime: RunRuntime) -> AsyncIterator[bytes]:
    offset = 0
    while True:
        while offset < len(runtime.events):
            mode, data = runtime.events[offset]
            offset += 1
            yield _frame(mode, data)
        if runtime.done.is_set():
            return
        changed = runtime.changed
        await changed.wait()


async def stream_run(request: Request) -> Response:
    response = await create_run(request)
    if response.status_code != 200:
        return response
    record = json.loads(bytes(response.body))
    runtime = _engine(request).runtime[record["run_id"]]
    return StreamingResponse(_stream(runtime), media_type="text/event-stream")


async def join_stream(request: Request) -> Response:
    try:
        engine, _, run_id = _lookup(request)
    except RunMissing:
        return JSONResponse({"detail": "Run not found"}, status_code=404)
    return StreamingResponse(
        _stream(engine.runtime[run_id]), media_type="text/event-stream"
    )


async def cancel_run(request: Request) -> Response:
    try:
        engine, thread_id, run_id = _lookup(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not body:
            qp = request.query_params
            body = {
                "action": qp.get("action", "rollback"),
                "wait": qp.get("wait") in ("1", "true", "True"),
            }
        parsed = CancelRequest.model_validate(body)
        await engine.cancel(thread_id, run_id, parsed)
    except RunMissing:
        return JSONResponse({"detail": "Run not found"}, status_code=404)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    return JSONResponse(engine.storage.runs[run_id])


routes = [
    Route("/threads/{thread_id}/runs", create_run, methods=["POST"]),
    Route("/threads/{thread_id}/runs/wait", wait_run, methods=["POST"]),
    Route("/threads/{thread_id}/runs/stream", stream_run, methods=["POST"]),
    Route("/threads/{thread_id}/runs/{run_id}", get_run, methods=["GET"]),
    Route("/threads/{thread_id}/runs", list_runs, methods=["GET"]),
    Route("/threads/{thread_id}/runs/{run_id}/join", join_run, methods=["GET"]),
    Route(
        "/threads/{thread_id}/runs/{run_id}/join/stream",
        join_stream,
        methods=["GET"],
    ),
    Route(
        "/threads/{thread_id}/runs/{run_id}/cancel", cancel_run, methods=["POST"]
    ),
]
