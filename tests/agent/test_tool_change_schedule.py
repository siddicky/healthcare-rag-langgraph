from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TypedDict

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.store.base import Item
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command, Interrupt
from pydantic import JsonValue, TypeAdapter, ValidationError

from healthcare_rag.agent import store_data
from healthcare_rag.agent.tools.change_schedule import (
    ChangeScheduleInput,
    change_schedule,
)


class CalendarCard(TypedDict):
    eventLabel: str
    fromLabel: str
    toLabel: str
    reason: str
    status: str


class ConfirmationData(TypedDict):
    card: CalendarCard
    schedule: list[dict[str, JsonValue]]


class EnvelopePayload(TypedDict):
    turn_scope_id: str
    block_id: str
    data: ConfirmationData
    text: str


class InterruptResult(TypedDict):
    __interrupt__: tuple[Interrupt, ...]


@dataclass(frozen=True, slots=True)
class ToolHarness:
    graph: CompiledStateGraph[MessagesState, None, MessagesState, MessagesState]
    store: InMemoryStore
    config: RunnableConfig


def _harness(
    *,
    store: InMemoryStore | None = None,
    thread_id: str = "thread-1",
    user_id: str = "user-1",
    human_msg_id: str = "human-1",
) -> ToolHarness:
    runtime_store = store or InMemoryStore()
    builder = StateGraph(MessagesState)
    _ = builder.add_node("tools", ToolNode([change_schedule], handle_tool_errors=False))
    _ = builder.add_edge(START, "tools")
    _ = builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=InMemorySaver(), store=runtime_store)
    config: RunnableConfig = {
        "configurable": {
            "thread_id": thread_id,
            "coach_human_msg_id": human_msg_id,
            "langgraph_auth_user": {"identity": user_id},
        }
    }
    return ToolHarness(graph=graph, store=runtime_store, config=config)


def _tool_input(
    request: dict[str, JsonValue], call_id: str = "call-1"
) -> MessagesState:
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "change_schedule",
                        "args": {"request": request},
                        "id": call_id,
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }


async def _decide(
    harness: ToolHarness,
    request: dict[str, JsonValue],
    *,
    accept: bool,
    call_id: str = "call-1",
) -> tuple[CalendarCard, EnvelopePayload]:
    interrupted = await harness.graph.ainvoke(
        _tool_input(request, call_id), harness.config
    )
    pending = TypeAdapter(CalendarCard).validate_python(
        TypeAdapter(InterruptResult)
        .validate_python(interrupted)["__interrupt__"][0]
        .value
    )
    resumed = await harness.graph.ainvoke(
        Command(resume={"accept": accept}), harness.config
    )
    message = resumed["messages"][-1]
    assert isinstance(message, ToolMessage)
    assert isinstance(message.content, str)
    envelope = TypeAdapter(EnvelopePayload).validate_json(message.content)
    return pending, envelope


async def _seed_add(
    store: InMemoryStore,
    *,
    user_id: str,
    op_id: str,
    entry_id: str,
    day: date,
    description: str,
    created_ts: datetime,
) -> None:
    await store_data.append_event(
        store,
        user_id,
        store_data.ApprovalEvent(
            op_id=op_id,
            event_key="",
            decision="approved",
            decision_ts=created_ts,
            entry_id=entry_id,
            mutation=store_data.AddMutation(
                action="add",
                date=day,
                time="09:00",
                kind="check-in",
                description=description,
            ),
            created_ts=created_ts,
        ),
    )


async def _apply(
    store: InMemoryStore,
    request: dict[str, JsonValue],
    *,
    call_id: str,
    thread_id: str = "lifecycle",
) -> EnvelopePayload:
    _, envelope = await _decide(
        _harness(
            store=store,
            thread_id=thread_id,
            human_msg_id=f"human-{call_id}",
        ),
        request,
        accept=True,
        call_id=call_id,
    )
    return envelope


def test_change_schedule_schema_rejects_cross_action_fields() -> None:
    # Given
    schema = change_schedule.args_schema

    # When / Then
    assert schema is not None
    assert schema is ChangeScheduleInput
    assert "request" in ChangeScheduleInput.model_json_schema()["properties"]
    with pytest.raises(ValidationError):
        ChangeScheduleInput.model_validate(
            {
                "request": {
                    "action": "add",
                    "date": "2026-08-22",
                    "kind": "injection",
                    "target": "forbidden",
                }
            }
        )


