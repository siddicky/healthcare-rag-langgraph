"""Read-only month schedule view: a MiniCalendar DATA envelope plus an agent listing.

Calendar facts (monthLabel, firstWeekday 0=Sunday, daysInMonth, day-of-month
highlights) are computed server-side from Python's ``calendar`` module — the
model never does date math. Entry ids appear ONLY in the envelope's ``text``
listing (``entry_id | date | time | kind | note``) because the MiniCalendar
design contract carries no ids in its data; the listing is what lets the model
target real ids for ``change_schedule``. Notes and kinds arrive already
scrubbed from the events fold (``append_event`` scrubs at write time).
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Mapping
from typing import Annotated, ClassVar, Final, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedStore
from langgraph.store.base import BaseStore
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from healthcare_rag.agent.memory import MemoryIdentityError
from healthcare_rag.agent.store_data import (
    JsonObject,
    ScheduleEntry,
    list_schedule,
    make_envelope,
)

_MONTH_PATTERN: Final = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")
_MONTH_NAMES: Final = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_INVALID_MONTH: Final = "Schedule unavailable: month must use YYYY-MM."
_MISSING_THREAD_CONTEXT: Final = "Schedule unavailable: thread context missing."
_EMPTY_MONTH_TEXT: Final = "No schedule entries for {month_label}."
_LISTING_LINE: Final = "{entry_id} | {date} | {time} | {kind} | {note}"


class _AuthPrincipal(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    identity: str


def _authenticated_user_id(config: RunnableConfig) -> str:
    principal = config.get("configurable", {}).get("langgraph_auth_user")
    if not isinstance(principal, Mapping):
        raise MemoryIdentityError
    try:
        identity = _AuthPrincipal.model_validate(principal).identity
    except ValidationError:
        raise MemoryIdentityError
    if not identity:
        raise MemoryIdentityError
    return identity


def _highlight_type(kind: str) -> Literal["injection", "checkin"]:
    """Map a schedule kind onto the MiniCalendar highlight vocabulary."""
    normalized = kind.casefold()
    if "review" in normalized or "check" in normalized:
        return "checkin"
    return "injection"


def _listing_line(entry: ScheduleEntry) -> str:
    return _LISTING_LINE.format(
        entry_id=entry.entry_id,
        date=entry.date.isoformat(),
        time=entry.time or "",
        kind=entry.kind,
        note=entry.description or "",
    )


async def view_schedule_impl(
    month: str,
    config: RunnableConfig,
    store: Annotated[BaseStore, InjectedStore()],
) -> str:
    """Show the member's schedule for one month (YYYY-MM) as a calendar card."""
    user_id = _authenticated_user_id(config)
    parsed = _MONTH_PATTERN.fullmatch(month)
    if parsed is None:
        return _INVALID_MONTH
    year, month_number = int(parsed.group(1)), int(parsed.group(2))
    configurable = config.get("configurable", {})
    thread_id = configurable.get("thread_id")
    human_msg_id = configurable.get("coach_human_msg_id")
    if not isinstance(thread_id, str) or not isinstance(human_msg_id, str):
        return _MISSING_THREAD_CONTEXT

    entries = await list_schedule(store, user_id)
    in_month = [entry for entry in entries if entry.date.year == year and entry.date.month == month_number]
    month_label = f"{_MONTH_NAMES[month_number - 1]} {year}"
    first_weekday_monday, days_in_month = calendar.monthrange(year, month_number)
    highlights: list[JsonValue] = [
        {"date": entry.date.day, "type": _highlight_type(entry.kind)}
        for entry in in_month
    ]
    data: JsonObject = {
        "monthLabel": month_label,
        "firstWeekday": (first_weekday_monday + 1) % 7,
        "daysInMonth": days_in_month,
        "highlights": highlights,
    }
    text = (
        "\n".join(_listing_line(entry) for entry in in_month)
        if in_month
        else _EMPTY_MONTH_TEXT.format(month_label=month_label)
    )
    return make_envelope(
        thread_id,
        human_msg_id,
        f"calendar:{month}",
        data,
        text,
    )


view_schedule = tool("view_schedule")(view_schedule_impl)

__all__ = ["view_schedule"]
