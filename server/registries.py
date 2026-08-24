from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, LiteralString, Protocol, TypeAlias

from psycopg import AsyncConnection, sql
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import TypeAdapter

Record: TypeAlias = dict[str, object]
RegistryPool: TypeAlias = AsyncConnectionPool[AsyncConnection[DictRow]]


_RECORD_ADAPTER: Final = TypeAdapter(Record)
_COUNT_ADAPTER: Final = TypeAdapter(int)


class Registry(Protocol):
    async def get(self, record_id: str) -> Record | None: ...

    async def save(self, record_id: str, record: Record) -> None: ...

    async def delete(self, record_id: str) -> None: ...

    async def all(self) -> list[Record]: ...

    async def contains(self, record_id: str) -> bool: ...

    async def count(self) -> int: ...

    async def create_if_absent(self, record_id: str, record: Record) -> bool: ...


class RunRegistry(Registry, Protocol):
    async def set_status(self, record_id: str, status: str) -> None: ...


class CronRegistry(Registry, Protocol):
    async def set_schedule_state(
        self, record_id: str, next_run_date: str | None, updated_at: str
    ) -> None: ...

    async def claim_due(
        self,
        record_id: str,
        expected_next_run_date: str | None,
        new_next_run_date: str | None,
        updated_at: str,
    ) -> bool: ...


class MemoryRegistry:
    def __init__(self) -> None:
        self._records: dict[str, Record] = {}

    async def get(self, record_id: str) -> Record | None:
        return self._records.get(record_id)

    async def save(self, record_id: str, record: Record) -> None:
        self._records[record_id] = record

    async def delete(self, record_id: str) -> None:
        _ = self._records.pop(record_id, None)

    async def all(self) -> list[Record]:
        return list(self._records.values())

    async def contains(self, record_id: str) -> bool:
        return record_id in self._records

    async def count(self) -> int:
        return len(self._records)

    async def create_if_absent(self, record_id: str, record: Record) -> bool:
        created = record_id not in self._records
        _ = self._records.setdefault(record_id, record)
        return created

    async def set_status(self, record_id: str, status: str) -> None:
        self._records[record_id]["status"] = status

    async def set_schedule_state(
        self, record_id: str, next_run_date: str | None, updated_at: str
    ) -> None:
        record = self._records[record_id]
        record["next_run_date"] = next_run_date
        record["updated_at"] = updated_at

    async def claim_due(
        self,
        record_id: str,
        expected_next_run_date: str | None,
        new_next_run_date: str | None,
        updated_at: str,
    ) -> bool:
        record = self._records.get(record_id)
        if record is None or record.get("next_run_date") != expected_next_run_date:
            return False
        record["next_run_date"] = new_next_run_date
        record["updated_at"] = updated_at
        return True

    def __getitem__(self, record_id: str) -> Record:
        return self._records[record_id]

    def __setitem__(self, record_id: str, record: Record) -> None:
        self._records[record_id] = record

    def __delitem__(self, record_id: str) -> None:
        del self._records[record_id]

    def __contains__(self, record_id: str) -> bool:
        return record_id in self._records

    def __len__(self) -> int:
        return len(self._records)


@dataclass(frozen=True, slots=True)
class MemoryRegistries:
    threads: MemoryRegistry = field(default_factory=MemoryRegistry)
    runs: MemoryRegistry = field(default_factory=MemoryRegistry)
    crons: MemoryRegistry = field(default_factory=MemoryRegistry)


