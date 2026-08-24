from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TypeAlias

Record: TypeAlias = dict[str, object]


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


class MemoryRegistry:
    def __init__(self) -> None:
        self._records: dict[str, Record] = {}

    async def get(self, record_id: str) -> Record | None:
        return self._records.get(record_id)

    async def save(self, record_id: str, record: Record) -> None:
        self._records[record_id] = record

    async def delete(self, record_id: str) -> None:
        self._records.pop(record_id, None)

    async def all(self) -> list[Record]:
        return list(self._records.values())

    async def contains(self, record_id: str) -> bool:
        return record_id in self._records

    async def count(self) -> int:
        return len(self._records)

    async def create_if_absent(self, record_id: str, record: Record) -> bool:
        created = record_id not in self._records
        self._records.setdefault(record_id, record)
        return created

    async def set_status(self, record_id: str, status: str) -> None:
        self._records[record_id]["status"] = status

    async def set_schedule_state(
        self, record_id: str, next_run_date: str | None, updated_at: str
    ) -> None:
        record = self._records[record_id]
        record["next_run_date"] = next_run_date
        record["updated_at"] = updated_at

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
