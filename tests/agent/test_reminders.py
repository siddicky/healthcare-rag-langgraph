from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import cast

import httpx
import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage
from langgraph.store.memory import InMemoryStore
from langgraph.types import StreamWriter
from pydantic import JsonValue

from healthcare_rag.agent import store_data
from healthcare_rag.agent.build import build_coach_graph
from healthcare_rag.agent.cron_client import CronClient, CronCreate
from healthcare_rag.agent.reminders import (
    cancel_reminder_impl,
    cleanup_user_crons,
    create_reminder_impl,
    edit_reminder_impl,
    reminder_delivery,
    sweep_upload_reservations,
)
from healthcare_rag.agent.state import CoachState, CronWakePayload

# noqa: SIZE_OK - acceptance pins the complete reminder matrix to this file.


def _runtime(
    store: InMemoryStore, *, thread_id: str = "thread-1"
) -> ToolRuntime[None, CoachState]:
    state: CoachState = {"messages": []}

    def writer(_chunk: JsonValue) -> None:
        return None

    return ToolRuntime(
        state=state,
        context=None,
        config={
            "configurable": {
                "thread_id": thread_id,
                "coach_human_msg_id": "human-1",
                "langgraph_auth_user": {"identity": "user-1"},
            }
        },
        stream_writer=cast("StreamWriter", writer),
        tool_call_id="call-1",
        store=store,
    )


def _cron_payload(
    cron_id: str = "cron-1", *, enabled: bool = True
) -> dict[str, JsonValue]:
    return {
        "cron_id": cron_id,
        "thread_id": "thread-1",
        "schedule": "0 9 * * 1",
        "timezone": "UTC",
        "enabled": enabled,
        "next_run_date": "2026-08-24T09:00:00Z",
        "metadata": {"reminder_id": "reminder-1", "user_id": "user-1"},
    }


def _envelope(result: str) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", json.loads(result))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("weekday", "time", "expected"),
    [("Mon", "09:00", "0 9 * * 1"), ("Sun", "23:05", "5 23 * * 0")],
)
async def test_cron_client_creates_authenticated_thread_cron(
    weekday: str, time: str, expected: str
) -> None:
    # Given
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_cron_payload(), request=request)

    async with httpx.AsyncClient(
        base_url="https://deployment.test", transport=httpx.MockTransport(handler)
    ) as http:
        client = CronClient(http=http, api_key="platform", internal_token="internal")

        # When
        cron = await client.create(
            CronCreate(
                reminder_id="reminder-1",
                user_id="user-1",
                thread_id="thread-1",
                wake_token="secret",
                weekday=store_data.Weekday(weekday),
                time=time,
                timezone="UTC",
                enabled=True,
            )
        )

    # Then
    request = requests[0]
    body = cast("dict[str, JsonValue]", json.loads(request.content))
    assert request.url.path == "/threads/thread-1/runs/crons"
    assert request.headers["x-api-key"] == "platform"
    assert request.headers["x-internal-token"] == "internal"
    assert request.headers["x-internal-owner"] == "user-1"
    assert body["schedule"] == expected
    assert body["assistant_id"] == "coach"
    assert body["multitask_strategy"] == "enqueue"
    input_payload = cast("dict[str, JsonValue]", body["input"])
    wake_payload = cast("dict[str, JsonValue]", input_payload["cron_wake"])
    assert wake_payload["wake_token"] == "secret"
    assert cron.next_run_date == "2026-08-24T09:00:00Z"


@pytest.mark.asyncio
async def test_search_by_metadata_paginates_to_exhaustion() -> None:
    # Given
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = cast("dict[str, JsonValue]", json.loads(request.content))
        offset = cast("int", body["offset"])
        offsets.append(offset)
        items = [_cron_payload(f"cron-{offset + index}") for index in range(2)]
        if offset == 2:
            items = [_cron_payload("cron-2")]
        return httpx.Response(200, json={"items": items}, request=request)

    async with httpx.AsyncClient(
        base_url="https://deployment.test", transport=httpx.MockTransport(handler)
    ) as http:
        client = CronClient(
            http=http, api_key="platform", internal_token="internal", page_size=2
        )

        # When
        crons = await client.search(metadata={"user_id": "user-1"}, owner="user-1")

    # Then
    assert offsets == [0, 2]
    assert [cron.cron_id for cron in crons] == ["cron-0", "cron-1", "cron-2"]


