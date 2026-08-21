"""Injection log tool: record today's dose, emit a sparse week-strip envelope.

The server records ONLY the reported event — today's date, status ``logged``.
Any ``upcoming`` day and ``nextDoseLabel`` are derived exclusively from
approved schedule entries in the events fold (todo 2 readers ``list_schedule``
and ``next_dose``). No cadence is inferred, no filler or undated entries are
emitted: the seven-day Monday-first grid with ``muted`` filler is the frontend
adapter's job. ``medicationName``/``doseLabel`` are included in the data so the
InjectionTracker's required props hydrate fully through refs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, ClassVar, Final, final, override
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import InjectedStore
from langgraph.store.base import BaseStore
from pydantic import BaseModel, ConfigDict, ValidationError

from healthcare_rag.agent.store_data import (
    InjectionLogEntry,
    JsonObject,
    Weekday,
    add_injection,
    list_schedule,
    make_envelope,
    next_dose,
)
from healthcare_rag.graph.resources import get as get_resources
from healthcare_rag.processors.privacy import PrivacyScanError

BLOCK_ID: Final = "weekstrip:injection"
STORE_REFUSAL: Final = "Injection not logged: storage unavailable."
PRIVACY_REFUSAL: Final = "Injection not logged: privacy checks failed."

_WEEKDAY_LABELS: Final = (
    Weekday.MON,
    Weekday.TUE,
    Weekday.WED,
    Weekday.THU,
    Weekday.FRI,
    Weekday.SAT,
    Weekday.SUN,
)
_WEEKDAY_NAMES: Final = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


@final
@dataclass(frozen=True, slots=True)
class InjectionIdentityError(Exception):
    code: str = "INJECTION_AUTH_IDENTITY_REQUIRED"

    @override
    def __str__(self) -> str:
        return self.code


@final
@dataclass(frozen=True, slots=True)
class InjectionScopeError(Exception):
    code: str = "INJECTION_TURN_SCOPE_REQUIRED"

    @override
    def __str__(self) -> str:
        return self.code


class _AuthPrincipal(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    identity: str


class LogInjectionArgs(BaseModel):
    """Model-facing arguments; dates and statuses are never model-set."""

    medication_name: str
    dose_label: str


def _authenticated_user_id(config: RunnableConfig) -> str:
    principal = config.get("configurable", {}).get("langgraph_auth_user")
    if not isinstance(principal, Mapping):
        raise InjectionIdentityError
    try:
        identity = _AuthPrincipal.model_validate(principal).identity
    except ValidationError:
        raise InjectionIdentityError from None
    if not identity:
        raise InjectionIdentityError
    return identity


def _turn_scope(config: RunnableConfig) -> tuple[str, str]:
    configurable = config.get("configurable", {})
    thread_id = configurable.get("thread_id")
    human_msg_id = configurable.get("coach_human_msg_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise InjectionScopeError
    if not isinstance(human_msg_id, str) or not human_msg_id:
        raise InjectionScopeError
    return thread_id, human_msg_id


def _server_today() -> date:
    return datetime.now(UTC).date()


def _is_dose_kind(kind: str) -> bool:
    normalized = kind.casefold()
    return "dose" in normalized or "injection" in normalized


def _day(day: date, status: str) -> JsonObject:
    return {
        "date": day.isoformat(),
        "label": str(_WEEKDAY_LABELS[day.weekday()]),
        "status": status,
    }


async def _upcoming_days(
    store: BaseStore,
    user_id: str,
    today: date,
) -> list[JsonObject]:
    """Project one upcoming day per distinct future dose date; nothing else."""
    seen: dict[str, JsonObject] = {}
    for entry in await list_schedule(store, user_id):
        if entry.date <= today or not _is_dose_kind(entry.kind):
            continue
        key = entry.date.isoformat()
        if key not in seen:
            seen[key] = _day(entry.date, "upcoming")
    return [seen[key] for key in sorted(seen)]


async def log_injection_impl(
    medication_name: str,
    dose_label: str,
    config: RunnableConfig,
    store: Annotated[BaseStore, InjectedStore()],
    today: Annotated[Callable[[], date] | None, InjectedToolArg] = None,
) -> str:
    """Log today's injection dose and return a sparse week-strip card."""
    user_id = _authenticated_user_id(config)
    thread_id, human_msg_id = _turn_scope(config)
    privacy = get_resources().privacy
    try:
        clean_medication = privacy.scan(medication_name).text
        clean_dose = privacy.scan(dose_label).text
    except PrivacyScanError:
        return PRIVACY_REFUSAL
    server_today = (today or _server_today)()
    try:
        upcoming = await _upcoming_days(store, user_id, server_today)
        dose = await next_dose(store, user_id, from_date=server_today)
        await add_injection(
            store,
            user_id,
            InjectionLogEntry(
                injection_id=str(uuid4()),
                medication=clean_medication,
                date=server_today,
                created_ts=datetime.now(UTC),
            ),
        )
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK - store boundary.
        return STORE_REFUSAL
    data: JsonObject = {
        "medicationName": clean_medication,
        "doseLabel": clean_dose,
        "days": [_day(server_today, "logged"), *upcoming],
    }
    if dose is not None:
        data["nextDoseLabel"] = _WEEKDAY_NAMES[dose.date.weekday()]
    text = (
        f"{clean_medication} {clean_dose} logged for "
        f"{_WEEKDAY_NAMES[server_today.weekday()]}."
    )
    return make_envelope(thread_id, human_msg_id, BLOCK_ID, data, text)


log_injection = tool("log_injection", args_schema=LogInjectionArgs)(log_injection_impl)

__all__ = [
    "InjectionIdentityError",
    "InjectionScopeError",
    "LogInjectionArgs",
    "log_injection",
]
