from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Final, TypedDict

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.store.memory import InMemoryStore
from pydantic import TypeAdapter

from healthcare_rag.agent import store_data
from healthcare_rag.agent.memory import MemoryIdentityError
from healthcare_rag.agent.store_data import (
    AddMutation,
    ApprovalEvent,
    CancelMutation,
    EventMutation,
)
from healthcare_rag.agent.tools import view_schedule
from healthcare_rag.processors.privacy import PrivacyScan

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


class CalendarHighlight(TypedDict):
    date: int
    type: str


class CalendarData(TypedDict):
    monthLabel: str
    firstWeekday: int
    daysInMonth: int
    highlights: list[CalendarHighlight]


class EnvelopePayload(TypedDict):
    turn_scope_id: str
    block_id: str
    data: CalendarData
    text: str


_ENVELOPE: Final = TypeAdapter(EnvelopePayload)


def _envelope(result: str) -> EnvelopePayload:
    return _ENVELOPE.validate_json(result)


class FakeSanitizer:
    def scan(self, text: str) -> PrivacyScan:
        return PrivacyScan(text.replace("Alice Johnson", "[REDACTED_PERSON]"), ())


def _config(
    identity: str = "user-a",
    *,
    thread_id: str = "thread-1",
    human_msg_id: str = "human-1",
) -> RunnableConfig:
    return {
        "configurable": {
            "langgraph_auth_user": {"identity": identity},
            "thread_id": thread_id,
            "coach_human_msg_id": human_msg_id,
        }
    }


def _event(op_id: str, entry_id: str, mutation: EventMutation) -> ApprovalEvent:
    return ApprovalEvent(
        op_id=op_id,
        event_key="ignored-client-key",
        decision="approved",
        decision_ts=NOW,
        entry_id=entry_id,
        mutation=mutation,
        created_ts=NOW,
    )


async def _add(
    store: InMemoryStore,
    user_id: str,
    op_id: str,
    entry_id: str,
    day: date,
    *,
    time: str | None = None,
    kind: str = "injection",
    description: str | None = None,
) -> None:
    _ = await store_data.append_event(
        store,
        user_id,
        _event(
            op_id,
            entry_id,
            AddMutation(
                action="add",
                date=day,
                time=time,
                kind=kind,
                description=description,
            ),
        ),
        scanner=FakeSanitizer(),
    )


async def _view(
    store: InMemoryStore,
    month: str = "2026-06",
    config: RunnableConfig | None = None,
) -> str:
    return await view_schedule.view_schedule_impl(
        month=month,
        config=config or _config(),
        store=store,
    )


async def _seed_june(store: InMemoryStore) -> None:
    await _add(
        store,
        "user-a",
        "op-1",
        "dose-1",
        date(2026, 6, 4),
        time="09:30",
        description="weekly dose for Alice Johnson",
    )
    await _add(
        store,
        "user-a",
        "op-2",
        "review-1",
        date(2026, 6, 9),
        kind="dose_review",
    )
    await _add(
        store,
        "user-a",
        "op-3",
        "dose-2",
        date(2026, 6, 11),
        time="08:00",
        description="weekly dose for Alice Johnson",
    )


async def test_june_entries_emit_exact_calendar_envelope_with_agent_listing() -> (
    None
):
    # Given: three active June entries, one folded-cancelled June entry.
    store = InMemoryStore()
    await _seed_june(store)
    _ = await store_data.append_event(
        store,
        "user-a",
        _event(
            "op-4",
            "dose-3",
            AddMutation(action="add", date=date(2026, 6, 20), kind="injection"),
        ),
        scanner=FakeSanitizer(),
    )
    _ = await store_data.append_event(
        store,
        "user-a",
        _event("op-5", "dose-3", CancelMutation(action="cancel")),
        scanner=FakeSanitizer(),
    )

    # When
    result = await _view(store)

    # Then
    envelope = _envelope(result)
    assert envelope["block_id"] == "calendar:2026-06"
    assert envelope["turn_scope_id"] == hashlib.sha256(
        b"thread-1|human-1"
    ).hexdigest()
    assert envelope["data"] == {
        "monthLabel": "June 2026",
        "firstWeekday": 1,
        "daysInMonth": 30,
        "highlights": [
            {"date": 4, "type": "injection"},
            {"date": 9, "type": "checkin"},
            {"date": 11, "type": "injection"},
        ],
    }
    assert envelope["text"] == (
        "dose-1 | 2026-06-04 | 09:30 | injection | weekly dose for [REDACTED_PERSON]\n"
        "review-1 | 2026-06-09 |  | dose_review | \n"
        "dose-2 | 2026-06-11 | 08:00 | injection | weekly dose for [REDACTED_PERSON]"
    )
    assert "dose-3" not in envelope["text"]
    assert "entry_id" not in json.dumps(envelope["data"])


