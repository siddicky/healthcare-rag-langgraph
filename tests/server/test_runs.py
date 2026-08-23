from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TypedDict

import anyio
import httpx
import pytest
from anyio.lowlevel import checkpoint
from httpx_sse import aconnect_sse
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from langgraph_sdk import Auth

from server.app import create_app
from server.config import ServerConfig
from server.run_engine import JSONValue


class ToyState(TypedDict, total=False):
    value: int
    mode: str
    resumed: bool


@dataclass(slots=True)
class ToyControl:
    """Mutable synchronization gates for deterministic graph execution."""

    started: anyio.Event
    release: anyio.Event


class ToyFailure(RuntimeError):
    pass


_AUTH_USER_SEEN: list[object] = []


@dataclass(frozen=True, slots=True)
class Harness:
    app: object
    client: httpx.AsyncClient
    control: ToyControl


@pytest.fixture
async def harness(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Harness]:
    control = ToyControl(started=anyio.Event(), release=anyio.Event())
    # Reset per test, not per node call: clearing inside the node would make
    # `len(_AUTH_USER_SEEN) == 1` trivially true and stop it proving that the
    # graph ran exactly once.
    _AUTH_USER_SEEN.clear()

    # `config` MUST be annotated exactly `RunnableConfig` (not `RunnableConfig |
    # None`): this module uses `from __future__ import annotations`, so LangGraph
    # sees the raw annotation *string* and only matches "RunnableConfig" /
    # "Optional[RunnableConfig]" (langgraph/_internal/_runnable.py KWARGS_CONFIG_KEYS).
    # Any other spelling silently stops config injection and this node would
    # observe nothing at all — which is exactly how the principal assertion below
    # rotted into a no-op.
    async def step(state: ToyState, config: RunnableConfig) -> ToyState:
        configurable = config.get("configurable", {})
        _AUTH_USER_SEEN.append(
            configurable.get("langgraph_auth_user")
            if isinstance(configurable, dict)
            else None
        )
        match state.get("mode"):
            case "block":
                control.started.set()
                await control.release.wait()
                return {"value": state.get("value", 0) + 1}
            case "fail":
                raise ToyFailure
            case "interrupt":
                resumed = interrupt({"kind": "approval"})
                return {"resumed": bool(resumed)}
            case None | "increment":
                return {"value": state.get("value", 0) + 1}
            case unreachable:
                raise AssertionError(unreachable)

    builder = StateGraph(ToyState).add_node("step", step).add_edge(START, "step").add_edge("step", END)
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

    @auth.on.threads.create_run
    async def authorize_run(
        ctx: Auth.types.AuthContext,
        value: Auth.types.on.threads.create_run.value,
    ) -> bool | None:
        del ctx
        kwargs = value.get("kwargs")
        run_input = kwargs.get("input") if kwargs is not None else None
        return False if isinstance(run_input, dict) and "cron_wake" in run_input else None

    def load_stub_auth(path: str | None) -> Auth:
        del path
        return auth

    monkeypatch.setattr("server.app.load_auth_instance", load_stub_auth)
    config = ServerConfig(
        graphs={}, auth_path="stub:auth", http_app=None, http_flags={}, store_index={}, api_version="test"
    )
    app = create_app(config)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        app.state.graphs["toy"] = builder.compile(
            checkpointer=app.state.storage.saver,
            store=app.state.storage.store,
            name="toy",
        )
        app.state.storage.threads["thread-1"] = {"thread_id": "thread-1"}
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Harness(app=app, client=client, control=control)


def body(input_: dict[str, JSONValue], **extra: object) -> dict[str, object]:
    return {
        "assistant_id": "toy",
        "input": input_,
        "config": {},
        "durability": "exit",
        "if_not_exists": "reject",
        **extra,
    }


async def create(harness: Harness, payload: dict[str, object]) -> httpx.Response:
    return await harness.client.post("/threads/thread-1/runs", json=payload)


async def wait_status(harness: Harness, run_id: str, status: str) -> dict[str, object]:
    with anyio.fail_after(2):
        while True:
            response = await harness.client.get(f"/threads/thread-1/runs/{run_id}")
            record = response.json()
            if record["status"] == status:
                return record
            await checkpoint()


