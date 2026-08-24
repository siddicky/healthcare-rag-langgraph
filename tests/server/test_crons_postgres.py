from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import anyio
import pytest

from server.config import ServerConfig
from server.crons import (
    reconcile_crons,
    run_due_crons,
)
from server.run_engine import JSONValue, RunRequest
from server.storage import create_storage

NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


@dataclass(slots=True)
class RecordingSubmitter:
    submissions: list[tuple[str, RunRequest]] = field(default_factory=list)

    async def submit(
        self,
        thread_id: str,
        request: RunRequest,
        *,
        auth_user: dict[str, JSONValue] | None = None,
    ) -> dict[str, object]:
        del auth_user
        self.submissions.append((thread_id, request))
        return {"run_id": f"run-{len(self.submissions)}"}


def _postgres_config(dsn: str) -> ServerConfig:
    return ServerConfig(
        graphs={},
        auth_path=None,
        http_app=None,
        http_flags={},
        store_index={},
        api_version="durable-crons-test",
        storage="postgres",
        database_uri=dsn,
    )


def _cron_record(
    *,
    cron_id: str,
    next_run_date: datetime | None,
    end_time: datetime | None = None,
) -> dict[str, object]:
    created_at = (NOW - timedelta(days=2)).isoformat()
    return {
        "cron_id": cron_id,
        "thread_id": "thread-a",
        "end_time": end_time.isoformat() if end_time is not None else None,
        "schedule": "* * * * *",
        "created_at": created_at,
        "updated_at": created_at,
        "payload": {
            "assistant_id": "coach",
            "input": {"cron_wake": {"token": "durable"}},
            "config": {},
            "multitask_strategy": "enqueue",
        },
        "next_run_date": (
            next_run_date.isoformat() if next_run_date is not None else None
        ),
        "metadata": {"user_id": "owner-1"},
        "enabled": True,
        "user_id": "owner-1",
        "_timezone": "UTC",
        "auth_user": None,
    }


@pytest.mark.anyio
async def test_postgres_claim_due_is_atomic_under_concurrency(
    postgres_url: str,
) -> None:
    storage = await create_storage(_postgres_config(postgres_url))
    cron_id = f"claim-{uuid4()}"
    seen = NOW.isoformat()
    following = (NOW + timedelta(minutes=1)).isoformat()
    await storage.crons.save(
        cron_id,
        _cron_record(cron_id=cron_id, next_run_date=NOW),
    )
    claims: list[bool] = []

    async def claim() -> None:
        claims.append(
            await storage.crons.claim_due(
                cron_id,
                seen,
                following,
                NOW.isoformat(),
            )
        )

    try:
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(claim)
            _ = tasks.start_soon(claim)

        assert sorted(claims) == [False, True]
    finally:
        await storage.crons.delete(cron_id)
        await storage.aclose()


@pytest.mark.anyio
async def test_cron_survives_storage_rebuild_and_reconciles_from_fixed_clock(
    postgres_url: str,
) -> None:
    config = _postgres_config(postgres_url)
    cron_id = f"restart-{uuid4()}"
    original = await create_storage(config)
    await original.crons.save(
        cron_id,
        _cron_record(cron_id=cron_id, next_run_date=None),
    )
    await original.aclose()

    rebuilt = await create_storage(config)
    try:
        await reconcile_crons(rebuilt, clock=lambda: NOW)

        persisted = await rebuilt.crons.get(cron_id)
        assert persisted is not None
        assert persisted["enabled"] is True
        assert datetime.fromisoformat(str(persisted["next_run_date"])) > NOW
    finally:
        await rebuilt.crons.delete(cron_id)
        await rebuilt.aclose()


@pytest.mark.anyio
async def test_overdue_reconciliation_fires_once_without_backlog(
    postgres_url: str,
) -> None:
    storage = await create_storage(_postgres_config(postgres_url))
    cron_id = f"overdue-{uuid4()}"
    await storage.crons.save(
        cron_id,
        _cron_record(cron_id=cron_id, next_run_date=NOW - timedelta(days=1)),
    )
    submitter = RecordingSubmitter()

    try:
        await reconcile_crons(storage, clock=lambda: NOW)
        reconciled = await storage.crons.get(cron_id)
        assert reconciled is not None
        first_due = datetime.fromisoformat(str(reconciled["next_run_date"]))

        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(run_due_crons, submitter, storage, first_due)
            _ = tasks.start_soon(run_due_crons, submitter, storage, first_due)

        assert len(submitter.submissions) == 1
        advanced = await storage.crons.get(cron_id)
        assert advanced is not None
        assert datetime.fromisoformat(str(advanced["next_run_date"])) > first_due
    finally:
        await storage.crons.delete(cron_id)
        await storage.aclose()


@pytest.mark.anyio
async def test_reconciliation_leaves_expired_cron_dormant(postgres_url: str) -> None:
    storage = await create_storage(_postgres_config(postgres_url))
    cron_id = f"expired-{uuid4()}"
    await storage.crons.save(
        cron_id,
        _cron_record(
            cron_id=cron_id,
            next_run_date=NOW - timedelta(days=1),
            end_time=NOW - timedelta(hours=1),
        ),
    )

    try:
        await reconcile_crons(storage, clock=lambda: NOW)

        reconciled = await storage.crons.get(cron_id)
        assert reconciled is not None
        assert reconciled["enabled"] is True
        assert reconciled["next_run_date"] is None
    finally:
        await storage.crons.delete(cron_id)
        await storage.aclose()