@pytest.mark.asyncio
async def test_create_is_pending_inactive_before_cron_then_finalizes() -> None:
    # Given
    store = InMemoryStore()
    observed_pending = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_pending
        items = await store_data.list_reminders(store, "user-1")
        observed_pending = (
            len(items) == 1 and not items[0].active and items[0].cron_id is None
        )
        payload = _cron_payload()
        payload["metadata"] = {
            "reminder_id": items[0].reminder_id,
            "user_id": "user-1",
        }
        return httpx.Response(200, json=payload, request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="https://deployment.test", transport=transport
    ) as http:
        client = CronClient(http=http, api_key="platform", internal_token="internal")

        # When
        result = await create_reminder_impl(
            "Weekly weight log",
            store_data.Weekday.MON,
            "09:00",
            _runtime(store),
            client=client,
        )

    # Then
    assert observed_pending
    reminder = (await store_data.list_reminders(store, "user-1"))[0]
    assert reminder.active is True
    assert reminder.cron_id == "cron-1"
    assert reminder.next_run_date == date(2026, 8, 24)
    envelope = _envelope(result)
    assert envelope["block_id"] == "reminders:list"
    assert "secret" not in result
    assert cast("dict[str, JsonValue]", envelope["data"])["items"]


@pytest.mark.asyncio
async def test_ambiguous_create_reconciles_and_removes_duplicates() -> None:
    # Given
    store = InMemoryStore()
    calls: list[str] = []
    active_crons = {"cron-a", "cron-b"}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/runs/crons"):
            raise httpx.ReadTimeout("unknown create outcome", request=request)
        if request.url.path == "/runs/crons/search":
            return httpx.Response(
                200,
                json={
                    "items": [
                        _cron_payload(cron_id) for cron_id in sorted(active_crons)
                    ]
                },
                request=request,
            )
        if request.method == "DELETE":
            active_crons.discard(request.url.path.rsplit("/", maxsplit=1)[-1])
        return httpx.Response(200, json={}, request=request)

    async with httpx.AsyncClient(
        base_url="https://deployment.test", transport=httpx.MockTransport(handler)
    ) as http:
        client = CronClient(http=http, api_key="platform", internal_token="internal")

        # When
        result = await create_reminder_impl(
            "Weekly weight log",
            store_data.Weekday.MON,
            "09:00",
            _runtime(store),
            client=client,
        )

    # Then
    reminder = (await store_data.list_reminders(store, "user-1"))[0]
    assert reminder.active is True
    assert reminder.cron_id == "cron-a"
    assert "POST /runs/crons/search" in calls
    assert "DELETE /runs/crons/cron-b" in calls
    assert "reminders:list" in result


@pytest.mark.asyncio
async def test_hard_create_failure_leaves_inactive_and_returns_fixed_error() -> None:
    # Given
    store = InMemoryStore()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"detail": "sentinel@example.com"}, request=request
        )

    async with httpx.AsyncClient(
        base_url="https://deployment.test", transport=httpx.MockTransport(handler)
    ) as http:
        client = CronClient(http=http, api_key="platform", internal_token="internal")

        # When
        result = await create_reminder_impl(
            "Call Alice at 555-0100",
            store_data.Weekday.MON,
            "09:00",
            _runtime(store),
            client=client,
        )

    # Then
    reminder = (await store_data.list_reminders(store, "user-1"))[0]
    assert reminder.active is False
    assert reminder.cron_id is None
    assert result == "Reminder not scheduled: the reminder service is unavailable."
    assert "sentinel" not in result


