from __future__ import annotations

import json
from typing import Literal

from langgraph_sdk import Auth
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from server.auth import require_scope_match
from .protocol_events import (
    ProtocolFailure,
    parse_stream_filter,
    protocol_state,
    stream_runtime,
)
from server.run_engine import (
    CheckpointMissing,
    JSONValue,
    QueueFull,
    RunConflict,
    RunEngine,
    RunRequest,
    _to_jsonable,
)
from server.runs import _auth_user
from server.threads import _thread_graph


def _error(
    command_id: int | None, code: str, message: str, *, status_code: int = 400
) -> JSONResponse:
    return JSONResponse(
        {"type": "error", "id": command_id, "error": code, "message": message},
        status_code=status_code,
    )


async def _success(
    request: Request, command_id: int, result: JSONValue
) -> JSONResponse:
    thread_id = request.path_params["thread_id"]
    state = protocol_state(request, thread_id)
    async with state.lock:
        applied = state.next_seq - 1
    return JSONResponse(
        {
            "type": "success",
            "id": command_id,
            "result": result,
            "meta": {"thread_id": thread_id, "applied_through_seq": applied},
        }
    )


async def _authorize_read(request: Request, thread: dict[str, object]) -> None:
    scope_filter = await request.app.state.auth_engine.run_policy(
        "threads", "read", request.user, {"thread_id": request.path_params["thread_id"]}
    )
    if scope_filter is not None:
        require_scope_match(thread, scope_filter)


async def _latest_run(engine: RunEngine, thread_id: str) -> dict[str, object] | None:
    records = [
        record
        for record in await engine.storage.runs.all()
        if record.get("thread_id") == thread_id
    ]
    records.sort(key=lambda record: str(record.get("created_at", "")), reverse=True)
    return records[0] if records else None


