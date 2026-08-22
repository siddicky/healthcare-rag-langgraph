from __future__ import annotations

# SIZE_OK — todo 6 requires this complete cron boundary in one file.
# ANYIO_OK: the exported scheduler contract requires an asyncio.Task handle.
import asyncio
import json
from collections.abc import Callable, Mapping, MutableMapping
from datetime import UTC, datetime
from typing import Annotated, ClassVar, Final, Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio
from croniter import croniter
from langgraph_sdk import Auth
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
)
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from server.auth import require_scope_match
from server.run_engine import QueueFull, RunConflict, RunEngine, RunRequest
from server.storage import Storage

CRON_LIMIT: Final = 500
_PUBLIC_FIELDS: Final = (
    "cron_id",
    "thread_id",
    "end_time",
    "schedule",
    "created_at",
    "updated_at",
    "payload",
    "next_run_date",
    "metadata",
    "enabled",
)


def _valid_schedule(value: str) -> str:
    if not croniter.is_valid(value):
        raise ValueError("invalid cron expression")
    return value


def _valid_timezone(value: str) -> str:
    try:
        _ = ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise ValueError("invalid IANA timezone") from error
    return value


CronSchedule = Annotated[str, AfterValidator(_valid_schedule)]
Timezone = Annotated[str, AfterValidator(_valid_timezone)]


class CronPayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    schedule: CronSchedule
    timezone: Timezone = "UTC"
    assistant_id: str
    input: dict[str, JsonValue]
    config: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    enabled: bool = True
    end_time: datetime | None = None
    multitask_strategy: Literal["reject", "enqueue"] = "enqueue"


