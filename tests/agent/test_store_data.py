from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TypedDict

import pytest
from langgraph.store.base import Item
from langgraph.store.memory import InMemoryStore
from pydantic import JsonValue, TypeAdapter

from healthcare_rag.agent import store_data
from healthcare_rag.agent.store_data import (
    AddMutation,
    ApprovalEvent,
    CancelMutation,
    ErasureGateError,
    InjectionLogEntry,
    MetricEntry,
    OpRecord,
    ReminderCapError,
    ReminderEdit,
    ReminderRecord,
    RescheduleMutation,
    StoreNamespaceError,
    UploadRegistryRecord,
    Weekday,
)
from healthcare_rag.processors.privacy import (
    PrivacySanitizer,
    PrivacyScan,
    PrivacyScanError,
)


class FakeSanitizer:
    def scan(self, text: str) -> PrivacyScan:
        return PrivacyScan(
            text.replace("Alice Johnson", "[REDACTED_PERSON]").replace(
                "555-867-5309", "[REDACTED_PHONE_NUMBER]"
            ),
            (),
        )


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _event(
    op_id: str,
    entry_id: str,
    mutation: AddMutation | RescheduleMutation | CancelMutation,
    *,
    decision: store_data.EventDecision = "approved",
) -> ApprovalEvent:
    return ApprovalEvent(
        op_id=op_id,
        event_key="ignored-client-key",
        decision=decision,
        decision_ts=NOW,
        entry_id=entry_id,
        mutation=mutation,
        created_ts=NOW,
    )


async def test_schedule_is_derived_from_append_cancel_readd_and_stale_reschedule() -> (
    None
):
    # Given
    store = InMemoryStore()
    add = AddMutation(
        action="add",
        date=date(2026, 8, 22),
        time="09:30",
        kind="injection",
        description="weekly dose",
    )
    _ = await store_data.append_event(store, "user-1", _event("op-1", "dose-1", add))

    # When / Then: append derives an active entry.
    state = await store_data.schedule_state(store, "user-1", page_size=1)
    assert state["dose-1"].active is True

    # When / Then: cancel folds to inactive.
    _ = await store_data.append_event(
        store,
        "user-1",
        _event("op-2", "dose-1", CancelMutation(action="cancel")),
    )
    assert (await store_data.schedule_state(store, "user-1"))["dose-1"].active is False

    # When / Then: rescheduling an inactive prefix is declined-stale and a no-op.
    stale = await store_data.append_event(
        store,
        "user-1",
        _event(
            "op-3",
            "dose-1",
            RescheduleMutation(
                action="reschedule",
                destination_date=date(2026, 8, 29),
                destination_time="10:00",
            ),
        ),
    )
    assert stale.decision == "declined-stale"
    assert (await store_data.schedule_state(store, "user-1"))["dose-1"].date == date(
        2026, 8, 22
    )

    # When / Then: a later add resets the same entry to active.
    _ = await store_data.append_event(store, "user-1", _event("op-4", "dose-1", add))
    assert (await store_data.schedule_state(store, "user-1"))["dose-1"].active is True


async def test_schedule_fold_paginates_and_breaks_equal_timestamp_ties_by_event_key() -> (
    None
):
    # Given
    store = InMemoryStore()
    for index in range(13):
        event = _event(
            f"page-op-{index}",
            f"entry-{index}",
            AddMutation(
                action="add",
                date=date(2026, 9, 1) + timedelta(days=index),
                time="08:00",
                kind="check-in",
            ),
        )
        _ = await store_data.append_event(store, "user-pages", event)

    first_key = store_data.event_key_for("user-tie", "a")
    second_key = store_data.event_key_for("user-tie", "z")
    low_key, high_key = sorted((first_key, second_key))
    namespace = ("users", "user-tie", "events")
    add_value = (
        _event(
            "a" if first_key == low_key else "z",
            "same-entry",
            AddMutation(
                action="add",
                date=date(2026, 9, 1),
                time="08:00",
                kind="check-in",
            ),
        )
        .model_copy(update={"event_key": low_key})
        .model_dump(mode="json")
    )
    cancel_value = (
        _event(
            "z" if second_key == high_key else "a",
            "same-entry",
            CancelMutation(action="cancel"),
        )
        .model_copy(update={"event_key": high_key})
        .model_dump(mode="json")
    )
    store._data[namespace][low_key] = Item(
        value=add_value,
        key=low_key,
        namespace=namespace,
        created_at=NOW,
        updated_at=NOW,
    )
    store._data[namespace][high_key] = Item(
        value=cancel_value,
        key=high_key,
        namespace=namespace,
        created_at=NOW,
        updated_at=NOW,
    )

    # When
    paged = await store_data.list_schedule(store, "user-pages", page_size=3)
    tied = await store_data.schedule_state(store, "user-tie", page_size=1)

    # Then
    assert len(paged) == 13
    assert tied["same-entry"].active is False


