from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import CheckpointTuple


class AsyncCheckpointLister(Protocol):
    def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, object] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]: ...


@dataclass(frozen=True, slots=True, order=True)
class CheckpointRecord:
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str
    parent_root_id: str | None

    @property
    def record_id(self) -> tuple[str, str, str]:
        return self.thread_id, self.checkpoint_ns, self.checkpoint_id


async def checkpoint_records(
    saver: AsyncCheckpointLister, thread_id: str
) -> tuple[CheckpointRecord, ...]:
    """Enumerate every root and child checkpoint directly from the saver."""
    records: list[CheckpointRecord] = []
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    async for item in saver.alist(config):
        configurable = item.config.get("configurable", {})
        parents = item.metadata.get("parents", {})
        parent_root_id = parents.get("") if isinstance(parents, dict) else None
        records.append(
            CheckpointRecord(
                thread_id=str(configurable["thread_id"]),
                checkpoint_ns=str(configurable.get("checkpoint_ns", "")),
                checkpoint_id=str(configurable["checkpoint_id"]),
                parent_root_id=parent_root_id
                if isinstance(parent_root_id, str)
                else None,
            )
        )
    return tuple(sorted(records))


def diff_records(
    before: Iterable[CheckpointRecord], after: Iterable[CheckpointRecord]
) -> tuple[CheckpointRecord, ...]:
    prior = {record.record_id for record in before}
    return tuple(record for record in after if record.record_id not in prior)


def lineage_leaves(
    records: Iterable[CheckpointRecord],
) -> dict[str, tuple[CheckpointRecord, ...]]:
    groups: dict[str, list[CheckpointRecord]] = {}
    for record in records:
        if record.checkpoint_ns and record.parent_root_id is not None:
            groups.setdefault(record.parent_root_id, []).append(record)
    return {
        lineage: (max(group, key=lambda item: item.checkpoint_id),)
        for lineage, group in groups.items()
    }


__all__ = ["CheckpointRecord", "checkpoint_records", "diff_records", "lineage_leaves"]