@pytest.mark.parametrize(
    "raw_request",
    [
        {"action": "unknown", "date": "2026-08-22", "kind": "injection"},
        {"action": "add", "date": "2026-08-22", "kind": "unknown"},
        {"action": "add", "date": "2026-08-22"},
        {
            "action": "cancel",
            "target": "entry-1",
            "destination": {"date": "2026-08-23"},
        },
    ],
)
def test_change_schedule_schema_rejects_off_contract_variants(
    raw_request: dict[str, JsonValue],
) -> None:
    # Given
    schema = change_schedule.args_schema

    # When / Then
    assert schema is not None
    with pytest.raises(ValidationError):
        ChangeScheduleInput.model_validate({"request": raw_request})


@pytest.mark.asyncio
async def test_add_interrupts_then_approved_event_confirms_same_card() -> None:
    # Given
    harness = _harness()
    request: dict[str, JsonValue] = {
        "action": "add",
        "date": "2026-08-22",
        "time": "09:30",
        "kind": "injection",
        "description": "weekly injection",
    }

    # When
    interrupted = await harness.graph.ainvoke(_tool_input(request), harness.config)
    resumed = await harness.graph.ainvoke(
        Command(resume={"accept": True}), harness.config
    )

    # Then
    pending = interrupted["__interrupt__"][0].value
    assert pending == {
        "eventLabel": "weekly injection",
        "fromLabel": "Not scheduled",
        "toLabel": "Sat, Aug 22 · 9:30 AM UTC",
        "reason": "Add this event to your schedule.",
        "status": "pending",
    }
    message = resumed["messages"][-1]
    assert isinstance(message, ToolMessage)
    assert isinstance(message.content, str)
    envelope = json.loads(message.content)
    assert envelope["data"]["card"] == {**pending, "status": "confirmed"}
    events = harness.store.search(("users", "user-1", "events"), limit=10)
    assert len(events) == 1
    assert events[0].value["decision"] == "approved"
    assert harness.store.search(("users", "user-1", "schedule"), limit=10) == []


@pytest.mark.asyncio
async def test_decline_and_malformed_resume_preserve_ledger_contract() -> None:
    # Given
    request: dict[str, JsonValue] = {
        "action": "add",
        "date": "2026-08-23",
        "kind": "appointment",
    }
    declined_harness = _harness(thread_id="decline")
    malformed_harness = _harness(thread_id="malformed")

    # When
    _, declined = await _decide(declined_harness, request, accept=False)
    _ = await malformed_harness.graph.ainvoke(
        _tool_input(request), malformed_harness.config
    )
    malformed = await malformed_harness.graph.ainvoke(
        Command(resume={"accept": True, "fields": []}), malformed_harness.config
    )

    # Then
    assert declined["data"]["card"]["status"] == "declined"
    malformed_message = malformed["messages"][-1]
    assert isinstance(malformed_message, ToolMessage)
    assert "remains pending" in malformed_message.content
    assert malformed_harness.store.search(("users", "user-1", "events"), limit=10) == []
    ops = malformed_harness.store.search(("users", "user-1", "ops"), limit=10)
    assert len(ops) == 1
    assert ops[0].value["status"] == "pending"


@pytest.mark.asyncio
async def test_interrupt_is_scrubbed_and_byte_equal_to_persisted_card() -> None:
    # Given
    harness = _harness(thread_id="scrub")
    request: dict[str, JsonValue] = {
        "action": "add",
        "date": "2026-08-24",
        "kind": "check-in",
        "description": "Alice Johnson check-in",
    }

    # When
    interrupted = await harness.graph.ainvoke(_tool_input(request), harness.config)

    # Then
    payload = (
        TypeAdapter(InterruptResult)
        .validate_python(interrupted)["__interrupt__"][0]
        .value
    )
    ops = harness.store.search(("users", "user-1", "ops"), limit=10)
    assert len(ops) == 1
    assert payload == ops[0].value["interrupt_payload"]
    assert "Alice Johnson" not in json.dumps(payload)
    assert len(ops[0].key) == 64
    assert len(ops[0].value["resolved_entry_id"]) == 64


@pytest.mark.asyncio
async def test_calendar_card_fields_match_design_contract() -> None:
    # Given
    contract = json.loads(
        Path("tests/fixtures/calendar_change_card_contract.json").read_text()
    )
    prompt_fields = set(contract["interrupt_props"])
    harness = _harness(thread_id="card-contract")

    # When
    pending_result = await harness.graph.ainvoke(
        _tool_input(
            {
                "action": "add",
                "date": "2026-08-25",
                "kind": "check-in",
            }
        ),
        harness.config,
    )
    pending = (
        TypeAdapter(InterruptResult)
        .validate_python(pending_result)["__interrupt__"][0]
        .value
    )

    # Then
    assert set(pending) == prompt_fields | set(contract["post_decision_props"])


