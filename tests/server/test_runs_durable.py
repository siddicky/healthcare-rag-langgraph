from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import override
from uuid import uuid4

import anyio
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from starlette.applications import Starlette
from starlette.requests import Request

from server.config import ServerConfig
from server.registries import PostgresRunRegistry, Record, RegistryPool
from server.run_engine import (
    PERSISTED_PAYLOAD_REDACTION,
    ResumeCommand,
    RunEngine,
    RunRequest,
    reconcile_interrupted_runs,
)
from server.runs import cancel_run, get_run, join_run, list_runs
from server.storage import Storage, create_storage


def _config(postgres_url: str) -> ServerConfig:
    return ServerConfig(
        graphs={},
        auth_path=None,
        http_app=None,
        http_flags={},
        store_index={},
        api_version="test",
        storage="postgres",
        database_uri=postgres_url,
    )


def _request(app: Starlette, path: str, thread_id: str, run_id: str | None = None) -> Request:
    path_params = {"thread_id": thread_id}
    if run_id is not None:
        path_params["run_id"] = run_id
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "app": app,
            "path_params": path_params,
        }
    )


class StubPostgresRunRegistry(PostgresRunRegistry):
    def __init__(self, records: list[Record]) -> None:
        pool: RegistryPool = AsyncConnectionPool(
            conninfo="", open=False, kwargs={"row_factory": dict_row}
        )
        super().__init__(
            pool, "hc_runs", "run_id", ("thread_id", "status", "created_at")
        )
        self.records: list[Record] = records
        self.transitions: list[tuple[str, str]] = []

    @override
    async def all(self) -> list[Record]:
        return self.records

    @override
    async def set_status(self, record_id: str, status: str) -> None:
        self.transitions.append((record_id, status))
        for record in self.records:
            if record["run_id"] == record_id:
                record["status"] = status


@pytest.mark.anyio
async def test_reconcile_interrupts_only_nonterminal_postgres_runs() -> None:
    records: list[Record] = [
        {"run_id": "pending", "status": "pending"},
        {"run_id": "running", "status": "running"},
        {"run_id": "success", "status": "success"},
        {"run_id": "error", "status": "error"},
        {"run_id": "interrupted", "status": "interrupted"},
        {"run_id": "timeout", "status": "timeout"},
    ]
    registry = StubPostgresRunRegistry(records)
    storage = Storage(
        saver=InMemorySaver(),
        store=InMemoryStore(index=None),
        runs=registry,
    )

    first = await reconcile_interrupted_runs(storage)
    second = await reconcile_interrupted_runs(storage)

    assert first == 2
    assert second == 0
    assert registry.transitions == [
        ("pending", "interrupted"),
        ("running", "interrupted"),
    ]


@pytest.mark.anyio
async def test_memory_registry_keeps_raw_run_input() -> None:
    literal = "alice.patient@example.com"
    storage = await create_storage(
        ServerConfig(
            graphs={},
            auth_path=None,
            http_app=None,
            http_flags={},
            store_index={},
            api_version="test",
        )
    )
    record: Record = {}
    async with anyio.create_task_group() as tasks:
        engine = RunEngine(storage, {}, tasks)
        engine.active["thread-memory"] = "existing"

        record = await engine.submit(
            "thread-memory",
            RunRequest(
                assistant_id="toy",
                input={"question": literal},
                multitask_strategy="enqueue",
                stream_resumable=True,
            ),
        )

    stored = await storage.runs.get(str(record["run_id"]))
    assert stored is not None
    assert literal in json.dumps(stored)
    assert stored["stream_resumable"] is True