@pytest.mark.asyncio
async def test_active_cap_applies_across_threads() -> None:
    # Given
    store = InMemoryStore()
    for index in range(10):
        _ = await store_data.create_reminder(
            store,
            "user-1",
            store_data.ReminderRecord(
                reminder_id=f"existing-{index}",
                title=f"Reminder {index}",
                weekday=store_data.Weekday.MON,
                time="09:00",
                active=True,
                cron_id=f"cron-{index}",
                thread_id="thread-1" if index < 5 else "thread-2",
                wake_token=f"token-{index}",
                next_run_date=None,
                created_ts=datetime.now(UTC),
            ),
        )
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json=_cron_payload(), request=request)

    async with httpx.AsyncClient(
        base_url="https://deployment.test", transport=httpx.MockTransport(handler)
    ) as http:
        client = CronClient(http=http, api_key="platform", internal_token="internal")

        # When
        result = await create_reminder_impl(
            "Eleventh",
            store_data.Weekday.TUE,
            "10:00",
            _runtime(store, thread_id="thread-2"),
            client=client,
        )

    # Then
    assert result == "Reminder not scheduled: you can have up to 10 active reminders."
    assert requests == 0
    assert len(await store_data.list_reminders(store, "user-1")) == 10


@pytest.mark.asyncio
async def test_edit_toggle_and_cancel_call_cron_api_and_update_record() -> None:
    # Given
    store = InMemoryStore()
    record = store_data.ReminderRecord(
        reminder_id="reminder-1",
        title="Weekly weight log",
        weekday=store_data.Weekday.MON,
        time="09:00",
        active=True,
        cron_id="cron-1",
        thread_id="thread-1",
        wake_token="old-token",
        next_run_date=None,
        created_ts=datetime.now(UTC),
    )
    _ = await store_data.create_reminder(store, "user-1", record)
    requests: list[tuple[str, str, dict[str, JsonValue] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            cast("dict[str, JsonValue]", json.loads(request.content))
            if request.content
            else None
        )
        requests.append((request.method, request.url.path, body))
        return httpx.Response(200, json=_cron_payload(enabled=False), request=request)

    async with httpx.AsyncClient(
        base_url="https://deployment.test", transport=httpx.MockTransport(handler)
    ) as http:
        client = CronClient(http=http, api_key="platform", internal_token="internal")

        # When
        _ = await edit_reminder_impl(
            "Weekly weight log",
            store_data.Weekday.TUE,
            "10:30",
            None,
            _runtime(store),
            client=client,
        )
        edited = (await store_data.list_reminders(store, "user-1"))[0]
        _ = await edit_reminder_impl(
            edited.reminder_id, None, None, False, _runtime(store), client=client
        )
        _ = await cancel_reminder_impl(
            edited.reminder_id, _runtime(store), client=client
        )

    # Then
    assert requests[0][0:2] == ("PATCH", "/runs/crons/cron-1")
    first_body = requests[0][2]
    assert first_body is not None and first_body["schedule"] == "30 10 * * 2"
    assert cast("dict[str, JsonValue]", first_body["input"])["cron_wake"]
    assert requests[1][2] is not None and requests[1][2]["enabled"] is False
    assert requests[2][0:2] == ("DELETE", "/runs/crons/cron-1")
    cancelled = (await store_data.list_reminders(store, "user-1"))[0]
    assert cancelled.active is False
    assert cancelled.cron_id is None


@pytest.mark.asyncio
async def test_edit_failure_leaves_rotated_record_inactive() -> None:
    # Given
    store = InMemoryStore()
    record = store_data.ReminderRecord(
        reminder_id="reminder-1",
        title="Weekly weight log",
        weekday=store_data.Weekday.MON,
        time="09:00",
        active=True,
        cron_id="cron-1",
        thread_id="thread-1",
        wake_token="old-token",
        next_run_date=None,
        created_ts=datetime.now(UTC),
    )
    _ = await store_data.create_reminder(store, "user-1", record)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "unavailable"}, request=request)

    async with httpx.AsyncClient(
        base_url="https://deployment.test", transport=httpx.MockTransport(handler)
    ) as http:
        client = CronClient(http=http, api_key="platform", internal_token="internal")

        # When
        result = await edit_reminder_impl(
            "reminder-1",
            store_data.Weekday.TUE,
            "10:30",
            True,
            _runtime(store),
            client=client,
        )

    # Then
    paused = (await store_data.list_reminders(store, "user-1"))[0]
    assert result == "Reminder paused: the schedule update could not be completed."
    assert paused.active is False
    assert paused.wake_token != "old-token"