@pytest.mark.asyncio
async def test_target_resolution_fails_closed_and_freezes_unique_entry() -> None:
    # Given
    store = InMemoryStore()
    now = datetime(2026, 8, 21, 12, tzinfo=UTC)
    await _seed_add(
        store,
        user_id="user-1",
        op_id="seed-a",
        entry_id="entry-a",
        day=date(2026, 8, 22),
        description="Friday check-in",
        created_ts=now,
    )
    await _seed_add(
        store,
        user_id="user-1",
        op_id="seed-b",
        entry_id="entry-b",
        day=date(2026, 8, 23),
        description="Weekend check-in",
        created_ts=now + timedelta(seconds=1),
    )
    ambiguous = _harness(store=store, thread_id="ambiguous")
    unique = _harness(store=store, thread_id="frozen")

    # When
    failed = await ambiguous.graph.ainvoke(
        _tool_input({"action": "cancel", "target": "check-in"}), ambiguous.config
    )
    _ = await unique.graph.ainvoke(
        _tool_input(
            {
                "action": "reschedule",
                "target": "entry-a",
                "destination": {"date": "2026-08-30", "time": "10:00"},
            }
        ),
        unique.config,
    )
    await store_data.append_event(
        store,
        "user-1",
        store_data.ApprovalEvent(
            op_id="interleaving-cancel",
            event_key="",
            decision="approved",
            decision_ts=now,
            entry_id="entry-a",
            mutation=store_data.CancelMutation(action="cancel"),
            created_ts=now,
        ),
    )
    resumed = await unique.graph.ainvoke(
        Command(resume={"accept": True}), unique.config
    )

    # Then
    failed_message = failed["messages"][-1]
    assert isinstance(failed_message, ToolMessage)
    assert "entry-a" in failed_message.content and "entry-b" in failed_message.content
    result_message = resumed["messages"][-1]
    assert isinstance(result_message, ToolMessage)
    assert isinstance(result_message.content, str)
    result = json.loads(result_message.content)
    assert result["data"]["card"]["status"] == "declined"
    ops = store.search(("users", "user-1", "ops"), limit=20)
    frozen = next(item for item in ops if item.value["resolved_entry_id"] == "entry-a")
    assert frozen.value["status"] == "declined-stale"


@pytest.mark.asyncio
async def test_crash_replay_reuses_existing_event_and_terminalizes() -> None:
    # Given
    store = InMemoryStore()
    request: dict[str, JsonValue] = {
        "action": "add",
        "date": "2026-09-01",
        "kind": "injection",
    }
    first = _harness(store=store, thread_id="replay")
    _ = await first.graph.ainvoke(_tool_input(request), first.config)
    op_item = store.search(("users", "user-1", "ops"), limit=10)[0]
    await store_data.append_event(
        store,
        "user-1",
        store_data.ApprovalEvent(
            op_id=op_item.key,
            event_key="",
            decision="approved",
            decision_ts=datetime.now(UTC),
            entry_id=op_item.value["resolved_entry_id"],
            mutation=store_data.AddMutation(
                action="add",
                date=date(2026, 9, 1),
                kind="injection",
            ),
            created_ts=datetime.now(UTC),
        ),
    )

    # When
    replayed = await first.graph.ainvoke(Command(resume={"accept": True}), first.config)

    # Then
    message = replayed["messages"][-1]
    assert isinstance(message, ToolMessage)
    assert isinstance(message.content, str)
    assert json.loads(message.content)["data"]["card"]["status"] == "confirmed"
    assert len(store.search(("users", "user-1", "events"), limit=10)) == 1
    assert (
        store.search(("users", "user-1", "ops"), limit=10)[0].value["status"]
        == "applied"
    )


@pytest.mark.asyncio
async def test_lifecycle_reactivation_supersession_independence_and_non_merge() -> None:
    # Given
    store = InMemoryStore()
    original: dict[str, JsonValue] = {
        "action": "add",
        "date": "2026-09-05",
        "time": "09:00",
        "kind": "check-in",
        "description": "weekly check-in",
    }
    _ = await _apply(store, original, call_id="add-1")
    entry_id = (await store_data.list_schedule(store, "user-1"))[0].entry_id

    # When
    _ = await _apply(
        store,
        {"action": "cancel", "target": entry_id},
        call_id="cancel-1",
    )
    _ = await _apply(store, original, call_id="add-2")
    reactivated = (await store_data.schedule_state(store, "user-1"))[entry_id]
    _ = await _apply(
        store,
        {
            "action": "reschedule",
            "target": entry_id,
            "destination": {"date": "2026-09-06", "time": "10:00"},
        },
        call_id="move-1",
    )
    _ = await _apply(
        store,
        {
            "action": "reschedule",
            "target": entry_id,
            "destination": {"date": "2026-09-07", "time": "11:00"},
        },
        call_id="move-2",
    )
    _ = await _apply(
        store,
        {
            "action": "add",
            "date": "2026-09-07",
            "kind": "appointment",
            "description": "same-date appointment",
        },
        call_id="add-independent",
    )
    _ = await _apply(
        store,
        {
            "action": "add",
            "date": "2026-09-07",
            "kind": "check-in",
            "description": "different content",
        },
        call_id="add-non-merge",
    )
    final = await store_data.list_schedule(store, "user-1")

    # Then
    assert reactivated.active is True
    moved = next(entry for entry in final if entry.entry_id == entry_id)
    assert (moved.date, moved.time) == (date(2026, 9, 7), "11:00")
    assert len(final) == 3
    assert len({entry.entry_id for entry in final}) == 3


