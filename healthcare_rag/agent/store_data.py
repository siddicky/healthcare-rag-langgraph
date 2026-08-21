from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import (
    ClassVar,
    Final,
    Literal,
    Protocol,
    TypeAlias,
    assert_never,
    final,
    override,
)
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langgraph.store.base import BaseStore, Item, SearchItem
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from healthcare_rag.processors.privacy import PrivacySanitizer, PrivacyScan

JsonObject: TypeAlias = dict[str, JsonValue]
Namespace: TypeAlias = tuple[str, ...]
OpStatus: TypeAlias = Literal["pending", "applied", "declined", "declined-stale"]
EventDecision: TypeAlias = Literal["approved", "declined", "declined-stale"]

PAGE_SIZE: Final = 50
MAX_ACTIVE_REMINDERS: Final = 10
TIME_PATTERN: Final = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
WRITABLE_COLLECTIONS: Final = frozenset(
    {
        "metrics",
        "injection_log",
        "reminders",
        "feedback",
        "upload_registry",
        "profile",
        "episodic",
        "ops",
        "events",
        "gate",
    }
)
_DEFAULT_SANITIZER: Final = PrivacySanitizer()


class PrivacyScanner(Protocol):
    def scan(self, text: str) -> PrivacyScan: ...


@dataclass(frozen=True, slots=True)
class StoreNamespaceError(Exception):
    namespace: Namespace

    @override
    def __str__(self) -> str:
        return f"invalid user store namespace: {self.namespace!r}"


@dataclass(frozen=True, slots=True)
class ErasureGateError(Exception):
    user_id: str

    @override
    def __str__(self) -> str:
        return f"writes are disabled while user {self.user_id!r} is being erased"


@dataclass(frozen=True, slots=True)
class ErasureCapabilityError(Exception):
    @override
    def __str__(self) -> str:
        return "privileged erasure capability required"


@dataclass(frozen=True, slots=True)
class ReminderCapError(Exception):
    user_id: str

    @override
    def __str__(self) -> str:
        return (
            f"user {self.user_id!r} already has {MAX_ACTIVE_REMINDERS} active reminders"
        )


@dataclass(frozen=True, slots=True)
class RecordNotFoundError(Exception):
    collection: str
    key: str

    @override
    def __str__(self) -> str:
        return f"{self.collection} record {self.key!r} was not found"


class StoreModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class Weekday(StrEnum):
    MON = "Mon"
    TUE = "Tue"
    WED = "Wed"
    THU = "Thu"
    FRI = "Fri"
    SAT = "Sat"
    SUN = "Sun"


class ScheduleEntry(StoreModel):
    entry_id: str
    date: date
    time: str | None = None
    kind: str
    description: str | None = None
    active: bool
    created_ts: datetime


class InjectionLogEntry(StoreModel):
    injection_id: str
    medication: str
    date: date
    note: str | None = None
    created_ts: datetime


class MetricEntry(StoreModel):
    metric_id: str
    metric: str
    value: float
    unit: str
    date: date
    note: str | None = None
    created_ts: datetime


class ReminderRecord(StoreModel):
    reminder_id: str
    title: str = Field(min_length=1, max_length=80)
    weekday: Weekday
    time: str
    timezone: str = "UTC"
    active: bool
    cron_id: str | None
    thread_id: str
    wake_token: str
    next_run_date: date | None
    created_ts: datetime

    @field_validator("time")
    @classmethod
    def valid_time(cls, value: str) -> str:
        if TIME_PATTERN.fullmatch(value) is None:
            raise ValueError("time must use HH:MM")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            _ = ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be an IANA name") from error
        return value