@pytest.mark.asyncio
async def test_graph_delivery_consumes_gate_handoff() -> None:
    # Given
    store = InMemoryStore()
    record = store_data.ReminderRecord(
        reminder_id="reminder-1",
        title="Weekly weight log",
        weekday=store_data.Weekday.MON,
        time="09:00",
        active=True,
        cron_id="cron-1",
        thread_id="thread-1",
        wake_token="secret-token",
        next_run_date=date(2026, 8, 24),
        created_ts=datetime.now(UTC),
    )
    _ = await store_data.create_reminder(store, "user-1", record)
    wake: CronWakePayload = {
        "reminder_id": "reminder-1",
        "user_id": "user-1",
        "thread_id": "thread-1",
        "wake_token": "secret-token",
    }
    graph = build_coach_graph().compile(store=store)

    # When
    result = await graph.ainvoke(
        {"cron_wake": wake},
        {
            "configurable": {
                "thread_id": "thread-1",
                "langgraph_auth_user": {"identity": "internal"},
            }
        },
    )

    # Then
    messages = cast("list[AIMessage]", result["messages"])
    assert len(messages) == 1
    assert "reminder:reminder-1" in cast("str", messages[0].content)
    assert "secret-token" not in cast("str", messages[0].content)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["forged", "mismatched", "inactive", "missing"])
async def test_delivery_fails_closed_for_invalid_identity_chain(case: str) -> None:
    # Given
    store = InMemoryStore()
    active = case != "inactive"
    record = store_data.ReminderRecord(
        reminder_id="reminder-1",
        title="Weekly weight log",
        weekday=store_data.Weekday.MON,
        time="09:00",
        active=active,
        cron_id="cron-1",
        thread_id="thread-1",
        wake_token="secret-token",
        next_run_date=date(2026, 8, 24),
        created_ts=datetime.now(UTC),
    )
    _ = await store_data.create_reminder(store, "user-1", record)
    wake: CronWakePayload = {
        "reminder_id": "missing" if case == "missing" else "reminder-1",
        "user_id": "user-1",
        "thread_id": "other" if case == "mismatched" else "thread-1",
        "wake_token": "forged" if case == "forged" else "secret-token",
    }

    # When
    state: CoachState = {"cron_wake": wake, "messages": []}
    update = await reminder_delivery(
        state,
        {"configurable": {"thread_id": "thread-1"}},
        store=store,
    )

    # Then
    assert update == {"cron_wake": None, "reminder_wake": None}


@pytest.mark.asyncio
async def test_valid_delivery_is_model_free_and_emits_full_reminder_card() -> None:
    # Given
    store = InMemoryStore()
    record = store_data.ReminderRecord(
        reminder_id="reminder-1",
        title="Weekly weight log",
        weekday=store_data.Weekday.MON,
        time="09:00",
        active=True,
        cron_id="cron-1",
        thread_id="thread-1",
        wake_token="secret-token",
        next_run_date=date(2026, 8, 24),
        created_ts=datetime.now(UTC),
    )
    _ = await store_data.create_reminder(store, "user-1", record)
    wake: CronWakePayload = {
        "reminder_id": "reminder-1",
        "user_id": "user-1",
        "thread_id": "thread-1",
        "wake_token": "secret-token",
    }

    # When
    state: CoachState = {"cron_wake": wake, "messages": []}
    update = await reminder_delivery(
        state,
        {"configurable": {"thread_id": "thread-1"}},
        store=store,
    )

    # Then
    messages = cast("list[AIMessage]", update.get("messages", []))
    assert len(messages) == 1
    assert messages[0].id
    content = cast("str", messages[0].content)
    envelope = _envelope(content.split("\n", 1)[1])
    card = cast("dict[str, JsonValue]", envelope["data"])
    assert card == {
        "title": "Weekly weight log",
        "schedule": "Every Monday at 9:00 AM",
        "nextRun": "Mon, Aug 24",
        "weekday": "Monday",
        "time": "09:00",
        "active": True,
    }
    assert "secret-token" not in content