async def test_schedule_readers_list_resolve_and_find_next_dose() -> None:
    # Given
    store = InMemoryStore()
    for op_id, entry_id, day, kind in (
        ("op-a", "dose-a", 23, "injection"),
        ("op-b", "visit-b", 22, "check-in"),
    ):
        _ = await store_data.append_event(
            store,
            "user-1",
            _event(
                op_id,
                entry_id,
                AddMutation(
                    action="add",
                    date=date(2026, 8, day),
                    time="09:00",
                    kind=kind,
                    description=f"{kind} appointment",
                ),
            ),
        )

    # When
    listing = await store_data.list_schedule(store, "user-1")
    resolved = await store_data.resolve_target(store, "user-1", "dose-a")
    next_entry = await store_data.next_dose(store, "user-1", date(2026, 8, 22))

    # Then
    assert [entry.entry_id for entry in listing] == ["visit-b", "dose-a"]
    assert resolved == listing[1]
    assert next_entry == listing[1]


async def test_event_log_is_append_only_and_lookup_by_op_is_direct() -> None:
    # Given
    store = InMemoryStore()
    event = _event(
        "same-op",
        "entry-1",
        AddMutation(
            action="add",
            date=date(2026, 8, 22),
            time="09:30",
            kind="check-in",
        ),
    )

    # When
    first = await store_data.append_event(store, "user-1", event)
    replay = await store_data.append_event(store, "user-1", event)
    found = await store_data.get_event_for_op(store, "user-1", "same-op")

    # Then
    assert replay == first == found
    assert len(await store.asearch(("users", "user-1", "events"), limit=20)) == 1


async def test_op_ledger_put_if_absent_round_trips_declined_stale() -> None:
    # Given
    store = InMemoryStore()
    op = OpRecord(
        op_id="op-1",
        status="declined-stale",
        result={"message": "Alice Johnson request was stale"},
        created_ts=NOW,
        resolved_entry_id="entry-1",
        frozen_request={"action": "cancel"},
        interrupt_payload={"eventLabel": "Alice Johnson visit"},
    )

    # When
    inserted = await store_data.put_op_if_absent(store, "user-1", op, FakeSanitizer())
    duplicate = await store_data.put_op_if_absent(
        store,
        "user-1",
        op.model_copy(update={"status": "applied"}),
        FakeSanitizer(),
    )
    loaded = await store_data.get_op(store, "user-1", "op-1")

    # Then
    assert inserted is True
    assert duplicate is False
    assert loaded is not None
    assert loaded.status == "declined-stale"
    assert "Alice Johnson" not in json.dumps(loaded.model_dump(mode="json"))


def _reminder(index: int, *, active: bool = True) -> ReminderRecord:
    return ReminderRecord(
        reminder_id=f"reminder-{index}",
        title=f"Alice Johnson reminder {index}",
        weekday=Weekday.MON,
        time="08:30",
        timezone="UTC",
        active=active,
        cron_id=None,
        thread_id=f"thread-{index % 2}",
        wake_token=f"token-{index}",
        next_run_date=date(2026, 8, 24),
        created_ts=NOW + timedelta(seconds=index),
    )