class ReminderEdit(StoreModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    weekday: Weekday | None = None
    time: str | None = None
    timezone: str | None = None
    active: bool | None = None
    cron_id: str | None = None
    next_run_date: date | None = None

    @field_validator("time")
    @classmethod
    def valid_time(cls, value: str | None) -> str | None:
        if value is not None and TIME_PATTERN.fullmatch(value) is None:
            raise ValueError("time must use HH:MM")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            _ = ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone must be an IANA name") from error
        return value


class OpRecord(StoreModel):
    op_id: str
    status: OpStatus
    result: JsonValue
    created_ts: datetime
    resolved_entry_id: str | None
    frozen_request: JsonObject
    interrupt_payload: JsonObject


class AddMutation(StoreModel):
    action: Literal["add"]
    date: date
    time: str | None = None
    kind: str
    description: str | None = None

    @field_validator("time")
    @classmethod
    def valid_time(cls, value: str | None) -> str | None:
        if value is not None and TIME_PATTERN.fullmatch(value) is None:
            raise ValueError("time must use HH:MM")
        return value


class RescheduleMutation(StoreModel):
    action: Literal["reschedule"]
    destination_date: date
    destination_time: str | None = None

    @field_validator("destination_time")
    @classmethod
    def valid_time(cls, value: str | None) -> str | None:
        if value is not None and TIME_PATTERN.fullmatch(value) is None:
            raise ValueError("time must use HH:MM")
        return value


class CancelMutation(StoreModel):
    action: Literal["cancel"]


EventMutation: TypeAlias = AddMutation | RescheduleMutation | CancelMutation


class ApprovalEvent(StoreModel):
    op_id: str
    event_key: str
    decision: EventDecision
    decision_ts: datetime
    entry_id: str
    mutation: EventMutation = Field(discriminator="action")
    created_ts: datetime


class UploadRegistryRecord(StoreModel):
    owner: str
    intended_thread: str
    expires_at: datetime
    status: Literal["uploading", "scanning", "extracting", "done", "error"]
    proposal: JsonObject | None = None
    consumed: bool = False


@final
class _EraseCapability:
    __slots__: tuple[str, ...] = ()


_ERASE_CAPABILITY: Final = _EraseCapability()


def _coordinator_capability() -> _EraseCapability:
    return _ERASE_CAPABILITY


def _namespace(user_id: str, collection: str) -> Namespace:
    namespace = ("users", user_id, collection)
    validate_user_namespace(namespace, user_id)
    return namespace


def validate_user_namespace(namespace: Namespace, user_id: str) -> None:
    if (
        len(namespace) != 3
        or namespace[0] != "users"
        or namespace[1] != user_id
        or namespace[2] not in WRITABLE_COLLECTIONS
    ):
        raise StoreNamespaceError(namespace)


async def guard_user_write(store: BaseStore, user_id: str) -> None:
    if await store.aget(_namespace(user_id, "gate"), "erasing") is not None:
        raise ErasureGateError(user_id)


def _scanner(scanner: PrivacyScanner | None) -> PrivacyScanner:
    return scanner if scanner is not None else _DEFAULT_SANITIZER


def _scrub_json(value: JsonValue, scanner: PrivacyScanner) -> JsonValue:
    match value:
        case str() as text:
            return scanner.scan(text).text
        case bool() | int() | float() | None:
            return value
        case list() as items:
            return [_scrub_json(item, scanner) for item in items]
        case dict() as mapping:
            return {key: _scrub_json(item, scanner) for key, item in mapping.items()}
        case unreachable:
            assert_never(unreachable)


async def _put_model(
    store: BaseStore,
    user_id: str,
    collection: str,
    key: str,
    model: StoreModel,
    scanner: PrivacyScanner | None,
) -> None:
    await guard_user_write(store, user_id)
    clean = _scrub_json(model.model_dump(mode="json"), _scanner(scanner))
    if not isinstance(clean, dict):
        raise StoreNamespaceError(_namespace(user_id, collection))
    validated = type(model).model_validate(clean).model_dump(mode="json")
    await store.aput(_namespace(user_id, collection), key, validated, index=False)


async def _all_items(
    store: BaseStore,
    namespace: Namespace,
    page_size: int = PAGE_SIZE,
) -> list[SearchItem]:
    validate_user_namespace(namespace, namespace[1] if len(namespace) > 1 else "")
    items: list[SearchItem] = []
    offset = 0
    while True:
        page = await store.asearch(namespace, limit=page_size, offset=offset)
        exact = [item for item in page if item.namespace == namespace]
        items.extend(exact)
        if len(page) < page_size:
            return items
        offset += page_size


def make_envelope(
    thread_id: str,
    human_msg_id: str,
    block_id: str,
    data: JsonValue,
    text: str,
) -> str:
    turn_scope_id = hashlib.sha256(f"{thread_id}|{human_msg_id}".encode()).hexdigest()
    return json.dumps(
        {
            "turn_scope_id": turn_scope_id,
            "block_id": block_id,
            "data": data,
            "text": text,
        }
    )


def event_key_for(user_id: str, op_id: str) -> str:
    return hashlib.sha256(user_id.encode() + b"\x00" + op_id.encode()).hexdigest()


def _event_from_item(item: Item | SearchItem) -> ApprovalEvent:
    return ApprovalEvent.model_validate({**item.value, "created_ts": item.created_at})


async def get_event_for_op(
    store: BaseStore,
    user_id: str,
    op_id: str,
) -> ApprovalEvent | None:
    item = await store.aget(
        _namespace(user_id, "events"), event_key_for(user_id, op_id)
    )
    if item is None:
        return None
    event = _event_from_item(item)
    if event.op_id != op_id:
        raise StoreNamespaceError(item.namespace)
    return event


async def _events(
    store: BaseStore,
    user_id: str,
    page_size: int = PAGE_SIZE,
) -> list[ApprovalEvent]:
    items = await _all_items(store, _namespace(user_id, "events"), page_size)
    return sorted(
        (_event_from_item(item) for item in items),
        key=lambda event: (event.created_ts, event.event_key),
    )


async def schedule_state(
    store: BaseStore,
    user_id: str,
    page_size: int = PAGE_SIZE,
) -> dict[str, ScheduleEntry]:
    state: dict[str, ScheduleEntry] = {}
    for event in await _events(store, user_id, page_size):
        match event.decision:
            case "declined" | "declined-stale":
                continue
            case "approved":
                pass
            case unreachable:
                assert_never(unreachable)
        match event.mutation:
            case AddMutation(
                date=event_date, time=event_time, kind=kind, description=description
            ):
                state[event.entry_id] = ScheduleEntry(
                    entry_id=event.entry_id,
                    date=event_date,
                    time=event_time,
                    kind=kind,
                    description=description,
                    active=True,
                    created_ts=event.created_ts,
                )
            case RescheduleMutation(
                destination_date=destination_date, destination_time=destination_time
            ):
                current = state.get(event.entry_id)
                if current is not None and current.active:
                    state[event.entry_id] = current.model_copy(
                        update={"date": destination_date, "time": destination_time}
                    )
            case CancelMutation():
                current = state.get(event.entry_id)
                if current is not None and current.active:
                    state[event.entry_id] = current.model_copy(update={"active": False})
            case unreachable:
                assert_never(unreachable)
    return state


async def list_schedule(
    store: BaseStore,
    user_id: str,
    page_size: int = PAGE_SIZE,
) -> list[ScheduleEntry]:
    state = await schedule_state(store, user_id, page_size)
    return sorted(
        (entry for entry in state.values() if entry.active),
        key=lambda entry: (entry.date, entry.time or "", entry.entry_id),
    )


async def resolve_target(
    store: BaseStore,
    user_id: str,
    target: str,
) -> ScheduleEntry | None:
    entries = await list_schedule(store, user_id)
    exact = next((entry for entry in entries if entry.entry_id == target), None)
    if exact is not None:
        return exact
    normalized = target.casefold()
    matches = [
        entry
        for entry in entries
        if normalized in entry.kind.casefold()
        or (
            entry.description is not None and normalized in entry.description.casefold()
        )
    ]
    return matches[0] if len(matches) == 1 else None


async def next_dose(
    store: BaseStore,
    user_id: str,
    from_date: date | None = None,
) -> ScheduleEntry | None:
    boundary = from_date or datetime.now(UTC).date()
    return next(
        (
            entry
            for entry in await list_schedule(store, user_id)
            if entry.date >= boundary
            and (
                "dose" in entry.kind.casefold() or "injection" in entry.kind.casefold()
            )
        ),
        None,
    )


async def append_event(
    store: BaseStore,
    user_id: str,
    event: ApprovalEvent,
    scanner: PrivacyScanner | None = None,
) -> ApprovalEvent:
    existing = await get_event_for_op(store, user_id, event.op_id)
    if existing is not None:
        return existing
    await guard_user_write(store, user_id)
    decision: EventDecision = event.decision
    if decision == "approved" and not isinstance(event.mutation, AddMutation):
        current = (await schedule_state(store, user_id)).get(event.entry_id)
        if current is None or not current.active:
            decision = "declined-stale"
    server_event = event.model_copy(
        update={
            "event_key": event_key_for(user_id, event.op_id),
            "decision": decision,
            "decision_ts": datetime.now(UTC),
        }
    )
    clean = _scrub_json(server_event.model_dump(mode="json"), _scanner(scanner))
    if not isinstance(clean, dict):
        raise StoreNamespaceError(_namespace(user_id, "events"))
    await store.aput(
        _namespace(user_id, "events"), server_event.event_key, clean, index=False
    )
    stored = await get_event_for_op(store, user_id, event.op_id)
    if stored is None:
        raise RecordNotFoundError("events", server_event.event_key)
    return stored


async def get_op(store: BaseStore, user_id: str, op_id: str) -> OpRecord | None:
    item = await store.aget(_namespace(user_id, "ops"), op_id)
    return OpRecord.model_validate(item.value) if item is not None else None


async def put_op_if_absent(
    store: BaseStore,
    user_id: str,
    op: OpRecord,
    scanner: PrivacyScanner | None = None,
) -> bool:
    await guard_user_write(store, user_id)
    if await store.aget(_namespace(user_id, "ops"), op.op_id) is not None:
        return False
    clean = _scrub_json(op.model_dump(mode="json"), _scanner(scanner))
    if not isinstance(clean, dict):
        raise StoreNamespaceError(_namespace(user_id, "ops"))
    clean["op_id"] = op.op_id
    validated = OpRecord.model_validate(clean).model_dump(mode="json")
    await store.aput(_namespace(user_id, "ops"), op.op_id, validated, index=False)
    return True


async def put_op(
    store: BaseStore,
    user_id: str,
    op: OpRecord,
    scanner: PrivacyScanner | None = None,
) -> None:
    await guard_user_write(store, user_id)
    clean = _scrub_json(op.model_dump(mode="json"), _scanner(scanner))
    if not isinstance(clean, dict):
        raise StoreNamespaceError(_namespace(user_id, "ops"))
    clean["op_id"] = op.op_id
    validated = OpRecord.model_validate(clean).model_dump(mode="json")
    await store.aput(_namespace(user_id, "ops"), op.op_id, validated, index=False)


async def create_reminder(
    store: BaseStore,
    user_id: str,
    reminder: ReminderRecord,
    scanner: PrivacyScanner | None = None,
) -> ReminderRecord:
    if reminder.active:
        active_count = sum(
            entry.active for entry in await list_reminders(store, user_id)
        )
        if active_count >= MAX_ACTIVE_REMINDERS:
            raise ReminderCapError(user_id)
    await _put_model(
        store, user_id, "reminders", reminder.reminder_id, reminder, scanner
    )
    item = await store.aget(_namespace(user_id, "reminders"), reminder.reminder_id)
    if item is None:
        raise RecordNotFoundError("reminders", reminder.reminder_id)
    return ReminderRecord.model_validate(item.value)


async def list_reminders(
    store: BaseStore,
    user_id: str,
    page_size: int = PAGE_SIZE,
) -> list[ReminderRecord]:
    items = await _all_items(store, _namespace(user_id, "reminders"), page_size)
    return sorted(
        (ReminderRecord.model_validate(item.value) for item in items),
        key=lambda reminder: (reminder.created_ts, reminder.reminder_id),
    )


async def edit_reminder(
    store: BaseStore,
    user_id: str,
    reminder_id: str,
    edit: ReminderEdit,
    scanner: PrivacyScanner | None = None,
) -> ReminderRecord:
    item = await store.aget(_namespace(user_id, "reminders"), reminder_id)
    if item is None:
        raise RecordNotFoundError("reminders", reminder_id)
    current = ReminderRecord.model_validate(item.value)
    changes = edit.model_dump(exclude_unset=True)
    changes["wake_token"] = secrets.token_urlsafe(32)
    updated = current.model_copy(update=changes)
    await _put_model(store, user_id, "reminders", reminder_id, updated, scanner)
    stored = await store.aget(_namespace(user_id, "reminders"), reminder_id)
    if stored is None:
        raise RecordNotFoundError("reminders", reminder_id)
    return ReminderRecord.model_validate(stored.value)


async def soft_cancel_reminder(
    store: BaseStore,
    user_id: str,
    reminder_id: str,
) -> ReminderRecord:
    return await edit_reminder(
        store,
        user_id,
        reminder_id,
        ReminderEdit(active=False, cron_id=None),
    )


async def add_metric(
    store: BaseStore,
    user_id: str,
    metric: MetricEntry,
    scanner: PrivacyScanner | None = None,
) -> None:
    await _put_model(store, user_id, "metrics", metric.metric_id, metric, scanner)


async def list_metrics(
    store: BaseStore,
    user_id: str,
    page_size: int = PAGE_SIZE,
) -> list[MetricEntry]:
    items = await _all_items(store, _namespace(user_id, "metrics"), page_size)
    return sorted(
        (MetricEntry.model_validate(item.value) for item in items),
        key=lambda metric: (metric.date, metric.created_ts, metric.metric_id),
    )


async def metric_range(
    store: BaseStore,
    user_id: str,
    start: date,
    end: date,
    page_size: int = PAGE_SIZE,
) -> list[MetricEntry]:
    return [
        metric
        for metric in await list_metrics(store, user_id, page_size)
        if start <= metric.date <= end
    ]


async def add_injection(
    store: BaseStore,
    user_id: str,
    injection: InjectionLogEntry,
    scanner: PrivacyScanner | None = None,
) -> None:
    await _put_model(
        store,
        user_id,
        "injection_log",
        injection.injection_id,
        injection,
        scanner,
    )


async def list_injections(
    store: BaseStore,
    user_id: str,
    page_size: int = PAGE_SIZE,
) -> list[InjectionLogEntry]:
    items = await _all_items(store, _namespace(user_id, "injection_log"), page_size)
    return sorted(
        (InjectionLogEntry.model_validate(item.value) for item in items),
        key=lambda injection: (
            injection.date,
            injection.created_ts,
            injection.injection_id,
        ),
    )


async def injection_range(
    store: BaseStore,
    user_id: str,
    start: date,
    end: date,
    page_size: int = PAGE_SIZE,
) -> list[InjectionLogEntry]:
    return [
        injection
        for injection in await list_injections(store, user_id, page_size)
        if start <= injection.date <= end
    ]


async def put_upload_registry(
    store: BaseStore,
    user_id: str,
    reservation_id: str,
    record: UploadRegistryRecord,
    scanner: PrivacyScanner | None = None,
) -> None:
    if record.owner != user_id:
        raise StoreNamespaceError(_namespace(user_id, "upload_registry"))
    await _put_model(store, user_id, "upload_registry", reservation_id, record, scanner)


async def get_upload_registry(
    store: BaseStore,
    user_id: str,
    reservation_id: str,
    now: datetime | None = None,
) -> UploadRegistryRecord | None:
    item = await store.aget(_namespace(user_id, "upload_registry"), reservation_id)
    if item is None:
        return None
    record = UploadRegistryRecord.model_validate(item.value)
    instant = now or datetime.now(UTC)
    if record.owner != user_id:
        raise StoreNamespaceError(item.namespace)
    if record.expires_at <= instant:
        await guard_user_write(store, user_id)
        await store.adelete(item.namespace, item.key)
        return None
    return record


async def put_user_record(
    store: BaseStore,
    user_id: str,
    collection: Literal["feedback", "profile", "episodic"],
    key: str,
    value: JsonObject,
    scanner: PrivacyScanner | None = None,
) -> None:
    await guard_user_write(store, user_id)
    clean = _scrub_json(value, _scanner(scanner))
    if not isinstance(clean, dict):
        raise StoreNamespaceError(_namespace(user_id, collection))
    await store.aput(_namespace(user_id, collection), key, clean, index=False)


async def delete_all_for_user(
    store: BaseStore,
    user_id: str,
    capability: _EraseCapability | None,
    page_size: int = PAGE_SIZE,
) -> None:
    if capability is not _ERASE_CAPABILITY:
        raise ErasureCapabilityError()
    namespaces: list[Namespace] = []
    offset = 0
    while True:
        page = await store.alist_namespaces(
            prefix=("users", user_id),
            limit=page_size,
            offset=offset,
        )
        namespaces.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    for namespace in namespaces:
        validate_user_namespace(namespace, user_id)
    for namespace in namespaces:
        if namespace[2] == "gate":
            continue
        while True:
            page = await store.asearch(namespace, limit=page_size, offset=0)
            if not page:
                break
            for item in page:
                if item.namespace != namespace:
                    raise StoreNamespaceError(item.namespace)
                await store.adelete(namespace, item.key)
