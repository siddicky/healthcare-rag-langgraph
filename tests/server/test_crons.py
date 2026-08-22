from __future__ import annotations

# SIZE_OK — todo 6 requires all cron contract fixtures in one file.
# ANYIO_OK: cancellation assertion targets the required asyncio.Task API.
import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime

import anyio
import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph_sdk import Auth
from starlette.applications import Starlette

from healthcare_rag.agent.auth import Principal
from server.auth import AuthMiddleware, AuthPolicyEngine
from server.crons import CRON_LIMIT, routes, run_due_crons, start_scheduler
from server.run_engine import RunEngine, RunRequest
from server.storage import Storage

OWNER_HEADERS = {"authorization": "Bearer owner-1"}
OTHER_HEADERS = {"authorization": "Bearer owner-2"}
MEMBER_HEADERS = {"authorization": "Bearer member"}
NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


@dataclass(slots=True)
class FakeRunEngine(RunEngine):
    submissions: list[tuple[str, RunRequest]] = field(default_factory=list)

    async def submit(self, thread_id: str, request: RunRequest) -> dict[str, object]:
        self.submissions.append((thread_id, request))
        return {"run_id": f"run-{len(self.submissions)}"}


@dataclass(frozen=True, slots=True)
class Harness:
    client: httpx.AsyncClient
    storage: Storage
    engine: FakeRunEngine


def _auth() -> Auth:
    auth = Auth()

    @auth.authenticate
    async def authenticate(
        method: str,
        path: str,
        headers: dict[bytes, bytes],
        authorization: str | None,
    ) -> Principal:
        del method, path, headers
        if authorization == "Bearer member":
            return {"identity": "member-1", "role": "member"}
        owner = "owner-2" if authorization == "Bearer owner-2" else "owner-1"
        return {
            "identity": "cron-worker",
            "role": "internal",
            "sub_role": "cron_ops",
            "internal_owner": owner,
        }

    @auth.on
    async def deny_all(
        ctx: Auth.types.AuthContext, value: Auth.types.on.value
    ) -> Auth.types.HandlerResult:
        del ctx, value
        return False

    @auth.on.crons.create
    async def create_cron(
        ctx: Auth.types.AuthContext, value: Auth.types.on.crons.create.value
    ) -> Auth.types.HandlerResult:
        payload = value.get("payload")
        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        owner = ctx.user["internal_owner"] if "internal_owner" in ctx.user else None  # noqa: SIM401
        if (
            (ctx.user["role"] if "role" in ctx.user else None)  # noqa: SIM401
            != "internal"
            or (ctx.user["sub_role"] if "sub_role" in ctx.user else None)  # noqa: SIM401
            != "cron_ops"
            or not isinstance(owner, str)
            or not isinstance(metadata, dict)
            or metadata.get("user_id") != owner
        ):
            return False
        return {"user_id": owner}

    def scoped_owner(ctx: Auth.types.AuthContext) -> Auth.types.HandlerResult:
        owner = ctx.user["internal_owner"] if "internal_owner" in ctx.user else None  # noqa: SIM401
        if (
            (ctx.user["role"] if "role" in ctx.user else None)  # noqa: SIM401
            != "internal"
            or (ctx.user["sub_role"] if "sub_role" in ctx.user else None)  # noqa: SIM401
            != "cron_ops"
            or not isinstance(owner, str)
        ):
            return False
        return {"user_id": owner}

    async def read_scope(
        ctx: Auth.types.AuthContext,
        value: Auth.types.on.crons.read.value,
    ) -> Auth.types.HandlerResult:
        del value
        return scoped_owner(ctx)

    async def search_scope(
        ctx: Auth.types.AuthContext,
        value: Auth.types.on.crons.search.value,
    ) -> Auth.types.HandlerResult:
        del value
        return scoped_owner(ctx)

    async def update_scope(
        ctx: Auth.types.AuthContext,
        value: Auth.types.on.crons.update.value,
    ) -> Auth.types.HandlerResult:
        del value
        return scoped_owner(ctx)

    async def delete_scope(
        ctx: Auth.types.AuthContext,
        value: Auth.types.on.crons.delete.value,
    ) -> Auth.types.HandlerResult:
        del value
        return scoped_owner(ctx)

    _ = auth.on.crons.read(read_scope)
    _ = auth.on.crons.search(search_scope)
    _ = auth.on.crons.update(update_scope)
    _ = auth.on.crons.delete(delete_scope)
    return auth


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    storage = Storage(saver=InMemorySaver(), store=InMemoryStore(index=None))
    storage.threads["thread-a"] = {"thread_id": "thread-a", "user_id": "owner-1"}
    engine = FakeRunEngine()
    auth = _auth()
    app = Starlette(
        routes=routes,
        middleware=[AuthMiddleware.as_starlette(auth)],
    )
    app.state.storage = storage
    app.state.run_engine = engine
    app.state.auth_engine = AuthPolicyEngine(auth)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield Harness(client=client, storage=storage, engine=engine)


def _body(
    *,
    owner: str = "owner-1",
    schedule: str = "* * * * *",
    timezone: str = "UTC",
) -> dict[str, object]:
    return {
        "schedule": schedule,
        "timezone": timezone,
        "assistant_id": "coach",
        "input": {"cron_wake": {"token": "exact-input"}},
        "metadata": {"user_id": owner, "reminder_id": f"reminder-{owner}"},
        "enabled": True,
        "multitask_strategy": "enqueue",
    }


