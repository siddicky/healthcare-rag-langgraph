from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final, TypedDict
from unittest.mock import AsyncMock

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.store.memory import InMemoryStore
from pydantic import TypeAdapter

from healthcare_rag.agent import store_data
from healthcare_rag.agent.store_data import AddMutation, ApprovalEvent
from healthcare_rag.agent.tools import log_injection as log_injection_tool
from healthcare_rag.processors.privacy import (
    PrivacySanitizer,
    PrivacyScan,
    PrivacyScanError,
)

FROZEN_THURSDAY = date(2026, 8, 20)
NEXT_MONDAY = date(2026, 8, 24)
NEXT_WEDNESDAY = date(2026, 8, 26)
NEXT_THURSDAY = date(2026, 8, 27)
FIXTURE_TS = datetime(2026, 8, 1, tzinfo=UTC)


class InjectionDay(TypedDict):
    date: str
    label: str
    status: str


class WeekstripData(TypedDict):
    medicationName: str
    doseLabel: str
    days: list[InjectionDay]


class WeekstripDataWithNext(WeekstripData, total=False):
    nextDoseLabel: str


class EnvelopePayload(TypedDict):
    turn_scope_id: str
    block_id: str
    data: WeekstripDataWithNext
    text: str


_ENVELOPE: Final = TypeAdapter(EnvelopePayload)


@dataclass(frozen=True, slots=True)
class FakeResources:
    privacy: FakePrivacy | PrivacySanitizer


@dataclass(frozen=True, slots=True)
class FakePrivacy:
    scan_value: Callable[[str], PrivacyScan]

    def scan(self, value: str) -> PrivacyScan:
        return self.scan_value(value)


def _clean_scan(value: str) -> PrivacyScan:
    return PrivacyScan(value, ())


def _name_scan(value: str) -> PrivacyScan:
    clean = value.replace("Alice Johnson", "[REDACTED_PERSON]")
    return PrivacyScan(clean, ("PERSON",)) if clean != value else PrivacyScan(value, ())


def _config(
    identity: str = "user-a",
    thread_id: str | None = "thread-1",
    human_msg_id: str | None = "human-1",
) -> RunnableConfig:
    configurable: dict[str, object] = {"langgraph_auth_user": {"identity": identity}}
    if thread_id is not None:
        configurable["thread_id"] = thread_id
    if human_msg_id is not None:
        configurable["coach_human_msg_id"] = human_msg_id
    return {"configurable": configurable}


def _frozen_today() -> date:
    return FROZEN_THURSDAY


async def _approve_add(
    store: InMemoryStore,
    *,
    entry_date: date,
    kind: str = "injection",
    op_id: str = "op-1",
    entry_id: str = "entry-1",
) -> None:
    """Build schedule state through the real fold, never by hand."""
    _ = await store_data.append_event(
        store,
        "user-a",
        ApprovalEvent(
            op_id=op_id,
            event_key="pending",
            decision="approved",
            decision_ts=FIXTURE_TS,
            entry_id=entry_id,
            mutation=AddMutation(action="add", date=entry_date, kind=kind),
            created_ts=FIXTURE_TS,
        ),
    )


async def _log(
    store: InMemoryStore,
    config: RunnableConfig,
    *,
    medication: str = "Semaglutide",
    dose: str = "1.0 mg",
) -> str:
    return await log_injection_tool.log_injection_impl(
        medication_name=medication,
        dose_label=dose,
        config=config,
        store=store,
        today=_frozen_today,
    )


def _envelope(result: str) -> EnvelopePayload:
    return _ENVELOPE.validate_json(result)


def _data(result: str) -> WeekstripDataWithNext:
    return _envelope(result)["data"]