@pytest.mark.asyncio
async def test_same_add_across_two_threads_has_independent_ops() -> None:
    # Given
    store = InMemoryStore()
    request: dict[str, JsonValue] = {
        "action": "add",
        "date": "2026-09-10",
        "kind": "injection",
    }

    # When
    _ = await _apply(store, request, call_id="same-call", thread_id="thread-a")
    _ = await _apply(store, request, call_id="same-call", thread_id="thread-b")

    # Then
    ops = store.search(("users", "user-1", "ops"), limit=10)
    events = store.search(("users", "user-1", "events"), limit=10)
    assert len(ops) == len(events) == 2
    assert ops[0].key != ops[1].key
    assert len({item.value["resolved_entry_id"] for item in ops}) == 1


@pytest.mark.asyncio
async def test_tool_events_fold_beyond_page_size_without_merging_dates() -> None:
    # Given
    store = InMemoryStore()
    start = date(2026, 10, 1)
    for index in range(store_data.PAGE_SIZE + 3):
        await _seed_add(
            store,
            user_id="user-pages",
            op_id=f"page-op-{index}",
            entry_id=f"page-entry-{index}",
            day=start + timedelta(days=index),
            description=f"check-in {index}",
            created_ts=datetime(2026, 8, 21, 12, tzinfo=UTC) + timedelta(seconds=index),
        )

    # When
    state = await store_data.schedule_state(store, "user-pages", page_size=7)

    # Then
    assert len(state) == store_data.PAGE_SIZE + 3
    assert state["page-entry-0"].date != state["page-entry-1"].date


@pytest.mark.asyncio
async def test_same_entry_order_uses_timestamp_then_max_event_key() -> None:
    # Given
    store = InMemoryStore()
    user_id = "user-order"
    namespace = ("users", user_id, "events")
    instant = datetime(2027, 8, 21, 12, tzinfo=UTC)
    await _seed_add(
        store,
        user_id=user_id,
        op_id="initial",
        entry_id="same-entry",
        day=date(2026, 9, 1),
        description="ordered entry",
        created_ts=instant,
    )
    for index, day in enumerate((date(2026, 9, 2), date(2026, 9, 3)), start=1):
        event = store_data.ApprovalEvent(
            op_id=f"move-{index}",
            event_key=store_data.event_key_for(user_id, f"move-{index}"),
            decision="approved",
            decision_ts=instant,
            entry_id="same-entry",
            mutation=store_data.RescheduleMutation(
                action="reschedule", destination_date=day
            ),
            created_ts=instant,
        )
        key = event.event_key
        store._data[namespace][key] = Item(
            value=event.model_dump(mode="json"),
            key=key,
            namespace=namespace,
            created_at=instant + timedelta(seconds=index),
            updated_at=instant + timedelta(seconds=index),
        )
    low_key, high_key = sorted(
        (
            store_data.event_key_for(user_id, "tie-add"),
            store_data.event_key_for(user_id, "tie-cancel"),
        )
    )
    tie_events = (
        store_data.ApprovalEvent(
            op_id="tie-add",
            event_key=low_key,
            decision="approved",
            decision_ts=instant,
            entry_id="tie-entry",
            mutation=store_data.AddMutation(
                action="add", date=date(2026, 9, 5), kind="appointment"
            ),
            created_ts=instant,
        ),
        store_data.ApprovalEvent(
            op_id="tie-cancel",
            event_key=high_key,
            decision="approved",
            decision_ts=instant,
            entry_id="tie-entry",
            mutation=store_data.CancelMutation(action="cancel"),
            created_ts=instant,
        ),
    )
    for event in tie_events:
        store._data[namespace][event.event_key] = Item(
            value=event.model_dump(mode="json"),
            key=event.event_key,
            namespace=namespace,
            created_at=instant + timedelta(seconds=3),
            updated_at=instant + timedelta(seconds=3),
        )

    # When
    state = await store_data.schedule_state(store, user_id, page_size=1)

    # Then
    assert state["same-entry"].date == date(2026, 9, 3)
    assert state["tie-entry"].active is False
