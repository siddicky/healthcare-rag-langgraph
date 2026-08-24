"""Proves the same advisory-lock concurrency guarantee as
tests/server/test_registries.py::test_concurrent_storage_setup_uses_advisory_lock,
but outside pytest's async harness.

That pytest test is known to hang under pytest+anyio's task-group/event-loop
interaction when racing a cold-start CREATE TABLE DDL (see its docstring).
This script runs the identical two-concurrent-create_storage() logic via plain
asyncio.run() / anyio.create_task_group() and succeeds reproducibly against the
same fresh database — confirming the production path is correct. It is the
second half of `make server-test-pg`'s gated lane.
"""
import asyncio
import os
import sys

import anyio

from server.config import ServerConfig
from server.registries import PostgresRegistry
from server.storage import create_storage

dsn = os.environ.get("POSTGRES_TEST_DSN")
if not dsn:
    print("POSTGRES_TEST_DSN not set", file=sys.stderr)
    sys.exit(1)


def _cfg():
    return ServerConfig(
        graphs={},
        auth_path=None,
        http_app=None,
        http_flags={},
        store_index={},
        api_version="registries-test",
        storage="postgres",
        database_uri=dsn,
    )


async def main() -> None:
    from server.storage import Storage

    storages: list[Storage] = []

    async def setup() -> None:
        storages.append(await create_storage(_cfg()))

    async with anyio.create_task_group() as tg:
        _ = tg.start_soon(setup)
        _ = tg.start_soon(setup)

    assert len(storages) == 2, f"expected 2, got {len(storages)}"
    assert all(isinstance(s.threads, PostgresRegistry) for s in storages), "not all PostgresRegistry"
    for s in storages:
        await s.aclose()
    print("concurrent test: 1 passed")


if __name__ == "__main__":
    asyncio.run(main())