class CronPatch(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    schedule: CronSchedule | None = None
    timezone: Timezone | None = None
    input: dict[str, JsonValue] | None = None
    config: dict[str, JsonValue] | None = None
    metadata: dict[str, JsonValue] | None = None
    enabled: bool | None = None
    end_time: datetime | None = None


class CronSearch(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    sort_by: Literal["cron_id", "created_at", "updated_at", "next_run_date"] = "cron_id"
    sort_order: Literal["asc", "desc"] = "asc"


class RunSubmitter(Protocol):
    async def submit(
        self, thread_id: str, request: RunRequest
    ) -> dict[str, object]: ...


def next_run_date(schedule: str, timezone: str, after: datetime) -> datetime:
    localized = after.astimezone(ZoneInfo(timezone))
    upcoming = croniter(schedule, localized).get_next(datetime)
    return upcoming.astimezone(UTC)


def _public(record: Mapping[str, object]) -> dict[str, object]:
    return {field: record[field] for field in _PUBLIC_FIELDS}


async def _json(request: Request, model: type[BaseModel]) -> BaseModel | JSONResponse:
    try:
        return model.model_validate(await request.json())
    except (ValidationError, json.JSONDecodeError) as error:
        return JSONResponse({"detail": str(error)}, status_code=422)


async def _scope(
    request: Request,
    action: Literal["create", "read", "update", "delete", "search"],
    value: MutableMapping[str, object],
) -> dict[str, object] | None | JSONResponse:
    try:
        return await request.app.state.auth_engine.run_policy(
            "crons", action, request.user, value
        )
    except Auth.exceptions.HTTPException as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status_code)


def _record(
    payload: CronPayload, thread_id: str | None, now: datetime
) -> dict[str, object]:
    cron_id = str(uuid4())
    following = next_run_date(payload.schedule, payload.timezone, now)
    if payload.end_time is not None and following > payload.end_time:
        following_value: str | None = None
    else:
        following_value = following.isoformat()
    run_payload = {
        "assistant_id": payload.assistant_id,
        "input": payload.input,
        "config": payload.config,
        "multitask_strategy": payload.multitask_strategy,
    }
    return {
        "cron_id": cron_id,
        "thread_id": thread_id,
        "end_time": payload.end_time.isoformat() if payload.end_time else None,
        "schedule": payload.schedule,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "payload": run_payload,
        "next_run_date": following_value if payload.enabled else None,
        "metadata": payload.metadata,
        "enabled": payload.enabled,
        "user_id": payload.metadata.get("user_id"),
        "_timezone": payload.timezone,
    }


async def _create(request: Request, thread_id: str | None) -> Response:
    parsed = await _json(request, CronPayload)
    if isinstance(parsed, JSONResponse):
        return parsed
    assert isinstance(parsed, CronPayload)
    scope = await _scope(request, "create", {"payload": parsed.model_dump(mode="json")})
    if isinstance(scope, JSONResponse):
        return scope
    storage: Storage = request.app.state.storage
    if thread_id is not None and thread_id not in storage.threads:
        return JSONResponse({"detail": "Thread not found"}, status_code=404)
    if len(storage.crons) >= CRON_LIMIT:
        return JSONResponse(
            {"detail": "Cron registry is full"},
            status_code=503,
            headers={"Retry-After": "1"},
        )
    record = _record(parsed, thread_id, datetime.now(UTC))
    if scope is not None:
        require_scope_match(record, scope)
    storage.crons[str(record["cron_id"])] = record
    return JSONResponse(_public(record))


async def create_stateless(request: Request) -> Response:
    return await _create(request, None)


async def create_thread(request: Request) -> Response:
    return await _create(request, request.path_params["thread_id"])


async def _authorized_record(
    request: Request, action: Literal["read", "update", "delete"]
):
    scope = await _scope(request, action, {"cron_id": request.path_params["cron_id"]})
    if isinstance(scope, JSONResponse):
        return scope
    record = request.app.state.storage.crons.get(request.path_params["cron_id"])
    if record is None:
        return JSONResponse({"detail": "Cron not found"}, status_code=404)
    try:
        if scope is not None:
            require_scope_match(record, scope)
    except Auth.exceptions.HTTPException as error:
        return JSONResponse({"detail": error.detail}, status_code=error.status_code)
    return record


async def get_cron(request: Request) -> Response:
    record = await _authorized_record(request, "read")
    return record if isinstance(record, JSONResponse) else JSONResponse(_public(record))


async def search_crons(request: Request) -> Response:
    parsed = await _json(request, CronSearch)
    if isinstance(parsed, JSONResponse):
        return parsed
    assert isinstance(parsed, CronSearch)
    scope = await _scope(request, "search", parsed.model_dump(mode="json"))
    if isinstance(scope, JSONResponse):
        return scope
    records = list(request.app.state.storage.crons.values())
    if scope is not None:
        records = [
            record
            for record in records
            if all(record.get(key) == value for key, value in scope.items())
        ]
    records = [
        record
        for record in records
        if all(
            record["metadata"].get(key) == value
            for key, value in parsed.metadata.items()
        )
    ]
    records.sort(
        key=lambda record: str(record.get(parsed.sort_by) or ""),
        reverse=parsed.sort_order == "desc",
    )
    return JSONResponse(
        [
            _public(record)
            for record in records[parsed.offset : parsed.offset + parsed.limit]
        ]
    )


async def patch_cron(request: Request) -> Response:
    record = await _authorized_record(request, "update")
    if isinstance(record, JSONResponse):
        return record
    parsed = await _json(request, CronPatch)
    if isinstance(parsed, JSONResponse):
        return parsed
    assert isinstance(parsed, CronPatch)
    updates = parsed.model_dump(exclude_unset=True, mode="json")
    payload_value = record["payload"]
    assert isinstance(payload_value, dict)
    payload = payload_value
    for key in ("input", "config"):
        if key in updates:
            payload[key] = updates.pop(key)
    if "timezone" in updates:
        record["_timezone"] = updates.pop("timezone")
    record.update(updates)
    record["user_id"] = record["metadata"].get("user_id")
    now = datetime.now(UTC)
    record["updated_at"] = now.isoformat()
    record["next_run_date"] = (
        next_run_date(
            str(record["schedule"]), str(record["_timezone"]), now
        ).isoformat()
        if record["enabled"]
        else None
    )
    return JSONResponse(_public(record))


async def delete_cron(request: Request) -> Response:
    record = await _authorized_record(request, "delete")
    if isinstance(record, JSONResponse):
        return record
    del request.app.state.storage.crons[request.path_params["cron_id"]]
    return Response(status_code=204)


async def run_due_crons(engine: RunSubmitter, storage: Storage, now: datetime) -> None:
    for record in tuple(storage.crons.values()):
        next_value = record["next_run_date"]
        if (
            not record["enabled"]
            or next_value is None
            or datetime.fromisoformat(str(next_value)) > now
        ):
            continue
        payload_value = record["payload"]
        assert isinstance(payload_value, dict)
        target = str(record["thread_id"] or uuid4())
        request = RunRequest.model_validate(
            payload_value | {"multitask_strategy": "enqueue"}
        )
        try:
            _ = await engine.submit(target, request)
        except (QueueFull, RunConflict):
            continue
        following = next_run_date(
            str(record["schedule"]), str(record["_timezone"]), now
        )
        end_time = record["end_time"]
        record["next_run_date"] = (
            following.isoformat()
            if end_time is None or following <= datetime.fromisoformat(str(end_time))
            else None
        )
        record["updated_at"] = now.isoformat()


async def _scheduler(
    engine: RunEngine, storage: Storage, clock: Callable[[], datetime]
) -> None:
    while True:
        await run_due_crons(engine, storage, clock())
        await anyio.sleep(1)


def start_scheduler(
    engine: RunEngine,
    storage: Storage,
    clock: Callable[[], datetime] | None = None,
) -> asyncio.Task[None]:
    source = clock or (lambda: datetime.now(UTC))
    return asyncio.create_task(
        _scheduler(engine, storage, source), name="cron-scheduler"
    )


routes: list[Route] = [
    Route("/runs/crons", create_stateless, methods=["POST"]),
    Route("/runs/crons/search", search_crons, methods=["POST"]),
    Route("/runs/crons/{cron_id}", get_cron, methods=["GET"]),
    Route("/runs/crons/{cron_id}", patch_cron, methods=["PATCH"]),
    Route("/runs/crons/{cron_id}", delete_cron, methods=["DELETE"]),
    Route("/threads/{thread_id}/runs/crons", create_thread, methods=["POST"]),
]
