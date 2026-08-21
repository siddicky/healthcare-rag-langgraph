from __future__ import annotations

# noqa: SIZE_OK - the plan pins tools, delivery, and erasure helpers to this module.
import hmac
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import ClassVar, Final, override
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.store.base import BaseStore
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
)

from healthcare_rag.agent.memory import authenticated_user_id, sanitize_memory_field
from healthcare_rag.graph.resources import get as get_resources

from .cron_client import (
    Cron,
    CronAmbiguousError,
    CronAPIError,
    CronClient,
    CronCreate,
)
from .state import CoachState
from .store_data import (
    MAX_ACTIVE_REMINDERS,
    ReminderCapError,
    ReminderEdit,
    ReminderRecord,
    Weekday,
    list_reminders,
    make_envelope,
    soft_cancel_reminder,
)
from .store_data import create_reminder as store_create_reminder
from .store_data import edit_reminder as store_edit_reminder

CREATE_FAILED: Final = "Reminder not scheduled: the reminder service is unavailable."
CAP_REACHED: Final = "Reminder not scheduled: you can have up to 10 active reminders."
TARGET_REQUIRED: Final = "Reminder not changed: choose one reminder from the list."
EDIT_FAILED: Final = "Reminder paused: the schedule update could not be completed."
CANCEL_FAILED: Final = "Reminder paused: cancellation cleanup can be retried."
TITLE_REJECTED: Final = "Reminder not scheduled: the title did not pass privacy checks."

_FULL_WEEKDAY: Final[dict[Weekday, str]] = {
    Weekday.MON: "Monday",
    Weekday.TUE: "Tuesday",
    Weekday.WED: "Wednesday",
    Weekday.THU: "Thursday",
    Weekday.FRI: "Friday",
    Weekday.SAT: "Saturday",
    Weekday.SUN: "Sunday",
}