@pytest.fixture(autouse=True)
def clean_privacy(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = FakeResources(FakePrivacy(_clean_scan))
    monkeypatch.setattr(log_injection_tool, "get_resources", lambda: resources)


async def test_no_schedule_emits_exactly_today_logged_and_no_next_dose() -> None:
    # Given a member with no approved schedule entries, frozen on a Thursday.
    store = InMemoryStore()

    # When
    result = await _log(store, _config())

    # Then
    envelope = _envelope(result)
    assert envelope["block_id"] == "weekstrip:injection"
    assert envelope["turn_scope_id"] == hashlib.sha256(b"thread-1|human-1").hexdigest()
    assert isinstance(envelope["text"], str) and envelope["text"]
    assert _data(result) == {
        "medicationName": "Semaglutide",
        "doseLabel": "1.0 mg",
        "days": [{"date": "2026-08-20", "label": "Thu", "status": "logged"}],
    }
    injections = await store_data.list_injections(store, "user-a")
    assert len(injections) == 1
    assert injections[0].medication == "Semaglutide"
    assert injections[0].date == FROZEN_THURSDAY


async def test_future_dose_schedule_adds_upcoming_day_and_next_dose_label() -> None:
    # Given an approved injection entry the Monday after the frozen Thursday.
    store = InMemoryStore()
    await _approve_add(store, entry_date=NEXT_MONDAY)

    # When
    result = await _log(store, _config())

    # Then
    assert _data(result) == {
        "medicationName": "Semaglutide",
        "doseLabel": "1.0 mg",
        "days": [
            {"date": "2026-08-20", "label": "Thu", "status": "logged"},
            {"date": "2026-08-24", "label": "Mon", "status": "upcoming"},
        ],
        "nextDoseLabel": "Monday",
    }


async def test_next_week_same_weekday_dose_stays_distinct() -> None:
    # Given an approved injection entry on next week's Thursday.
    store = InMemoryStore()
    await _approve_add(store, entry_date=NEXT_THURSDAY)

    # When
    result = await _log(store, _config())

    # Then
    assert _data(result)["days"] == [
        {"date": "2026-08-20", "label": "Thu", "status": "logged"},
        {"date": "2026-08-27", "label": "Thu", "status": "upcoming"},
    ]
    assert _data(result).get("nextDoseLabel") == "Thursday"


async def test_multiple_future_doses_are_date_sorted_and_deduplicated() -> None:
    # Given two approved entries on the same Monday plus one on Wednesday.
    store = InMemoryStore()
    await _approve_add(
        store, entry_date=NEXT_MONDAY, op_id="op-mon-1", entry_id="entry-mon-1"
    )
    await _approve_add(
        store, entry_date=NEXT_MONDAY, op_id="op-mon-2", entry_id="entry-mon-2"
    )
    await _approve_add(
        store, entry_date=NEXT_WEDNESDAY, op_id="op-wed", entry_id="entry-wed"
    )

    # When
    result = await _log(store, _config())

    # Then
    assert _data(result)["days"] == [
        {"date": "2026-08-20", "label": "Thu", "status": "logged"},
        {"date": "2026-08-24", "label": "Mon", "status": "upcoming"},
        {"date": "2026-08-26", "label": "Wed", "status": "upcoming"},
    ]


async def test_past_dose_schedule_does_not_project() -> None:
    # Given an approved injection entry dated before today.
    store = InMemoryStore()
    await _approve_add(store, entry_date=date(2026, 8, 18), op_id="op-past")

    # When
    result = await _log(store, _config())

    # Then
    assert _data(result)["days"] == [
        {"date": "2026-08-20", "label": "Thu", "status": "logged"}
    ]
    assert "nextDoseLabel" not in _data(result)


async def test_non_dose_schedule_kinds_do_not_project() -> None:
    # Given an approved check-in entry after today.
    store = InMemoryStore()
    await _approve_add(store, entry_date=NEXT_MONDAY, kind="check-in")

    # When
    result = await _log(store, _config())

    # Then
    assert _data(result)["days"] == [
        {"date": "2026-08-20", "label": "Thu", "status": "logged"}
    ]
    assert "nextDoseLabel" not in _data(result)


async def test_identifier_medication_is_scrubbed_in_store_and_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setattr(
        log_injection_tool,
        "get_resources",
        lambda: FakeResources(FakePrivacy(_name_scan)),
    )
    store = InMemoryStore()

    # When
    result = await _log(store, _config(), medication="Alice Johnson titration")

    # Then
    assert _data(result)["medicationName"] == "[REDACTED_PERSON] titration"
    assert "Alice Johnson" not in result
    injections = await store_data.list_injections(store, "user-a")
    assert [entry.medication for entry in injections] == ["[REDACTED_PERSON] titration"]


async def test_real_sanitizer_scrubs_identifier_medication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setattr(
        log_injection_tool,
        "get_resources",
        lambda: FakeResources(PrivacySanitizer()),
    )
    store = InMemoryStore()

    # When
    result = await _log(store, _config(), medication="Semaglutide 555-867-5309")

    # Then
    assert _data(result)["medicationName"] == "Semaglutide [REDACTED_PHONE]"
    assert "555-867-5309" not in result
    injections = await store_data.list_injections(store, "user-a")
    assert [entry.medication for entry in injections] == [
        "Semaglutide [REDACTED_PHONE]"
    ]


@pytest.mark.parametrize("missing", ["coach_human_msg_id", "thread_id"])
async def test_missing_turn_scope_key_raises_and_stores_nothing(missing: str) -> None:
    # Given
    store = InMemoryStore()
    config = _config(
        thread_id=None if missing == "thread_id" else "thread-1",
        human_msg_id=None if missing == "coach_human_msg_id" else "human-1",
    )

    # When / Then
    with pytest.raises(log_injection_tool.InjectionScopeError):
        _ = await _log(store, config)
    assert await store.asearch(("users", "user-a", "injection_log")) == []


async def test_missing_identity_raises_and_stores_nothing() -> None:
    # Given
    store = InMemoryStore()
    config: RunnableConfig = {
        "configurable": {"thread_id": "thread-1", "coach_human_msg_id": "human-1"}
    }

    # When / Then
    with pytest.raises(log_injection_tool.InjectionIdentityError):
        _ = await _log(store, config)
    assert await store.asearch(("users", "user-a", "injection_log")) == []


async def test_store_failure_returns_error_string_without_partial_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    store = InMemoryStore()
    monkeypatch.setattr(
        InMemoryStore,
        "aput",
        AsyncMock(side_effect=OSError("store unavailable")),
    )

    # When
    result = await _log(store, _config())

    # Then
    assert result == log_injection_tool.STORE_REFUSAL
    assert not result.startswith("{")
    assert await store.asearch(("users", "user-a", "injection_log")) == []


async def test_scanner_failure_refuses_without_storing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    def fail_scan(_value: str) -> PrivacyScan:
        raise PrivacyScanError("PRIVACY_SCAN_FAILED")

    monkeypatch.setattr(
        log_injection_tool,
        "get_resources",
        lambda: FakeResources(FakePrivacy(fail_scan)),
    )
    store = InMemoryStore()

    # When
    result = await _log(store, _config())

    # Then
    assert result == log_injection_tool.PRIVACY_REFUSAL
    assert await store.asearch(("users", "user-a", "injection_log")) == []


def test_tool_schema_exposes_only_model_args() -> None:
    # Given the Route-B registration contract (todo 9): no date or status args.
    assert (
        log_injection_tool.log_injection.args_schema
        is log_injection_tool.LogInjectionArgs
    )
    fields = log_injection_tool.LogInjectionArgs.model_fields
    assert set(fields) == {"medication_name", "dose_label"}
    assert all(field.is_required() for field in fields.values())