@pytest.mark.asyncio
async def test_erasure_sweeps_known_orphan_crons_and_upload_reservations() -> None:
    # Given
    store = InMemoryStore()
    _ = await store_data.create_reminder(
        store,
        "user-1",
        store_data.ReminderRecord(
            reminder_id="known-reminder",
            title="Known reminder",
            weekday=store_data.Weekday.MON,
            time="09:00",
            active=False,
            cron_id="known-cron",
            thread_id="thread-1",
            wake_token="known-token",
            next_run_date=None,
            created_ts=datetime.now(UTC),
        ),
    )
    await store.aput(
        ("users", "user-1", "upload_registry"),
        "reservation-1",
        {"owner": "user-1"},
    )
    calls: list[str] = []
    cron_searches = 0
    thread_searches = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal cron_searches, thread_searches
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path == "/runs/crons/search":
            cron_searches += 1
            items = [_cron_payload("orphan-cron")] if cron_searches == 1 else []
            return httpx.Response(200, json={"items": items}, request=request)
        if request.url.path == "/threads/search":
            thread_searches += 1
            items = (
                [{"thread_id": "reservation-1", "metadata": {"owner": "user-1"}}]
                if thread_searches == 1
                else []
            )
            return httpx.Response(200, json={"items": items}, request=request)
        return httpx.Response(200, json={}, request=request)

    async with httpx.AsyncClient(
        base_url="https://deployment.test", transport=httpx.MockTransport(handler)
    ) as http:
        client = CronClient(http=http, api_key="platform", internal_token="internal")

        # When
        cron_zero = await cleanup_user_crons(store, "user-1", client)
        reservation_zero = await sweep_upload_reservations(store, "user-1", client)

    # Then
    assert cron_zero is True
    assert reservation_zero is True
    assert "DELETE /runs/crons/known-cron" in calls
    assert "DELETE /runs/crons/orphan-cron" in calls
    assert "DELETE /threads/reservation-1" in calls
    assert (
        await store.aget(("users", "user-1", "upload_registry"), "reservation-1")
        is None
    )


@pytest.mark.asyncio
async def test_tool_node_runtime_injection_reaches_create_reminder_impl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ToolNode injects `runtime` into the call args before Pydantic parsing.

    The three reminder args models must tolerate that injected key
    (`extra="allow"`, the ChangeScheduleInput wrapper pattern); `forbid`
    made every real agent-run reminder call die as a filtered-blank
    tool-invocation error.
    """
    # Given
    from contextlib import asynccontextmanager

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    from healthcare_rag.agent import reminders as reminders_module
    from healthcare_rag.agent.reminders import create_reminder

    store = InMemoryStore()

    async def handler(request: httpx.Request) -> httpx.Response:
        items = await store_data.list_reminders(store, "member-u1")
        payload = _cron_payload()
        payload["metadata"] = {
            "reminder_id": items[0].reminder_id,
            "user_id": "member-u1",
        }
        return httpx.Response(200, json=payload, request=request)

    transport = httpx.MockTransport(handler)

    @asynccontextmanager
    async def fake_deployment_client():
        async with httpx.AsyncClient(
            base_url="https://deployment.test", transport=transport
        ) as http:
            yield CronClient(http=http, api_key="platform", internal_token="internal")

    monkeypatch.setattr(
        reminders_module, "_deployment_client", fake_deployment_client
    )

    builder = StateGraph(MessagesState)
    _ = builder.add_node(
        "tools", ToolNode([create_reminder], handle_tool_errors=False)
    )
    _ = builder.add_edge(START, "tools")
    _ = builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=InMemorySaver(), store=store)

    # When
    result = await graph.ainvoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "create_reminder",
                            "args": {
                                "title": "Weekly weight log",
                                "weekday": "Mon",
                                "time": "09:00",
                            },
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        },
        {
            "configurable": {
                "thread_id": "thread-1",
                "coach_human_msg_id": "human-1",
                "langgraph_auth_user": {"identity": "member-u1"},
            }
        },
    )

    # Then
    content = cast("str", result["messages"][-1].content)
    envelope = _envelope(content)
    assert envelope["block_id"] == "reminders:list"