async def test_reminders_create_list_edit_rotate_token_cap_and_soft_cancel() -> None:
    # Given
    store = InMemoryStore()
    for index in range(10):
        _ = await store_data.create_reminder(
            store, "user-1", _reminder(index), FakeSanitizer()
        )

    # When / Then: cap spans every thread in the user's namespace.
    with pytest.raises(ReminderCapError):
        _ = await store_data.create_reminder(
            store, "user-1", _reminder(10), FakeSanitizer()
        )

    old_token = _reminder(0).wake_token
    edited = await store_data.edit_reminder(
        store,
        "user-1",
        "reminder-0",
        ReminderEdit(weekday=Weekday.FRI, time="09:45", title="call 555-867-5309"),
        FakeSanitizer(),
    )
    cancelled = await store_data.soft_cancel_reminder(store, "user-1", "reminder-1")
    reminders = await store_data.list_reminders(store, "user-1", page_size=3)

    assert edited.weekday is Weekday.FRI
    assert edited.wake_token != old_token
    assert "555-867-5309" not in edited.title
    assert cancelled.active is False
    assert len(reminders) == 10


async def test_metric_and_injection_helpers_scrub_and_sort_by_date() -> None:
    # Given
    store = InMemoryStore()
    metrics = (
        MetricEntry(
            metric_id="metric-2",
            metric="weight for Alice Johnson",
            value=180,
            unit="lb",
            date=date(2026, 8, 22),
            note="call 555-867-5309",
            created_ts=NOW,
        ),
        MetricEntry(
            metric_id="metric-1",
            metric="weight",
            value=181,
            unit="lb",
            date=date(2026, 8, 20),
            created_ts=NOW,
        ),
    )
    injections = (
        InjectionLogEntry(
            injection_id="injection-2",
            medication="Alice Johnson medication",
            date=date(2026, 8, 22),
            note=None,
            created_ts=NOW,
        ),
        InjectionLogEntry(
            injection_id="injection-1",
            medication="reported injection",
            date=date(2026, 8, 19),
            note=None,
            created_ts=NOW,
        ),
    )
    for metric in metrics:
        await store_data.add_metric(store, "user-1", metric, FakeSanitizer())
    for injection in injections:
        await store_data.add_injection(store, "user-1", injection, FakeSanitizer())

    # When
    metric_range = await store_data.metric_range(
        store, "user-1", date(2026, 8, 19), date(2026, 8, 22), page_size=1
    )
    injection_range = await store_data.injection_range(
        store, "user-1", date(2026, 8, 19), date(2026, 8, 22), page_size=1
    )

    # Then
    assert [entry.date for entry in metric_range] == sorted(
        entry.date for entry in metric_range
    )
    assert [entry.date for entry in injection_range] == sorted(
        entry.date for entry in injection_range
    )
    assert "Alice Johnson" not in json.dumps(
        [item.model_dump(mode="json") for item in metric_range]
    )
    assert "Alice Johnson" not in json.dumps(
        [item.model_dump(mode="json") for item in injection_range]
    )


async def test_real_privacy_scan_error_stores_nothing() -> None:
    # Given
    store = InMemoryStore()
    oversized = "x" * (16 * 1024 + 1)
    metric = MetricEntry(
        metric_id="metric-1",
        metric=oversized,
        value=1,
        unit="lb",
        date=date(2026, 8, 21),
        created_ts=NOW,
    )

    # When / Then
    with pytest.raises(PrivacyScanError):
        await store_data.add_metric(store, "user-1", metric, PrivacySanitizer())
    assert await store.asearch(("users", "user-1", "metrics"), limit=10) == []