async def thread_events(request: Request) -> Response:
    engine: RunEngine = request.app.state.run_engine
    thread_id = request.path_params["thread_id"]
    thread = await engine.storage.threads.get(thread_id)
    if thread is None:
        return _error(None, "no_such_run", "Thread not found", status_code=404)
    try:
        await _authorize_read(request, thread)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            raise ProtocolFailure("invalid_argument", "Invalid JSON body") from None
        filter_ = parse_stream_filter(_to_jsonable(body))
    except Auth.exceptions.HTTPException as exc:
        return _error(
            None, "permission_denied", str(exc.detail), status_code=exc.status_code
        )
    except ProtocolFailure as exc:
        return _error(None, exc.code, exc.message, status_code=exc.status_code)
    active_id = engine.active.get(thread_id)
    record = await _latest_run(engine, thread_id)
    run_id = active_id or (
        str(record["run_id"])
        if record is not None and str(record.get("run_id")) in engine.runtime
        else None
    )
    if run_id is None:
        return StreamingResponse(iter(()), media_type="text/event-stream")
    return StreamingResponse(
        stream_runtime(engine, run_id, filter_, protocol_state(request, thread_id)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _pending_interrupts(
    engine: RunEngine, thread_id: str, assistant_id: str, config: dict[str, JSONValue]
) -> list[str]:
    graph = engine.graphs[assistant_id]
    configurable = config.get("configurable", {})
    merged = dict(configurable) if isinstance(configurable, dict) else {}
    merged["thread_id"] = thread_id
    get_state = getattr(graph, "aget_state")  # noqa: B009
    snapshot = await get_state({**config, "configurable": merged})
    return [
        pending.id
        for task in snapshot.tasks
        for pending in (task.interrupts or [])
        if isinstance(pending.id, str)
    ]


def _params(command: dict[str, JSONValue]) -> dict[str, JSONValue]:
    params = command.get("params", {})
    if not isinstance(params, dict):
        raise ProtocolFailure("invalid_argument", "Command params must be an object.")
    return params


async def _submit(
    request: Request,
    command_id: int,
    assistant_id: str,
    input_: JSONValue,
    config: dict[str, JSONValue],
    multitask: str = "enqueue",
    *,
    force_resume: bool = False,
) -> JSONResponse:
    engine: RunEngine = request.app.state.run_engine
    thread_id = request.path_params["thread_id"]
    if assistant_id not in engine.graphs:
        raise ProtocolFailure("invalid_argument", "Unknown assistant_id.")
    if not isinstance(input_, dict):
        raise ProtocolFailure("invalid_argument", "Run input must be an object.")
    pending = await _pending_interrupts(engine, thread_id, assistant_id, config)
    resume = force_resume or bool(pending)
    if force_resume and not pending:
        if thread_id in engine.active:
            raise ProtocolFailure(
                "invalid_argument", "input.respond requires an interrupted run."
            )
        raise ProtocolFailure(
            "no_such_run", "No interrupted run is bound to this thread.", 404
        )
    payload: dict[str, JSONValue] = {
        "assistant_id": assistant_id,
        "config": config,
        "stream_mode": ["updates", "values", "messages", "custom"],
        "stream_resumable": True,
        "multitask_strategy": multitask,
    }
    if resume:
        payload["command"] = {"resume": input_}
    else:
        payload["input"] = input_
    parsed = RunRequest.model_validate(payload)
    thread = await engine.storage.threads.get(thread_id)
    assert thread is not None
    policy_value = {
        "thread_id": thread_id,
        "assistant_id": assistant_id,
        "kwargs": {
            "input": parsed.input,
            "command": parsed.command.model_dump(mode="json")
            if parsed.command
            else None,
            "config": parsed.config,
        },
    }
    scope_filter = await request.app.state.auth_engine.run_policy(
        "threads", "create_run", request.user, policy_value
    )
    if scope_filter is not None:
        require_scope_match(thread, scope_filter)
    record = await engine.submit(thread_id, parsed, auth_user=_auth_user(request))
    return await _success(request, command_id, {"run_id": str(record["run_id"])})


async def _run_start(
    request: Request, command_id: int, params: dict[str, JSONValue]
) -> JSONResponse:
    assistant_id = params.get("assistant_id")
    if not isinstance(assistant_id, str) or not assistant_id:
        raise ProtocolFailure("invalid_argument", "run.start requires an assistant_id.")
    config = params.get("config", {})
    if not isinstance(config, dict):
        raise ProtocolFailure("invalid_argument", "config must be an object.")
    multitask = params.get("multitaskStrategy", "enqueue")
    if multitask not in {"reject", "enqueue", "interrupt"}:
        multitask = "enqueue"
    return await _submit(
        request,
        command_id,
        assistant_id,
        params.get("input", {}),
        config,
        str(multitask),
    )


async def _input_respond(
    request: Request, command_id: int, params: dict[str, JSONValue]
) -> JSONResponse:
    if "update" in params or "goto" in params or "metadata" in params:
        raise ProtocolFailure(
            "invalid_argument",
            "input.respond does not support metadata, update, or goto.",
        )
    responses: dict[str, JSONValue] = {}
    batch = params.get("responses")
    if isinstance(batch, list):
        for entry in batch:
            if not isinstance(entry, dict):
                raise ProtocolFailure(
                    "invalid_argument", "Each response requires an interrupt_id."
                )
            interrupt_id = entry.get("interrupt_id")
            if not isinstance(interrupt_id, str):
                raise ProtocolFailure(
                    "invalid_argument", "Each response requires an interrupt_id."
                )
            responses[interrupt_id] = entry.get("response")
    elif isinstance(params.get("interrupt_id"), str):
        responses[str(params["interrupt_id"])] = params.get("response")
    if not responses:
        raise ProtocolFailure("invalid_argument", "input.respond requires a response.")
    config = params.get("config", {})
    if not isinstance(config, dict):
        raise ProtocolFailure("invalid_argument", "config must be an object.")
    if set(config) - {"configurable"}:
        raise ProtocolFailure(
            "invalid_argument", "input.respond config only supports checkpoint_id."
        )
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict) or set(configurable) - {"checkpoint_id"}:
        raise ProtocolFailure(
            "invalid_argument", "input.respond config only supports checkpoint_id."
        )
    engine: RunEngine = request.app.state.run_engine
    latest = await _latest_run(engine, request.path_params["thread_id"])
    if latest is None:
        raise ProtocolFailure(
            "no_such_run", "No interrupted run is bound to this thread.", 404
        )
    return await _submit(
        request,
        command_id,
        str(latest.get("assistant_id", "coach")),
        responses,
        config,
        force_resume=True,
    )


async def _state_get(
    request: Request, command_id: int, params: dict[str, JSONValue]
) -> JSONResponse:
    engine: RunEngine = request.app.state.run_engine
    thread_id = request.path_params["thread_id"]
    graph = await _thread_graph(request, engine.storage, thread_id)
    if graph is None:
        return await _success(request, command_id, {"values": {}})
    get_state = getattr(graph, "aget_state")  # noqa: B009
    snapshot = await get_state(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    )
    values = dict(snapshot.values)
    keys = params.get("keys")
    if isinstance(keys, list) and all(isinstance(key, str) for key in keys):
        values = {key: value for key, value in values.items() if key in keys}
    configurable = snapshot.config.get("configurable", {})
    checkpoint = None
    if isinstance(configurable, dict) and isinstance(
        configurable.get("checkpoint_id"), str
    ):
        checkpoint = {"id": configurable["checkpoint_id"]}
        if isinstance(configurable.get("checkpoint_ns"), str):
            checkpoint["ns"] = configurable["checkpoint_ns"]
    result: dict[str, JSONValue] = {"values": _to_jsonable(values)}
    if checkpoint is not None:
        result["checkpoint"] = checkpoint
    return await _success(request, command_id, result)


async def _list_checkpoints(
    request: Request, command_id: int, params: dict[str, JSONValue]
) -> JSONResponse:
    engine: RunEngine = request.app.state.run_engine
    thread_id = request.path_params["thread_id"]
    graph = await _thread_graph(request, engine.storage, thread_id)
    if graph is None:
        return await _success(request, command_id, {"checkpoints": []})
    limit = params.get("limit", 10)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ProtocolFailure("invalid_argument", "limit must be a positive integer.")
    before = params.get("before")
    if before is not None and not isinstance(before, str):
        raise ProtocolFailure("invalid_argument", "before must be a checkpoint id.")
    checkpoints: list[JSONValue] = []
    seen_before = before is None
    get_history = getattr(graph, "aget_state_history")  # noqa: B009
    async for snapshot in get_history(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    ):
        configurable = snapshot.config.get("configurable", {})
        checkpoint_id = (
            configurable.get("checkpoint_id")
            if isinstance(configurable, dict)
            else None
        )
        if not seen_before:
            seen_before = checkpoint_id == before
            continue
        parent = snapshot.parent_config
        parent_configurable = (
            parent.get("configurable", {}) if isinstance(parent, dict) else {}
        )
        checkpoints.append(
            _to_jsonable(
                {
                    "checkpoint_id": checkpoint_id,
                    "parent_checkpoint_id": parent_configurable.get("checkpoint_id"),
                    "created_at": snapshot.created_at,
                    "metadata": snapshot.metadata,
                    "next": list(snapshot.next),
                }
            )
        )
        if len(checkpoints) >= limit:
            break
    return await _success(request, command_id, {"checkpoints": checkpoints})


async def _state_fork(
    request: Request, command_id: int, params: dict[str, JSONValue]
) -> JSONResponse:
    checkpoint_id = params.get("checkpoint_id")
    if not isinstance(checkpoint_id, str) or not checkpoint_id:
        raise ProtocolFailure(
            "invalid_argument", "state.fork requires a checkpoint_id."
        )
    engine: RunEngine = request.app.state.run_engine
    latest = await _latest_run(engine, request.path_params["thread_id"])
    assistant_id = str(latest.get("assistant_id", "coach")) if latest else "coach"
    return await _submit(
        request,
        command_id,
        assistant_id,
        params.get("input", {}),
        {"configurable": {"checkpoint_id": checkpoint_id}},
    )


async def _agent_tree(request: Request, command_id: int) -> JSONResponse:
    engine: RunEngine = request.app.state.run_engine
    latest = await _latest_run(engine, request.path_params["thread_id"])
    status = str(latest.get("status", "success")) if latest else "success"
    protocol_status: Literal["running", "completed", "interrupted", "failed"]
    match status:
        case "pending" | "running":
            protocol_status = "running"
        case "interrupted":
            protocol_status = "interrupted"
        case "error" | "timeout":
            protocol_status = "failed"
        case _:
            protocol_status = "completed"
    graph_name = str(latest.get("assistant_id", "coach")) if latest else "coach"
    return await _success(
        request,
        command_id,
        {
            "tree": {
                "namespace": [],
                "status": protocol_status,
                "graph_name": graph_name,
                "children": [],
            }
        },
    )


async def thread_command(request: Request) -> Response:
    engine: RunEngine = request.app.state.run_engine
    thread_id = request.path_params["thread_id"]
    try:
        raw = await request.json()
    except json.JSONDecodeError:
        return _error(None, "invalid_argument", "Protocol commands must be valid JSON.")
    payload = _to_jsonable(raw)
    command_id = payload.get("id") if isinstance(payload, dict) else None
    method = payload.get("method") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or not isinstance(command_id, int)
        or isinstance(command_id, bool)
        or not isinstance(method, str)
    ):
        return _error(
            command_id
            if isinstance(command_id, int) and not isinstance(command_id, bool)
            else None,
            "invalid_argument",
            "Protocol commands must include an integer id and string method.",
        )
    thread = await engine.storage.threads.get(thread_id)
    if thread is None:
        return _error(command_id, "no_such_run", "Thread not found", status_code=404)
    try:
        await _authorize_read(request, thread)
        params = _params(payload)
        match method:
            case "run.start":
                return await _run_start(request, command_id, params)
            case "input.respond":
                return await _input_respond(request, command_id, params)
            case "state.get":
                return await _state_get(request, command_id, params)
            case "state.listCheckpoints":
                return await _list_checkpoints(request, command_id, params)
            case "state.fork":
                return await _state_fork(request, command_id, params)
            case "agent.getTree":
                return await _agent_tree(request, command_id)
            case _:
                return _error(
                    command_id, "unknown_command", f"Unknown protocol command: {method}"
                )
    except Auth.exceptions.HTTPException as exc:
        return _error(
            command_id,
            "permission_denied",
            str(exc.detail),
            status_code=exc.status_code,
        )
    except ProtocolFailure as exc:
        return _error(command_id, exc.code, exc.message, status_code=exc.status_code)
    except CheckpointMissing:
        return _error(
            command_id, "no_such_checkpoint", "Checkpoint not found", status_code=404
        )
    except RunConflict:
        return _error(
            command_id,
            "invalid_argument",
            "Thread already has an active run.",
            status_code=409,
        )
    except QueueFull:
        return _error(
            command_id, "invalid_argument", "Run queue is full.", status_code=503
        )
    except ValidationError as exc:
        return _error(command_id, "invalid_argument", str(exc))


routes: list[Route] = [
    Route("/threads/{thread_id}/stream/events", thread_events, methods=["POST"]),
    Route("/threads/{thread_id}/commands", thread_command, methods=["POST"]),
]