_REGISTRY_DDL_LOCK: Final = 79560077811783
_REGISTRY_DDL: Final[tuple[LiteralString, ...]] = (
    """CREATE TABLE IF NOT EXISTS hc_threads (
        thread_id TEXT PRIMARY KEY,
        record JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NULL
    )""",
    """CREATE TABLE IF NOT EXISTS hc_runs (
        run_id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        status TEXT NOT NULL,
        record JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS hc_crons (
        cron_id TEXT PRIMARY KEY,
        thread_id TEXT NULL,
        enabled BOOLEAN NOT NULL,
        next_run_date TIMESTAMPTZ NULL,
        record JSONB NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS hc_runs_thread_id_idx ON hc_runs(thread_id)",
    "CREATE INDEX IF NOT EXISTS hc_threads_expires_at_idx ON hc_threads(expires_at)",
    "CREATE INDEX IF NOT EXISTS hc_crons_next_run_date_idx ON hc_crons(next_run_date)",
)
_SET_RUN_STATUS: Final[LiteralString] = """UPDATE hc_runs SET
    record = jsonb_set(record, '{status}', to_jsonb(%s::text)),
    status = %s WHERE run_id = %s
"""
_SET_CRON_SCHEDULE: Final[LiteralString] = """UPDATE hc_crons SET record =
    jsonb_set(jsonb_set(record, '{next_run_date}',
    COALESCE(to_jsonb(%s::text), 'null'::jsonb)),
    '{updated_at}', to_jsonb(%s::text)), next_run_date = %s
    WHERE cron_id = %s
"""
_CLAIM_CRON_DUE: Final[LiteralString] = """UPDATE hc_crons SET record =
    jsonb_set(jsonb_set(record, '{next_run_date}',
    COALESCE(to_jsonb(%s::text), 'null'::jsonb)),
    '{updated_at}', to_jsonb(%s::text)), next_run_date = %s
    WHERE cron_id = %s AND next_run_date IS NOT DISTINCT FROM %s
    RETURNING cron_id
"""


class PostgresRegistry:
    def __init__(
        self,
        pool: RegistryPool,
        table: str,
        id_column: str,
        denormalized_columns: tuple[str, ...],
    ) -> None:
        self._pool: RegistryPool = pool
        self._columns: tuple[str, ...] = denormalized_columns
        table_identifier = sql.Identifier(table)
        id_identifier = sql.Identifier(id_column)
        column_identifiers = tuple(
            sql.Identifier(column)
            for column in (id_column, "record", *denormalized_columns)
        )
        update_columns = tuple(
            sql.Identifier(column) for column in ("record", *denormalized_columns)
        )
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(column, column)
            for column in update_columns
        )
        insert = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) ").format(
            table_identifier,
            sql.SQL(", ").join(column_identifiers),
            sql.SQL(", ").join(sql.Placeholder() for _ in column_identifiers),
            id_identifier,
        )
        self._get_query: sql.Composed = sql.SQL(
            "SELECT record FROM {} WHERE {} = %s"
        ).format(table_identifier, id_identifier)
        self._save_query: sql.Composed = insert + sql.SQL("DO UPDATE SET {} ").format(
            assignments
        )
        self._delete_query: sql.Composed = sql.SQL(
            "DELETE FROM {} WHERE {} = %s"
        ).format(table_identifier, id_identifier)
        self._all_query: sql.Composed = sql.SQL("SELECT record FROM {}").format(
            table_identifier
        )
        self._contains_query: sql.Composed = sql.SQL(
            "SELECT 1 AS present FROM {} WHERE {} = %s"
        ).format(table_identifier, id_identifier)
        self._count_query: sql.Composed = sql.SQL(
            "SELECT COUNT(*) AS count FROM {}"
        ).format(table_identifier)
        self._create_query: sql.Composed = insert + sql.SQL(
            "DO NOTHING RETURNING {}"
        ).format(id_identifier)

    def _denormalized(self, record: Record) -> tuple[object, ...]:
        return tuple(record.get(column) for column in self._columns)

    async def get(self, record_id: str) -> Record | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(self._get_query, (record_id,))
            row = await cursor.fetchone()
        return None if row is None else _RECORD_ADAPTER.validate_python(row["record"])

    async def save(self, record_id: str, record: Record) -> None:
        values = (record_id, Jsonb(record), *self._denormalized(record))
        async with self._pool.connection() as connection:
            _ = await connection.execute(self._save_query, values)

    async def delete(self, record_id: str) -> None:
        async with self._pool.connection() as connection:
            _ = await connection.execute(self._delete_query, (record_id,))

    async def all(self) -> list[Record]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(self._all_query)
            rows = await cursor.fetchall()
        return [_RECORD_ADAPTER.validate_python(row["record"]) for row in rows]

    async def contains(self, record_id: str) -> bool:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(self._contains_query, (record_id,))
            row = await cursor.fetchone()
        return row is not None

    async def count(self) -> int:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(self._count_query)
            row = await cursor.fetchone()
        assert row is not None
        return _COUNT_ADAPTER.validate_python(row["count"])

    async def create_if_absent(self, record_id: str, record: Record) -> bool:
        values = (record_id, Jsonb(record), *self._denormalized(record))
        async with self._pool.connection() as connection:
            cursor = await connection.execute(self._create_query, values)
            row = await cursor.fetchone()
        return row is not None


class PostgresRunRegistry(PostgresRegistry):
    async def set_status(self, record_id: str, status: str) -> None:
        async with self._pool.connection() as connection:
            _ = await connection.execute(
                _SET_RUN_STATUS,
                (status, status, record_id),
            )


class PostgresCronRegistry(PostgresRegistry):
    async def set_schedule_state(
        self, record_id: str, next_run_date: str | None, updated_at: str
    ) -> None:
        async with self._pool.connection() as connection:
            _ = await connection.execute(
                _SET_CRON_SCHEDULE,
                (next_run_date, updated_at, next_run_date, record_id),
            )

    async def claim_due(
        self,
        record_id: str,
        expected_next_run_date: str | None,
        new_next_run_date: str | None,
        updated_at: str,
    ) -> bool:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                _CLAIM_CRON_DUE,
                (
                    new_next_run_date,
                    updated_at,
                    new_next_run_date,
                    record_id,
                    expected_next_run_date,
                ),
            )
            row = await cursor.fetchone()
        return row is not None


@dataclass(frozen=True, slots=True)
class PostgresRegistries:
    pool: RegistryPool = field(repr=False)
    threads: PostgresRegistry = field(init=False)
    runs: PostgresRunRegistry = field(init=False)
    crons: PostgresCronRegistry = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "threads",
            PostgresRegistry(
                self.pool, "hc_threads", "thread_id", ("updated_at", "expires_at")
            ),
        )
        object.__setattr__(
            self,
            "runs",
            PostgresRunRegistry(
                self.pool,
                "hc_runs",
                "run_id",
                ("thread_id", "status", "created_at"),
            ),
        )
        object.__setattr__(
            self,
            "crons",
            PostgresCronRegistry(
                self.pool,
                "hc_crons",
                "cron_id",
                ("thread_id", "enabled", "next_run_date"),
            ),
        )

    async def setup(self) -> None:
        async with self.pool.connection() as connection:
            _ = await connection.execute(
                "SELECT pg_advisory_lock(%s)", (_REGISTRY_DDL_LOCK,)
            )
            try:
                for statement in _REGISTRY_DDL:
                    _ = await connection.execute(statement)
            finally:
                _ = await connection.execute(
                    "SELECT pg_advisory_unlock(%s)", (_REGISTRY_DDL_LOCK,)
                )
