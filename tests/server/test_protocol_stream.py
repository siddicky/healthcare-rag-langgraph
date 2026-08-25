from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, TypedDict

import anyio
import httpx
import pytest
from anyio.lowlevel import checkpoint
from httpx_sse import aconnect_sse
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.types import interrupt
from langgraph_sdk import Auth
from pydantic import JsonValue, TypeAdapter
from starlette.applications import Starlette

from server.app import create_app
from server.config import ServerConfig


class ProtocolState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    mode: str
    value: int
    resumed: bool


class ProtocolParams(TypedDict):
    namespace: list[str]
    timestamp: int
    data: JsonValue


class ProtocolEventPayload(TypedDict):
    type: str
    event_id: str
    seq: int
    method: str
    params: ProtocolParams


EVENT_ADAPTER = TypeAdapter(ProtocolEventPayload)


@dataclass(frozen=True, slots=True)
class Harness:
    app: Starlette
    client: httpx.AsyncClient


@pytest.fixture
async def harness(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Harness]:
    async def step(state: ProtocolState) -> ProtocolState:
        match state.get("mode"):
            case "message":
                return {"messages": [AIMessage(content="hello", id="message-1")]}
            case "tools":
                return {
                    "messages": [
                        AIMessage(
                            content="",
                            id="tool-owner",
                            tool_calls=[
                                {"id": "call-1", "name": "lookup", "args": {"q": "x"}}
                            ],
                        ),
                        ToolMessage(content="found", tool_call_id="call-1"),
                    ]
                }
            case "interrupt":
                resumed = interrupt({"kind": "approval"})
                return {"resumed": bool(resumed)}
            case None | "increment":
                return {"value": state.get("value", 0) + 1}
            case unreachable:
                raise AssertionError(unreachable)

    auth = Auth()

    @auth.authenticate
    async def authenticate(
        method: str,
        path: str,
        headers: dict[bytes, bytes],
        authorization: str | None,
    ) -> Auth.types.MinimalUserDict:
        del method, path, headers, authorization
        return {"identity": "member-1", "is_authenticated": True}

    def load_stub_auth(path: str | None) -> Auth:
        del path
        return auth

    monkeypatch.setattr("server.app.load_auth_instance", load_stub_auth)
    app = create_app(
        ServerConfig(
            graphs={},
            auth_path="stub:auth",
            http_app=None,
            http_flags={},
            store_index={},
            api_version="test",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        app.state.graphs["toy"] = (
            StateGraph(ProtocolState)
            .add_node("step", step)
            .add_edge(START, "step")
            .add_edge("step", END)
            .compile(
                checkpointer=app.state.storage.saver,
                store=app.state.storage.store,
                name="toy",
            )
        )
        await app.state.storage.threads.save("thread-1", {"thread_id": "thread-1"})
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            yield Harness(app=app, client=client)


async def command(
    harness: Harness, command_id: int, method: str, params: dict[str, object]
) -> httpx.Response:
    return await harness.client.post(
        "/threads/thread-1/commands",
        json={"id": command_id, "method": method, "params": params},
    )


async def collect_events(
    harness: Harness, channels: list[str], *, since: int | None = None
) -> list[tuple[str, str, ProtocolEventPayload]]:
    body: dict[str, object] = {"channels": channels}
    if since is not None:
        body["since"] = since
    async with aconnect_sse(
        harness.client,
        "POST",
        "/threads/thread-1/stream/events",
        json=body,
    ) as source:
        return [
            (event.event, event.id, EVENT_ADAPTER.validate_python(event.json()))
            async for event in source.aiter_sse()
        ]


def event_data(payload: ProtocolEventPayload) -> dict[str, JsonValue]:
    data = payload["params"]["data"]
    assert isinstance(data, dict)
    return data


async def wait_for_status(harness: Harness, run_id: str, status: str) -> None:
    with anyio.fail_after(2):
        while True:
            record = await harness.app.state.storage.runs.get(run_id)
            if record is not None and record.get("status") == status:
                return
            await checkpoint()


@pytest.mark.anyio
async def test_commands_reject_missing_thread_and_malformed_envelopes(
    harness: Harness,
) -> None:
    missing = await harness.client.post(
        "/threads/missing/commands",
        json={"id": 1, "method": "run.start", "params": {}},
    )
    malformed = await harness.client.post(
        "/threads/thread-1/commands", json={"params": {}}
    )

    assert missing.status_code == 404
    assert missing.json()["error"] == "no_such_run"
    assert malformed.status_code == 400
    assert malformed.json()["error"] == "invalid_argument"


@pytest.mark.anyio
async def test_run_start_streams_framed_lifecycle_updates_and_stable_replay(
    harness: Harness,
) -> None:
    started = await command(
        harness,
        1,
        "run.start",
        {"assistant_id": "toy", "input": {"value": 2}},
    )
    run_id = started.json()["result"]["run_id"]
    await wait_for_status(harness, run_id, "success")

    first = await collect_events(harness, ["lifecycle", "updates"])
    last_seq = max(int(event[2]["seq"]) for event in first)
    replay = await collect_events(
        harness, ["lifecycle", "updates"], since=int(first[0][2]["seq"])
    )
    exhausted = await collect_events(harness, ["lifecycle", "updates"], since=last_seq)

    assert started.status_code == 200
    assert [event[0] for event in first] == ["lifecycle", "updates", "lifecycle"]
    assert [event_data(event[2])["event"] for event in (first[0], first[-1])] == [
        "started",
        "completed",
    ]
    assert all(event_id == str(payload["seq"]) for _, event_id, payload in first)
    assert all(payload["event_id"] for _, _, payload in first)
    assert [payload["event_id"] for _, _, payload in replay] == [
        payload["event_id"] for _, _, payload in first[1:]
    ]
    assert exhausted == []


@pytest.mark.anyio
async def test_messages_are_normalized_into_one_ordered_text_block(
    harness: Harness,
) -> None:
    response = await command(
        harness,
        2,
        "run.start",
        {"assistant_id": "toy", "input": {"mode": "message"}},
    )
    await wait_for_status(harness, response.json()["result"]["run_id"], "success")

    events = await collect_events(harness, ["messages"])
    names = [event_data(event[2])["event"] for event in events]

    assert names == [
        "message-start",
        "content-block-start",
        "content-block-delta",
        "content-block-finish",
        "message-finish",
    ]
    delta = event_data(events[2][2])["delta"]
    assert isinstance(delta, dict) and delta["text"] == "hello"


@pytest.mark.anyio
async def test_tool_and_interrupt_updates_are_normalized(harness: Harness) -> None:
    tools = await command(
        harness,
        3,
        "run.start",
        {"assistant_id": "toy", "input": {"mode": "tools"}},
    )
    await wait_for_status(harness, tools.json()["result"]["run_id"], "success")
    tool_events = await collect_events(harness, ["tools"])

    interrupted = await command(
        harness,
        4,
        "run.start",
        {"assistant_id": "toy", "input": {"mode": "interrupt"}},
    )
    await wait_for_status(
        harness, interrupted.json()["result"]["run_id"], "interrupted"
    )
    input_events = await collect_events(harness, ["input"])

    assert [event_data(event[2])["event"] for event in tool_events] == [
        "tool-started",
        "tool-finished",
    ]
    assert input_events, harness.app.state.run_engine.runtime[
        interrupted.json()["result"]["run_id"]
    ].events
    assert input_events[0][0] == "input.requested"
    assert event_data(input_events[0][2])["interrupt_id"]


@pytest.mark.anyio
async def test_input_respond_resumes_pending_interrupt(harness: Harness) -> None:
    interrupted = await command(
        harness,
        5,
        "run.start",
        {"assistant_id": "toy", "input": {"mode": "interrupt"}},
    )
    await wait_for_status(
        harness, interrupted.json()["result"]["run_id"], "interrupted"
    )
    snapshot = await harness.app.state.graphs["toy"].aget_state(
        {"configurable": {"thread_id": "thread-1"}}
    )
    interrupt_id = snapshot.tasks[0].interrupts[0].id

    resumed = await command(
        harness,
        6,
        "input.respond",
        {"interrupt_id": interrupt_id, "response": {"accept": True}},
    )
    resumed_run = resumed.json()["result"]["run_id"]
    await wait_for_status(harness, resumed_run, "success")

    assert resumed.status_code == 200
    assert resumed.json()["type"] == "success"


@pytest.mark.anyio
async def test_state_commands_get_list_and_fork_checkpoint_history(
    harness: Harness,
) -> None:
    initial = await command(
        harness,
        7,
        "run.start",
        {"assistant_id": "toy", "input": {"value": 2}},
    )
    await wait_for_status(harness, initial.json()["result"]["run_id"], "success")

    state = await command(harness, 8, "state.get", {"keys": ["value"]})
    history = await command(harness, 9, "state.listCheckpoints", {"limit": 10})
    tree = await command(harness, 12, "agent.getTree", {})
    checkpoint_id = history.json()["result"]["checkpoints"][0]["checkpoint_id"]
    forked = await command(
        harness,
        10,
        "state.fork",
        {"checkpoint_id": checkpoint_id, "input": {"value": 9}},
    )
    await wait_for_status(harness, forked.json()["result"]["run_id"], "success")

    assert state.json()["result"]["values"] == {"value": 3}
    assert state.json()["result"]["checkpoint"]["id"]
    assert "tasks" not in history.json()["result"]["checkpoints"][0]
    assert tree.json()["result"]["tree"] == {
        "namespace": [],
        "status": "completed",
        "graph_name": "toy",
        "children": [],
    }
    assert forked.status_code == 200


@pytest.mark.anyio
async def test_channel_filter_excludes_unsubscribed_methods(harness: Harness) -> None:
    started = await command(
        harness,
        11,
        "run.start",
        {"assistant_id": "toy", "input": {"value": 4}},
    )
    await wait_for_status(harness, started.json()["result"]["run_id"], "success")

    events = await collect_events(harness, ["values"])

    assert events
    assert {event[0] for event in events} == {"values"}


@pytest.mark.anyio
async def test_stream_filter_rejects_unknown_channels_and_accepts_bool_guards(
    harness: Harness,
) -> None:
    unknown = await harness.client.post(
        "/threads/thread-1/stream/events", json={"channels": ["bogus"]}
    )
    guarded = await harness.client.post(
        "/threads/thread-1/stream/events",
        json={"channels": ["values"], "depth": False, "since": True},
    )

    assert unknown.status_code == 400
    assert unknown.json()["error"] == "invalid_argument"
    assert guarded.status_code == 200
