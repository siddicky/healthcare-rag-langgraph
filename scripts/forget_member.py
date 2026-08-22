#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "pydantic"]
# ///

# ─── How to run ───
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Export SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY,
#    COACH_MEMBER_EMAIL, and COACH_MEMBER_PASSWORD.
# 3. Run: uv run python scripts/forget_member.py --url "$LANGGRAPH_DEPLOYMENT_URL"
# ──────────────────

from __future__ import annotations

import argparse
import os
import time
from typing import Final, final

import httpx
from pydantic import BaseModel, ConfigDict

from scripts.forget_member_api import (
    ERASE_MARKER_NAME as API_ERASE_MARKER_NAME,
)
from scripts.forget_member_api import (
    ConfigurationError,
    DeleteFailed,
    ErasureFailed,
    HTTPMemberAPI,
    MemberAPI,
    has_erase_marker,
    sign_in,
)

PAGE_SIZE: Final = 100
ERASE_MARKER_NAME: Final = API_ERASE_MARKER_NAME


@final
class CLIArgs(BaseModel):
    model_config = ConfigDict(frozen=True)
    url: str
    dry_run: bool = False


def wait_until_terminal(
    api: MemberAPI,
    thread_id: str,
    *,
    stream_reached_eof: bool,
    poll_interval: float = 2.0,
    max_polls: int = 60,
) -> None:
    """Apply the shared EOF-or-status-poll terminality rule."""
    if stream_reached_eof:
        return
    for attempt in range(max_polls):
        if api.thread_status(thread_id) != "busy":
            return
        if attempt + 1 < max_polls:
            time.sleep(poll_interval)
    raise ErasureFailed("erase run remained busy after the polling limit")


def snapshot_thread_ids(api: MemberAPI) -> list[str]:
    """Read every owned thread before issuing any deletion."""
    snapshot: list[str] = []
    offset = 0
    while True:
        page = api.search_threads(PAGE_SIZE, offset)
        snapshot.extend(page)
        if len(page) < PAGE_SIZE:
            return snapshot
        offset += len(page)


def delete_snapshot(api: MemberAPI, snapshot: list[str], current_thread_id: str) -> None:
    """Delete sorted non-current threads first and the marker thread last."""
    others = sorted(thread_id for thread_id in snapshot if thread_id != current_thread_id)
    for thread_id in others:
        api.delete_thread(thread_id)
    api.delete_thread(current_thread_id)


def run(url: str, *, dry_run: bool) -> None:
    token = sign_in(os.environ)
    with HTTPMemberAPI(url, token) as api:
        if dry_run:
            snapshot = snapshot_thread_ids(api)
            print(f"DRY RUN: would delete {len(snapshot)} owned thread(s):")
            for thread_id in snapshot:
                print(thread_id)
            return
        current = api.create_thread()
        stream_eof, stream_marker = api.erase_turn(current)
        wait_until_terminal(api, current, stream_reached_eof=stream_eof)
        if not stream_marker and not has_erase_marker(api.thread_state(current)):
            raise ErasureFailed(
                "Phase 1 did not produce erase_confirmation_v1; no threads were deleted"
            )
        snapshot = snapshot_thread_ids(api)
        delete_snapshot(api, snapshot, current)
        print(f"Erasure complete: deleted {len(snapshot)} owned thread(s).")


def main() -> int:
    parser = argparse.ArgumentParser(description="Erase the signed-in member's coach data")
    _ = parser.add_argument("--url", required=True, help="LangGraph deployment URL")
    _ = parser.add_argument("--dry-run", action="store_true", help="list threads without mutating")
    args = CLIArgs.model_validate(vars(parser.parse_args()))
    try:
        run(args.url, dry_run=args.dry_run)
    except (ConfigurationError, DeleteFailed, ErasureFailed, httpx.HTTPError) as error:
        print(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
