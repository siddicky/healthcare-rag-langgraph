from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pydantic import JsonValue

from scripts.forget_member import (
    ERASE_MARKER_NAME,
    DeleteFailed,
    delete_snapshot,
    has_erase_marker,
    snapshot_thread_ids,
    wait_until_terminal,
)


@dataclass(slots=True)
class FakeMemberAPI:
    pages: dict[int, list[str]] = field(default_factory=dict)
    statuses: list[str] = field(default_factory=lambda: ["idle"])
    fail_on: str | None = None
    searches: list[tuple[int, int]] = field(default_factory=list)
    status_reads: list[str] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)

    def search_threads(self, limit: int, offset: int) -> list[str]:
        self.searches.append((limit, offset))
        return self.pages.get(offset, [])

    def thread_status(self, thread_id: str) -> str:
        self.status_reads.append(thread_id)
        index = min(len(self.status_reads) - 1, len(self.statuses) - 1)
        return self.statuses[index]

    def delete_thread(self, thread_id: str) -> None:
        self.deletes.append(thread_id)
        if thread_id == self.fail_on:
            raise DeleteFailed(thread_id=thread_id, status_code=503)


def test_marker_latch_detects_only_named_ai_message() -> None:
    # Given
    state: JsonValue = {
        "values": {
            "messages": [
                {"type": "ai", "name": "ordinary", "content": ERASE_MARKER_NAME},
                {"type": "ai", "name": ERASE_MARKER_NAME, "content": "done"},
            ]
        }
    }

    # When
    latched = has_erase_marker(state)

    # Then
    assert latched is True


def test_disconnected_stream_polls_status_until_not_busy() -> None:
    # Given
    api = FakeMemberAPI(statuses=["busy", "busy", "idle"])

    # When
    wait_until_terminal(api, "current", stream_reached_eof=False, poll_interval=0)

    # Then
    assert api.status_reads == ["current", "current", "current"]


def test_snapshot_paginates_more_than_one_hundred_before_deletion() -> None:
    # Given
    first_page = [f"thread-{index:03d}" for index in range(100)]
    api = FakeMemberAPI(pages={0: first_page, 100: ["thread-100", "current"]})

    # When
    snapshot = snapshot_thread_ids(api)

    # Then
    assert snapshot == [*first_page, "thread-100", "current"]
    assert api.searches == [(100, 0), (100, 100)]
    assert api.deletes == []


def test_non_current_delete_failure_stops_and_preserves_current() -> None:
    # Given
    api = FakeMemberAPI(fail_on="thread-b")

    # When / Then
    with pytest.raises(DeleteFailed, match="thread-b"):
        delete_snapshot(api, ["thread-c", "current", "thread-a", "thread-b"], "current")
    assert api.deletes == ["thread-a", "thread-b"]
    assert "current" not in api.deletes