class CreateReminderArgs(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    title: str
    weekday: Weekday
    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class EditReminderArgs(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    target: str
    weekday: Weekday | None = None
    time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    active: bool | None = None


class CancelReminderArgs(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    target: str


class ReminderRuntimeError(Exception):
    @override
    def __str__(self) -> str:
        return "REMINDER_RUNTIME_REQUIRED"


def _runtime_values(
    runtime: ToolRuntime[None, CoachState],
) -> tuple[str, str, str, BaseStore]:
    user_id = authenticated_user_id(runtime.config)
    configurable = runtime.config.get("configurable", {})
    thread_id = configurable.get("thread_id")
    human_msg_id = configurable.get("coach_human_msg_id")
    if (
        not isinstance(thread_id, str)
        or not thread_id
        or not isinstance(human_msg_id, str)
        or not human_msg_id
        or not runtime.tool_call_id
        or runtime.store is None
    ):
        raise ReminderRuntimeError
    return user_id, thread_id, human_msg_id, runtime.store


def _schedule_label(reminder: ReminderRecord) -> str:
    hour, minute = (int(part) for part in reminder.time.split(":"))
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"Every {_FULL_WEEKDAY[reminder.weekday]} at {display_hour}:{minute:02d} {suffix}"


def _next_run(value: date | None) -> str | None:
    return value.strftime("%a, %b %-d") if value is not None else None


def _listing_data(reminders: list[ReminderRecord]) -> dict[str, JsonValue]:
    items: list[JsonValue] = []
    for reminder in reminders:
        item: dict[str, JsonValue] = {
            "reminder_id": reminder.reminder_id,
            "title": reminder.title,
            "scheduleLabel": _schedule_label(reminder),
            "active": reminder.active,
        }
        next_run = _next_run(reminder.next_run_date)
        if next_run is not None:
            item["nextRun"] = next_run
        items.append(item)
    return {"items": items}


async def _listing_envelope(
    store: BaseStore, user_id: str, thread_id: str, human_msg_id: str, text: str
) -> str:
    reminders = await list_reminders(store, user_id)
    return make_envelope(
        thread_id,
        human_msg_id,
        "reminders:list",
        _listing_data(reminders),
        text,
    )


async def _timezone(store: BaseStore, user_id: str) -> str:
    profile = await store.asearch(("users", user_id, "profile"), limit=100)
    for item in profile:
        candidate = item.value.get("timezone")
        if isinstance(candidate, str):
            try:
                _ = ZoneInfo(candidate)
            except ZoneInfoNotFoundError:
                continue
            return candidate
    return "UTC"


def _spec(record: ReminderRecord, user_id: str, *, enabled: bool) -> CronCreate:
    return CronCreate(
        reminder_id=record.reminder_id,
        user_id=user_id,
        thread_id=record.thread_id,
        wake_token=record.wake_token,
        weekday=record.weekday,
        time=record.time,
        timezone=record.timezone,
        enabled=enabled,
    )


def _cron_date(cron: Cron) -> date | None:
    if cron.next_run_date is None:
        return None
    try:
        return datetime.fromisoformat(cron.next_run_date).date()
    except ValueError as error:
        raise CronAPIError("next run parse") from error


async def _one_reconciled_cron(
    client: CronClient, record: ReminderRecord, user_id: str
) -> Cron:
    crons = sorted(
        await client.search(
            metadata={"reminder_id": record.reminder_id, "user_id": user_id},
            owner=user_id,
        ),
        key=lambda cron: cron.cron_id,
    )
    if not crons:
        raise CronAPIError("reconcile")
    keeper = next((cron for cron in crons if cron.cron_id == record.cron_id), crons[0])
    for duplicate in crons:
        if duplicate.cron_id != keeper.cron_id:
            await client.delete(duplicate.cron_id, user_id)
    remaining = await client.search(
        metadata={"reminder_id": record.reminder_id, "user_id": user_id},
        owner=user_id,
    )
    if len(remaining) != 1 or remaining[0].cron_id != keeper.cron_id:
        raise CronAPIError("reconcile")
    return remaining[0]


async def _finalize(
    store: BaseStore, user_id: str, record: ReminderRecord, cron: Cron, *, active: bool
) -> ReminderRecord:
    finalized = record.model_copy(
        update={
            "active": active,
            "cron_id": cron.cron_id,
            "next_run_date": _cron_date(cron),
        }
    )
    return await store_create_reminder(
        store, user_id, finalized, get_resources().privacy
    )


async def create_reminder_impl(
    title: str,
    weekday: Weekday,
    time: str,
    runtime: ToolRuntime[None, CoachState],
    *,
    client: CronClient,
) -> str:
    """Persist an inactive record before registering its authenticated cron."""
    user_id, thread_id, human_msg_id, store = _runtime_values(runtime)
    if (
        sum(item.active for item in await list_reminders(store, user_id))
        >= MAX_ACTIVE_REMINDERS
    ):
        return CAP_REACHED
    clean_title = sanitize_memory_field(title)
    if clean_title is None or not clean_title.strip():
        return TITLE_REJECTED
    pending = ReminderRecord(
        reminder_id=str(uuid4()),
        title=clean_title.strip()[:80],
        weekday=weekday,
        time=time,
        timezone=await _timezone(store, user_id),
        active=False,
        cron_id=None,
        thread_id=thread_id,
        wake_token=os.urandom(32).hex(),
        next_run_date=None,
        created_ts=datetime.now(UTC),
    )
    try:
        pending = await store_create_reminder(
            store, user_id, pending, get_resources().privacy
        )
        try:
            cron = await client.create(_spec(pending, user_id, enabled=True))
        except CronAmbiguousError:
            cron = await _one_reconciled_cron(client, pending, user_id)
        _ = await _finalize(store, user_id, pending, cron, active=True)
    except ReminderCapError:
        return CAP_REACHED
    except (CronAPIError, ValidationError):
        return CREATE_FAILED
    return await _listing_envelope(
        store, user_id, thread_id, human_msg_id, "Reminder scheduled."
    )


async def _resolve(
    store: BaseStore, user_id: str, target: str
) -> ReminderRecord | None:
    reminders = await list_reminders(store, user_id)
    exact = next((item for item in reminders if item.reminder_id == target), None)
    if exact is not None:
        return exact
    normalized = target.strip().casefold()
    matches = [item for item in reminders if item.title.casefold() == normalized]
    return matches[0] if len(matches) == 1 else None


async def edit_reminder_impl(
    target: str,
    weekday: Weekday | None,
    time: str | None,
    active: bool | None,
    runtime: ToolRuntime[None, CoachState],
    *,
    client: CronClient,
) -> str:
    """Pause and rotate first, then update or re-register the cron."""
    user_id, thread_id, human_msg_id, store = _runtime_values(runtime)
    current = await _resolve(store, user_id, target)
    if current is None:
        return TARGET_REQUIRED
    desired_active = current.active if active is None else active
    try:
        paused = await store_edit_reminder(
            store,
            user_id,
            current.reminder_id,
            ReminderEdit(
                weekday=weekday or current.weekday,
                time=time or current.time,
                active=False,
            ),
            get_resources().privacy,
        )
        spec = _spec(paused, user_id, enabled=desired_active)
        try:
            cron = (
                await client.update(current.cron_id, spec)
                if current.cron_id is not None
                else await client.create(spec)
            )
        except CronAmbiguousError:
            cron = await _one_reconciled_cron(client, paused, user_id)
        _ = await _finalize(store, user_id, paused, cron, active=desired_active)
    except (CronAPIError, ReminderCapError, ValidationError):
        return EDIT_FAILED
    return await _listing_envelope(
        store, user_id, thread_id, human_msg_id, "Reminder updated."
    )


async def _delete_all_for_reminder(
    client: CronClient, record: ReminderRecord, user_id: str
) -> bool:
    if record.cron_id is not None:
        await _delete_allowing_reconcile(client, record.cron_id, user_id)
    crons = await client.search(
        metadata={"reminder_id": record.reminder_id, "user_id": user_id},
        owner=user_id,
    )
    for cron in crons:
        await _delete_allowing_reconcile(client, cron.cron_id, user_id)
    remaining = await client.search(
        metadata={"reminder_id": record.reminder_id, "user_id": user_id},
        owner=user_id,
    )
    for cron in remaining:
        await client.delete(cron.cron_id, user_id)
    return not await client.search(
        metadata={"reminder_id": record.reminder_id, "user_id": user_id},
        owner=user_id,
    )


async def _delete_allowing_reconcile(
    client: CronClient, cron_id: str, user_id: str
) -> None:
    try:
        await client.delete(cron_id, user_id)
    except CronAmbiguousError:
        return


async def cancel_reminder_impl(
    target: str,
    runtime: ToolRuntime[None, CoachState],
    *,
    client: CronClient,
) -> str:
    """Soft-cancel before deleting every cron matching reminder metadata."""
    user_id, thread_id, human_msg_id, store = _runtime_values(runtime)
    current = await _resolve(store, user_id, target)
    if current is None:
        return TARGET_REQUIRED
    paused = await soft_cancel_reminder(store, user_id, current.reminder_id)
    try:
        clean = await _delete_all_for_reminder(client, current, user_id)
    except (CronAPIError, CronAmbiguousError):
        return CANCEL_FAILED
    if not clean:
        return CANCEL_FAILED
    if paused.cron_id is not None:
        return CANCEL_FAILED
    return await _listing_envelope(
        store, user_id, thread_id, human_msg_id, "Reminder cancelled."
    )


async def reminder_delivery(
    state: CoachState,
    config: RunnableConfig,
    *,
    store: BaseStore,
) -> CoachState:
    """Revalidate the wake and assemble one ReminderCard without model or memory access.

    Cron wakes have no human message id, so the generated AIMessage id is used as
    the turn-scope input. It is independent of the wake token and never exposes it.
    """
    configurable = config.get("configurable", {})
    wake = state.get("reminder_wake") or state.get("cron_wake")
    thread_id = configurable.get("thread_id")
    if wake is None or not isinstance(thread_id, str):
        return {"cron_wake": None, "reminder_wake": None}
    item = await store.aget(
        ("users", wake["user_id"], "reminders"), wake["reminder_id"]
    )
    try:
        record = ReminderRecord.model_validate(item.value) if item is not None else None
    except ValidationError:
        record = None
    valid = (
        record is not None
        and record.active
        and record.reminder_id == wake["reminder_id"]
        and record.thread_id == wake["thread_id"] == thread_id
        and hmac.compare_digest(record.wake_token, wake["wake_token"])
    )
    if not valid or record is None:
        return {"cron_wake": None, "reminder_wake": None}
    card: dict[str, JsonValue] = {
        "title": record.title,
        "schedule": _schedule_label(record),
        "weekday": _FULL_WEEKDAY[record.weekday],
        "time": record.time,
        "active": True,
    }
    next_run = _next_run(record.next_run_date)
    if next_run is not None:
        card["nextRun"] = next_run
    message_id = str(uuid4())
    envelope = make_envelope(
        thread_id,
        message_id,
        f"reminder:{record.reminder_id}",
        card,
        "Scheduled reminder.",
    )
    message = AIMessage(
        id=message_id,
        content=f"This is your scheduled reminder.\n{envelope}",
    )
    return {
        "cron_wake": None,
        "reminder_wake": None,
        "messages": [message],
        "follow_ups": [],
    }


async def cleanup_user_crons(
    store: BaseStore, user_id: str, client: CronClient
) -> bool:
    """Delete known and metadata-orphaned crons, returning exact-zero status."""
    try:
        for reminder in await list_reminders(store, user_id):
            if reminder.cron_id is not None:
                await client.delete(reminder.cron_id, user_id)
        orphans = await client.search(metadata={"user_id": user_id}, owner=user_id)
        for cron in orphans:
            await client.delete(cron.cron_id, user_id)
        return not await client.search(metadata={"user_id": user_id}, owner=user_id)
    except (CronAPIError, CronAmbiguousError):
        return False


async def sweep_upload_reservations(
    store: BaseStore, user_id: str, client: CronClient
) -> bool:
    """Delete owner-filtered reservation threads and matching registry records."""
    try:
        reservations = await client.search_reservations(user_id)
        for reservation_id in reservations:
            await client.delete_reservation(reservation_id)
            await store.adelete(("users", user_id, "upload_registry"), reservation_id)
        remaining_threads = await client.search_reservations(user_id)
        registry = await store.asearch(("users", user_id, "upload_registry"), limit=100)
        remaining_registry = [
            item for item in registry if item.value.get("owner") == user_id
        ]
        return not remaining_threads and not remaining_registry
    except CronAPIError:
        return False


@tool(args_schema=CreateReminderArgs)
async def create_reminder(
    title: str,
    weekday: Weekday,
    time: str,
    runtime: ToolRuntime[None, CoachState],
) -> str:
    """Create one recurring reminder for the authenticated member."""
    async with _deployment_client() as client:
        return await create_reminder_impl(title, weekday, time, runtime, client=client)


@tool(args_schema=EditReminderArgs)
async def edit_reminder(
    target: str,
    weekday: Weekday | None,
    time: str | None,
    active: bool | None,
    runtime: ToolRuntime[None, CoachState],
) -> str:
    """Edit or toggle one unambiguously selected reminder."""
    async with _deployment_client() as client:
        return await edit_reminder_impl(
            target, weekday, time, active, runtime, client=client
        )


@tool(args_schema=CancelReminderArgs)
async def cancel_reminder(target: str, runtime: ToolRuntime[None, CoachState]) -> str:
    """Cancel one unambiguously selected reminder."""
    async with _deployment_client() as client:
        return await cancel_reminder_impl(target, runtime, client=client)


@asynccontextmanager
async def _deployment_client() -> AsyncGenerator[CronClient]:
    async with httpx.AsyncClient(
        base_url=os.getenv("LANGGRAPH_API_URL", "http://localhost:2024"),
        timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
    ) as http:
        yield CronClient(
            http=http,
            api_key=os.getenv("LANGSMITH_API_KEY", ""),
            internal_token=os.getenv("COACH_INTERNAL_TOKEN", ""),
        )


__all__ = [
    "cancel_reminder",
    "cancel_reminder_impl",
    "cleanup_user_crons",
    "create_reminder",
    "create_reminder_impl",
    "edit_reminder",
    "edit_reminder_impl",
    "reminder_delivery",
    "sweep_upload_reservations",
]