@pytest.mark.anyio
async def test_lifecycle_create_wait_list_join_when_thread_exists(harness: Harness) -> None:
    waited = await harness.client.post("/threads/thread-1/runs/wait", json=body({"value": 2}))
    created = await create(harness, body({"value": 8}))
    run_id = created.json()["run_id"]
    joined = await harness.client.get(f"/threads/thread-1/runs/{run_id}/join")
    listed = await harness.client.get("/threads/thread-1/runs")

    assert waited.status_code == 200 and waited.json() == {"value": 3}
    assert joined.status_code == 200 and joined.json() == {"value": 9}
    assert [run["run_id"] for run in listed.json()] == [run_id, waited.headers["x-run-id"]]
    assert (await harness.client.get(f"/threads/thread-1/runs/{run_id}")).json()["status"] == "success"


@pytest.mark.anyio
async def test_enqueue_is_fifo_and_reject_returns_conflict(harness: Harness) -> None:
    active = await create(harness, body({"mode": "block"}))
    await harness.control.started.wait()
    rejected = await create(harness, body({"value": 1}, multitask_strategy="reject"))
    queued = await create(harness, body({"value": 2}, multitask_strategy="enqueue"))

    assert rejected.status_code == 409
    assert active.json()["status"] == "pending"
    assert queued.status_code == 200 and queued.json()["status"] == "pending"
    assert (await harness.client.get(f"/threads/thread-1/runs/{active.json()['run_id']}")).json()["status"] == "running"
    harness.control.release.set()
    assert await harness.client.get(f"/threads/thread-1/runs/{queued.json()['run_id']}/join")


@pytest.mark.anyio
async def test_interrupt_strategy_replaces_active_run(harness: Harness) -> None:
    active = await create(harness, body({"mode": "block"}))
    await harness.control.started.wait()
    replacement = await create(harness, body({"value": 4}, multitask_strategy="interrupt"))

    assert replacement.status_code == 200
    _ = await wait_status(harness, active.json()["run_id"], "interrupted")
    joined = await harness.client.get(f"/threads/thread-1/runs/{replacement.json()['run_id']}/join")
    assert joined.json() == {"value": 5}


@pytest.mark.anyio
async def test_rollback_keeps_queued_and_running_records(harness: Harness) -> None:
    running = await create(harness, body({"mode": "block"}))
    await harness.control.started.wait()
    queued = await create(harness, body({"value": 2}, multitask_strategy="enqueue"))
    queued_cancel = await harness.client.post(
        f"/threads/thread-1/runs/{queued.json()['run_id']}/cancel", json={"action": "rollback", "wait": True}
    )
    running_cancel = await harness.client.post(
        f"/threads/thread-1/runs/{running.json()['run_id']}/cancel", json={"action": "rollback", "wait": True}
    )

    assert queued_cancel.status_code == running_cancel.status_code == 200
    assert (await harness.client.get(f"/threads/thread-1/runs/{queued.json()['run_id']}")).json()["status"] == "interrupted"
    assert (await harness.client.get(f"/threads/thread-1/runs/{running.json()['run_id']}")).json()["status"] == "interrupted"


@pytest.mark.anyio
async def test_resume_replay_is_idempotent_and_invalid_command_is_4xx(harness: Harness) -> None:
    interrupted = await create(harness, body({"mode": "interrupt"}))
    _ = await wait_status(harness, interrupted.json()["run_id"], "interrupted")
    resume = body({}, command={"resume": {"accept": True}})
    _ = resume.pop("input")
    first = await create(harness, resume)
    first_join = await harness.client.get(f"/threads/thread-1/runs/{first.json()['run_id']}/join")
    replay = await create(harness, resume)
    malformed = await create(harness, body({}, command={"update": {}}))

    assert first_join.json() == {"mode": "interrupt", "resumed": True}
    assert replay.status_code == 200 and replay.json()["run_id"] == first.json()["run_id"]
    assert malformed.status_code == 422


@pytest.mark.anyio
async def test_stream_uses_event_and_data_sse_frames(harness: Harness) -> None:
    async with aconnect_sse(
        harness.client, "POST", "/threads/thread-1/runs/stream", json=body({"value": 6}, stream_mode=["updates", "custom"])
    ) as source:
        events = [(event.event, event.json()) async for event in source.aiter_sse()]

    assert events == [("updates", {"step": {"value": 7}})]


@pytest.mark.anyio
async def test_graph_error_sets_error_status(harness: Harness) -> None:
    created = await create(harness, body({"mode": "fail"}))
    record = await wait_status(harness, created.json()["run_id"], "error")
    assert record["status"] == "error"