@pytest.mark.anyio
async def test_postgres_row_redacts_payloads_and_runtime_keeps_input(postgres_url: str) -> None:
    literal = "alice.patient@example.com"
    storage = await create_storage(_config(postgres_url))
    run_ids: list[str] = []
    input_run_id = ""
    command_run_id = ""
    try:
        async with anyio.create_task_group() as tasks:
            engine = RunEngine(storage, {}, tasks)
            engine.active["thread-phi"] = "existing"
            engine.active["thread-command"] = "existing"
            record = await engine.submit(
                "thread-phi",
                RunRequest(
                    assistant_id="toy",
                    input={"question": literal},
                    multitask_strategy="enqueue",
                    stream_resumable=True,
                ),
            )
            input_run_id = str(record["run_id"])
            command_record = await engine.submit(
                "thread-command",
                RunRequest(
                    assistant_id="toy",
                    command=ResumeCommand(resume={"answer": literal}),
                    multitask_strategy="enqueue",
                ),
            )
            command_run_id = str(command_record["run_id"])
            run_ids.extend((input_run_id, command_run_id))
            assert engine.runtime[input_run_id].request.input == {"question": literal}

        async with await AsyncConnection.connect(postgres_url) as connection:
            cursor = await connection.execute(
                "SELECT run_id, record FROM hc_runs WHERE run_id = ANY(%s)", (run_ids,)
            )
            rows = await cursor.fetchall()

        stored = {row[0]: row[1] for row in rows}
        stored_json = json.dumps(stored)
        assert literal not in stored_json
        assert stored[input_run_id]["input"] == PERSISTED_PAYLOAD_REDACTION
        assert stored[input_run_id]["stream_resumable"] is True
        assert stored[command_run_id]["command"] == PERSISTED_PAYLOAD_REDACTION
    finally:
        for run_id in run_ids:
            await storage.runs.delete(run_id)
        await storage.aclose()


@pytest.mark.anyio
async def test_restarted_storage_reconciles_and_reads_terminal_runs(postgres_url: str) -> None:
    suffix = str(uuid4())
    thread_id = f"thread-{suffix}"
    pending_id = f"pending-{suffix}"
    completed_id = f"completed-{suffix}"
    now = datetime.now(UTC).isoformat()
    first = await create_storage(_config(postgres_url))
    try:
        await first.threads.save(
            thread_id,
            {"thread_id": thread_id, "updated_at": now, "expires_at": None},
        )
        await first.runs.save(
            pending_id,
            {
                "run_id": pending_id,
                "thread_id": thread_id,
                "status": "running",
                "created_at": now,
                "input": PERSISTED_PAYLOAD_REDACTION,
            },
        )
        await first.runs.save(
            completed_id,
            {
                "run_id": completed_id,
                "thread_id": thread_id,
                "status": "success",
                "created_at": now,
                "input": PERSISTED_PAYLOAD_REDACTION,
                "metadata": {"result": "complete"},
            },
        )
    finally:
        await first.aclose()

    restarted = await create_storage(_config(postgres_url))
    try:
        assert await reconcile_interrupted_runs(restarted) >= 1
        assert await reconcile_interrupted_runs(restarted) == 0
        pending = await restarted.runs.get(pending_id)
        assert pending is not None and pending["status"] == "interrupted"

        async with anyio.create_task_group() as tasks:
            engine = RunEngine(restarted, {}, tasks)
            app = Starlette()
            app.state.run_engine = engine
            get_response = await get_run(
                _request(app, f"/threads/{thread_id}/runs/{completed_id}", thread_id, completed_id)
            )
            list_response = await list_runs(
                _request(app, f"/threads/{thread_id}/runs", thread_id)
            )
            join_response = await join_run(
                _request(app, f"/threads/{thread_id}/runs/{completed_id}/join", thread_id, completed_id)
            )
            cancel_response = await cancel_run(
                _request(app, f"/threads/{thread_id}/runs/{completed_id}/cancel", thread_id, completed_id)
            )
            assert get_response.status_code == 200
            assert json.loads(bytes(get_response.body))["metadata"] == {"result": "complete"}
            listed = json.loads(bytes(list_response.body))
            assert {record["run_id"] for record in listed} == {pending_id, completed_id}
            assert join_response.status_code == 404
            assert cancel_response.status_code == 404
    finally:
        await restarted.runs.delete(pending_id)
        await restarted.runs.delete(completed_id)
        await restarted.threads.delete(thread_id)
        await restarted.aclose()