async def test_upload_registry_is_owner_bound_scrubbed_and_expired_in_code() -> None:
    # Given
    store = InMemoryStore()
    record = UploadRegistryRecord(
        owner="user-1",
        intended_thread="thread-1",
        expires_at=NOW + timedelta(minutes=15),
        status="done",
        proposal={"sourceLabel": "Alice Johnson document"},
    )
    await store_data.put_upload_registry(
        store, "user-1", "reservation-1", record, FakeSanitizer()
    )

    # When
    active = await store_data.get_upload_registry(store, "user-1", "reservation-1", NOW)
    expired = await store_data.get_upload_registry(
        store, "user-1", "reservation-1", NOW + timedelta(minutes=16)
    )

    # Then
    assert active is not None
    assert active.owner == "user-1"
    assert "Alice Johnson" not in json.dumps(active.model_dump(mode="json"))
    assert expired is None
    assert (
        await store.aget(("users", "user-1", "upload_registry"), "reservation-1")
        is None
    )


def test_writable_collection_allowlist_is_exact() -> None:
    # Given / When / Then
    assert store_data.WRITABLE_COLLECTIONS == {
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


class EnvelopePayload(TypedDict):
    turn_scope_id: str
    block_id: str
    data: JsonValue
    text: str


def test_make_envelope_has_stable_turn_scope() -> None:
    # Given / When
    parallel_a = TypeAdapter(EnvelopePayload).validate_json(
        store_data.make_envelope("thread-1", "human-1", "a", {"x": 1}, "one")
    )
    parallel_b = TypeAdapter(EnvelopePayload).validate_json(
        store_data.make_envelope("thread-1", "human-1", "b", {"x": 2}, "two")
    )
    next_turn = TypeAdapter(EnvelopePayload).validate_json(
        store_data.make_envelope("thread-1", "human-2", "a", {}, "next")
    )
    resumed = TypeAdapter(EnvelopePayload).validate_json(
        store_data.make_envelope("thread-1", "human-1", "a", {}, "resume")
    )

    # Then
    assert parallel_a["turn_scope_id"] == parallel_b["turn_scope_id"]
    assert parallel_a["turn_scope_id"] != next_turn["turn_scope_id"]
    assert parallel_a["turn_scope_id"] == resumed["turn_scope_id"]


async def test_erasure_gate_blocks_ordinary_writes_and_privileged_delete_is_paginated() -> (
    None
):
    # Given
    store = InMemoryStore()
    for index in range(13):
        await store.aput(
            ("users", "user-1", "feedback"), f"feedback-{index}", {"index": index}
        )
    await store.aput(("users", "user-2", "feedback"), "keep", {"index": 1})
    await store.aput(("users", "user-1", "gate"), "erasing", {"active": True})
    metric = MetricEntry(
        metric_id="blocked",
        metric="weight",
        value=180,
        unit="lb",
        date=date(2026, 8, 21),
        created_ts=NOW,
    )

    # When / Then: ordinary writes fail closed.
    with pytest.raises(ErasureGateError):
        await store_data.add_metric(store, "user-1", metric, FakeSanitizer())
    with pytest.raises(store_data.ErasureCapabilityError):
        await store_data.delete_all_for_user(store, "user-1", None, page_size=3)

    await store_data.delete_all_for_user(
        store,
        "user-1",
        store_data._coordinator_capability(),
        page_size=3,
    )

    assert await store.asearch(("users", "user-1", "feedback"), limit=20) == []
    assert await store.aget(("users", "user-1", "gate"), "erasing") is not None
    assert await store.aget(("users", "user-2", "feedback"), "keep") is not None


async def test_erasure_rejects_malformed_user_namespace_before_deleting() -> None:
    # Given
    store = InMemoryStore()
    await store.aput(("users", "user-1", "feedback"), "keep-on-error", {"value": "x"})
    await store.aput(("users", "user-1", "unexpected"), "bad", {"value": "x"})

    # When / Then
    with pytest.raises(StoreNamespaceError):
        await store_data.delete_all_for_user(
            store,
            "user-1",
            store_data._coordinator_capability(),
            page_size=1,
        )
    assert (
        await store.aget(("users", "user-1", "feedback"), "keep-on-error") is not None
    )


def test_agent_store_code_has_no_schedule_collection_literal() -> None:
    # Given
    source = Path(store_data.__file__).read_text(encoding="utf-8")

    # When / Then
    assert '("users", user_id, "schedule")' not in source