@pytest.mark.anyio
async def test_pending_queue_is_bounded_server_wide(harness: Harness) -> None:
    active = await create(harness, body({"mode": "block"}))
    await harness.control.started.wait()
    queued_ids: list[str] = []
    for value in range(100):
        response = await create(
            harness,
            body({"value": value, "mode": "increment"}, multitask_strategy="enqueue"),
        )
        assert response.status_code == 200
        queued_ids.append(response.json()["run_id"])
    overflow = await create(harness, body({"value": 101}, multitask_strategy="enqueue"))
    assert overflow.status_code == 503 and overflow.headers["retry-after"] == "1"
    for run_id in queued_ids:
        await harness.client.post(
            f"/threads/thread-1/runs/{run_id}/cancel",
            json={"action": "interrupt", "wait": True},
        )
    await harness.client.post(
        f"/threads/thread-1/runs/{active.json()['run_id']}/cancel", json={"action": "interrupt", "wait": True}
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "payload", "status"),
    [
        ("/threads/missing/runs", body({"value": 1}), 404),
        ("/threads/thread-1/runs", body({}, multitask_strategy="parallel"), 422),
        ("/threads/thread-1/runs/stream", body({}, stream_mode=["messages"]), 422),
        ("/threads/thread-1/runs", body({"cron_wake": {}}), 403),
    ],
)
async def test_malformed_and_forbidden_requests_are_rejected(
    harness: Harness, path: str, payload: dict[str, object], status: int
) -> None:
    response = await harness.client.post(path, json=payload)
    assert response.status_code == status


@pytest.mark.anyio
async def test_bare_runs_manifest_remains_unimplemented(harness: Harness) -> None:
    assert (await harness.client.post("/runs/stream", json={})).status_code == 501


@pytest.mark.anyio
async def test_queue_bound_applies_to_idle_threads(harness: Harness) -> None:
    # F2 regression: the server-wide QUEUE_LIMIT must hold even when the
    # submitting thread has NO active run (the old check was gated on
    # active_id and let idle-thread submissions grow the queue unbounded).
    active = await create(harness, body({"mode": "block"}))
    await harness.control.started.wait()
    queued_ids: list[str] = []
    try:
        for value in range(100):
            response = await create(
                harness,
                body({"value": value, "mode": "increment"}, multitask_strategy="enqueue"),
            )
            assert response.status_code == 200
            queued_ids.append(response.json()["run_id"])

        storage = harness.app.state.storage  # type: ignore[attr-defined]
        storage.threads["thread-2"] = {"thread_id": "thread-2"}
        idle_submit = await harness.client.post(
            "/threads/thread-2/runs", json=body({"value": 1})
        )
        assert idle_submit.status_code == 503 and idle_submit.headers["retry-after"] == "1"
    finally:
        for run_id in queued_ids:
            await harness.client.post(
                f"/threads/thread-1/runs/{run_id}/cancel",
                json={"action": "interrupt", "wait": True},
            )
        await harness.client.post(
            f"/threads/thread-1/runs/{active.json()['run_id']}/cancel",
            json={"action": "interrupt", "wait": True},
        )
        harness.control.release.set()


@pytest.mark.anyio
async def test_run_config_receives_server_principal_not_client(harness: Harness) -> None:
    # Regression (deployed-smoke check 3): the real Agent Server injects the
    # authenticated principal as configurable.langgraph_auth_user; a
    # client-supplied value must never survive.
    forged = await create(
        harness,
        body(
            {"value": 1},
            config={"configurable": {"langgraph_auth_user": {"identity": "forged"}}},
        ),
    )
    assert forged.status_code == 200
    record = await wait_status(harness, forged.json()["run_id"], "success")
    assert record["status"] == "success"
    # The node must have actually run and actually received a config. An empty
    # list here means config injection is broken, NOT that the invariant holds.
    assert len(_AUTH_USER_SEEN) == 1, "graph node never observed a RunnableConfig"
    injected = _AUTH_USER_SEEN[0]
    assert isinstance(injected, dict)
    # Server principal wins...
    assert injected.get("identity") == "member-1"
    # ...and the client's forged principal is gone, not merged alongside it.
    assert injected == {"identity": "member-1", "is_authenticated": True}
    assert "forged" not in str(injected)
    # The forged value must also not survive on the persisted run record, which
    # is what a later resume/replay would read back.
    stored_config = record["config"]
    assert isinstance(stored_config, dict)
    stored_configurable = stored_config.get("configurable", {})
    assert isinstance(stored_configurable, dict)
    assert stored_configurable.get("langgraph_auth_user") != {"identity": "forged"}