async def test_empty_month_returns_no_highlights() -> None:
    # Given
    store = InMemoryStore()
    await _seed_june(store)

    # When
    result = await _view(store, month="2026-07")

    # Then
    envelope = _envelope(result)
    assert envelope["block_id"] == "calendar:2026-07"
    assert envelope["data"]["highlights"] == []
    assert envelope["data"]["daysInMonth"] == 31
    assert envelope["text"] == "No schedule entries for July 2026."


async def test_member_b_sees_no_entries_for_member_a() -> None:
    # Given
    store = InMemoryStore()
    await _seed_june(store)

    # When
    result = await _view(store, config=_config("user-b", thread_id="thread-b"))

    # Then
    envelope = _envelope(result)
    assert envelope["data"]["highlights"] == []
    assert envelope["text"] == "No schedule entries for June 2026."


async def test_entries_outside_requested_month_are_excluded() -> None:
    # Given
    store = InMemoryStore()
    await _add(store, "user-a", "op-may", "dose-may", date(2026, 5, 30))

    # When
    result = await _view(store, month="2026-06")

    # Then
    envelope = _envelope(result)
    assert envelope["data"]["highlights"] == []
    assert "dose-may" not in envelope["text"]


@pytest.mark.parametrize(
    "month",
    ["2026-6", "26-06", "2026-13", "2026-00", "2026/06", "june", "", "2026-06-01"],
)
async def test_malformed_month_returns_error_string(month: str) -> None:
    # Given
    store = InMemoryStore()

    # When
    result = await _view(store, month=month)

    # Then
    assert result == "Schedule unavailable: month must use YYYY-MM."


async def test_missing_authenticated_identity_raises() -> None:
    # Given
    store = InMemoryStore()
    config: RunnableConfig = {"configurable": {"thread_id": "t", "coach_human_msg_id": "h"}}

    # When / Then
    with pytest.raises(MemoryIdentityError):
        _ = await _view(store, config=config)


@pytest.mark.parametrize(
    "configurable",
    [
        {"langgraph_auth_user": {"identity": "user-a"}},
        {
            "langgraph_auth_user": {"identity": "user-a"},
            "thread_id": "thread-1",
        },
        {
            "langgraph_auth_user": {"identity": "user-a"},
            "coach_human_msg_id": "human-1",
        },
    ],
)
async def test_missing_thread_context_returns_error_string(
    configurable: dict[str, object],
) -> None:
    # Given
    store = InMemoryStore()
    config: RunnableConfig = {"configurable": configurable}

    # When
    result = await _view(store, config=config)

    # Then
    assert result == "Schedule unavailable: thread context missing."


async def test_envelope_scope_tracks_configured_thread_and_human_message() -> None:
    # Given
    store = InMemoryStore()
    await _seed_june(store)

    # When
    first = _envelope(await _view(store))
    second = _envelope(
        await _view(store, config=_config(human_msg_id="human-2"))
    )

    # Then
    assert first["turn_scope_id"] == hashlib.sha256(
        b"thread-1|human-1"
    ).hexdigest()
    assert second["turn_scope_id"] == hashlib.sha256(
        b"thread-1|human-2"
    ).hexdigest()
    assert first["turn_scope_id"] != second["turn_scope_id"]
