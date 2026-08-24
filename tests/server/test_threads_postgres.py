from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import anyio
import httpx
import pytest
from langgraph_sdk import Auth
from starlette.applications import Starlette

from server.auth import AuthMiddleware, AuthPolicyEngine
from server.config import ServerConfig
from server.storage import Storage, create_storage
from server.threads import _purge_expired, routes


def _auth() -> Auth:
    auth = Auth()

    @auth.authenticate
    async def authenticate(
        method: str,
        path: str,
        headers: dict[bytes, bytes],
        authorization: str | None,
    ) -> dict[str, Any]:
        del method, path, headers, authorization
        return {"identity": "postgres-member", "is_authenticated": True}

    @auth.on.threads.create
    async def create_thread(ctx: Any, value: Any) -> None:
        del ctx, value

    @auth.on.threads.delete
    async def delete_thread(ctx: Any, value: Any) -> None:
        del ctx, value

    return auth


def _postgres_config(dsn: str) -> ServerConfig:
    return ServerConfig(
        graphs={},
        auth_path=None,
        http_app=None,
        http_flags={},
        store_index={},
        api_version="threads-postgres-test",
        storage="postgres",
        database_uri=dsn,
    )


@pytest.fixture
async def postgres_thread_client(
    postgres_url: str,
) -> AsyncIterator[tuple[httpx.AsyncClient, Storage]]:
    storage = await create_storage(_postgres_config(postgres_url))
    auth = _auth()
    app = Starlette(
        routes=routes,
        middleware=[AuthMiddleware.as_starlette(auth, local_dev=False)],
    )
    app.state.storage = storage
    app.state.auth_engine = AuthPolicyEngine(auth)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            yield client, storage
    finally:
        await storage.aclose()


@pytest.mark.anyio
async def test_concurrent_default_create_with_supplied_id_is_atomic(
    postgres_thread_client: tuple[httpx.AsyncClient, Storage],
) -> None:
    client, storage = postgres_thread_client
    thread_id = str(uuid4())
    statuses: list[int] = []

    async def create() -> None:
        response = await client.post("/threads", json={"thread_id": thread_id})
        statuses.append(response.status_code)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(create)
        tasks.start_soon(create)

    assert sorted(statuses) == [200, 409]
    await storage.threads.delete(thread_id)


@pytest.mark.anyio
async def test_delete_cascade_removes_postgres_run_and_cron_rows(
    postgres_thread_client: tuple[httpx.AsyncClient, Storage],
) -> None:
    client, storage = postgres_thread_client
    thread_id = str(uuid4())
    run_id = str(uuid4())
    cron_id = str(uuid4())
    now = datetime.now(UTC).isoformat()
    created = await client.post("/threads", json={"thread_id": thread_id})
    assert created.status_code == 200
    await storage.runs.save(
        run_id,
        {
            "run_id": run_id,
            "thread_id": thread_id,
            "status": "pending",
            "created_at": now,
        },
    )
    await storage.crons.save(
        cron_id,
        {
            "cron_id": cron_id,
            "thread_id": thread_id,
            "enabled": True,
            "next_run_date": None,
            "updated_at": now,
        },
    )

    response = await client.delete(f"/threads/{thread_id}")

    assert response.status_code == 204
    assert await storage.runs.get(run_id) is None
    assert await storage.crons.get(cron_id) is None


@pytest.mark.anyio
async def test_expiry_sweep_deletes_checkpoint_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = await create_storage(
        ServerConfig(
            graphs={},
            auth_path=None,
            http_app=None,
            http_flags={},
            store_index={},
            api_version="threads-expiry-test",
        )
    )
    thread_id = str(uuid4())
    deleted: list[str] = []

    async def record_delete(expired_thread_id: str) -> None:
        deleted.append(expired_thread_id)

    monkeypatch.setattr(storage.saver, "adelete_thread", record_delete)
    await storage.threads.save(
        thread_id,
        {
            "thread_id": thread_id,
            "updated_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        },
    )

    await _purge_expired(storage)

    assert deleted == [thread_id]