@pytest.mark.anyio
async def test_create_stateless_and_thread_crons_match_characterized_schema(
    harness: Harness,
) -> None:
    stateless = await harness.client.post(
        "/runs/crons", headers=OWNER_HEADERS, json=_body()
    )
    threaded = await harness.client.post(
        "/threads/thread-a/runs/crons", headers=OWNER_HEADERS, json=_body()
    )

    assert stateless.status_code == threaded.status_code == 200
    assert stateless.json()["thread_id"] is None
    assert threaded.json()["thread_id"] == "thread-a"
    assert set(threaded.json()) == {
        "cron_id",
        "thread_id",
        "end_time",
        "schedule",
        "created_at",
        "updated_at",
        "payload",
        "next_run_date",
        "metadata",
        "enabled",
    }
    assert threaded.json()["next_run_date"] is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("schedule", "timezone"),
    [("not a cron", "UTC"), ("* * * * *", "Mars/Olympus")],
)
async def test_create_rejects_bad_schedule_and_timezone(
    harness: Harness, schedule: str, timezone: str
) -> None:
    response = await harness.client.post(
        "/runs/crons",
        headers=OWNER_HEADERS,
        json=_body(schedule=schedule, timezone=timezone),
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_search_patch_get_and_delete_are_owner_scoped(harness: Harness) -> None:
    mine = await harness.client.post("/runs/crons", headers=OWNER_HEADERS, json=_body())
    _ = await harness.client.post(
        "/runs/crons", headers=OTHER_HEADERS, json=_body(owner="owner-2")
    )
    cron_id = mine.json()["cron_id"]

    page = await harness.client.post(
        "/runs/crons/search",
        headers=OWNER_HEADERS,
        json={"metadata": {"user_id": "owner-1"}, "limit": 100, "offset": 0},
    )
    patched = await harness.client.patch(
        f"/runs/crons/{cron_id}", headers=OWNER_HEADERS, json={"enabled": False}
    )
    fetched = await harness.client.get(f"/runs/crons/{cron_id}", headers=OWNER_HEADERS)
    deleted = await harness.client.delete(
        f"/runs/crons/{cron_id}", headers=OWNER_HEADERS
    )

    assert [item["cron_id"] for item in page.json()] == [cron_id]
    assert patched.json()["enabled"] is False
    assert patched.json()["next_run_date"] is None
    assert fetched.json()["enabled"] is False
    assert deleted.status_code == 204
    assert (
        await harness.client.get(f"/runs/crons/{cron_id}", headers=OWNER_HEADERS)
    ).status_code == 404


@pytest.mark.anyio
async def test_next_fire_uses_utc_and_iana_timezone() -> None:
    from server.crons import next_run_date

    assert next_run_date("* * * * *", "UTC", NOW) == datetime(
        2026, 1, 1, 12, 1, tzinfo=UTC
    )
    assert next_run_date("0 9 * * *", "America/New_York", NOW) == datetime(
        2026, 1, 1, 14, 0, tzinfo=UTC
    )


@pytest.mark.anyio
async def test_due_thread_cron_enqueues_exact_input_on_target_thread(
    harness: Harness,
) -> None:
    created = await harness.client.post(
        "/threads/thread-a/runs/crons", headers=OWNER_HEADERS, json=_body()
    )
    cron_id = created.json()["cron_id"]
    harness.storage.crons[cron_id]["next_run_date"] = NOW.isoformat()

    await run_due_crons(harness.engine, harness.storage, NOW)

    assert len(harness.engine.submissions) == 1
    thread_id, request = harness.engine.submissions[0]
    assert thread_id == "thread-a"
    assert request.assistant_id == "coach"
    assert request.input == {"cron_wake": {"token": "exact-input"}}
    assert request.multitask_strategy == "enqueue"


@pytest.mark.anyio
async def test_scheduler_uses_injected_clock(harness: Harness) -> None:
    called = anyio.Event()

    def clock() -> datetime:
        called.set()
        return NOW

    task = start_scheduler(harness.engine, harness.storage, clock)
    await called.wait()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_asgi_roundtrip_create_search_fire_observes_run(harness: Harness) -> None:
    created = await harness.client.post(
        "/threads/thread-a/runs/crons", headers=OWNER_HEADERS, json=_body()
    )
    cron_id = created.json()["cron_id"]
    searched = await harness.client.post(
        "/runs/crons/search",
        headers=OWNER_HEADERS,
        json={"metadata": {"reminder_id": "reminder-owner-1"}},
    )
    harness.storage.crons[cron_id]["next_run_date"] = NOW.isoformat()

    await run_due_crons(harness.engine, harness.storage, NOW)

    assert [item["cron_id"] for item in searched.json()] == [cron_id]
    assert harness.engine.submissions[0][0] == "thread-a"
    assert harness.engine.submissions[0][1].input == {
        "cron_wake": {"token": "exact-input"}
    }


@pytest.mark.anyio
async def test_registry_overflow_returns_retryable_503(harness: Harness) -> None:
    for _ in range(CRON_LIMIT):
        response = await harness.client.post(
            "/runs/crons", headers=OWNER_HEADERS, json=_body()
        )
        assert response.status_code == 200

    overflow = await harness.client.post(
        "/runs/crons", headers=OWNER_HEADERS, json=_body()
    )

    assert overflow.status_code == 503
    assert overflow.headers["retry-after"] == "1"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("POST", "/runs/crons", _body()),
        ("POST", "/runs/crons/search", {"limit": 10, "offset": 0}),
        ("GET", "/runs/crons/known", None),
        ("PATCH", "/runs/crons/known", {"enabled": False}),
        ("DELETE", "/runs/crons/known", None),
        ("POST", "/threads/thread-a/runs/crons", _body()),
    ],
)
async def test_member_is_denied_on_every_cron_route(
    harness: Harness,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    harness.storage.crons["known"] = {"cron_id": "known", "user_id": "owner-1"}

    response = await harness.client.request(
        method, path, headers=MEMBER_HEADERS, json=payload
    )

    assert response.status_code == 403
