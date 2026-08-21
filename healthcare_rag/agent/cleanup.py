from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import httpx
from pydantic import JsonValue
from starlette.requests import Request

from healthcare_rag.agent.store_data import ReminderEdit, edit_reminder, list_reminders
from healthcare_rag.agent.uploads import internal_headers

PAGE_SIZE: Final = 100


@dataclass(frozen=True, slots=True)
class CleanupResult:
    ready: bool
    notice: str | None = None


def _thread_id(request: Request) -> str:
    return request.url.path.split("/")[2]


def _cron_items(payload: JsonValue) -> list[dict[str, JsonValue]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        items = payload.get("items", [])
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


async def prepare_thread_deletion(request: Request) -> CleanupResult:
    from langgraph_api.store import get_store

    identity = request.user.identity
    thread_id = _thread_id(request)
    store = await get_store()
    marker_namespace = ("users", identity, "gate")
    marker_key = f"cleanup_pending:{thread_id}"
    await store.aput(
        marker_namespace,
        marker_key,
        {"thread_id": thread_id, "status": "cleanup_pending"},
        index=False,
    )

    reminders = [
        reminder
        for reminder in await list_reminders(store, identity)
        if reminder.thread_id == thread_id
    ]
    paused = []
    for reminder in reminders:
        try:
            paused.append(
                await edit_reminder(
                    store,
                    identity,
                    reminder.reminder_id,
                    ReminderEdit(active=False),
                )
            )
        except (ValueError, RuntimeError):
            continue
    if len(paused) != len(reminders):
        return CleanupResult(
            ready=False,
            notice="Reminders are paused; deletion cleanup can be retried.",
        )

    headers = internal_headers() | {"x-internal-owner": identity}
    try:
        async with httpx.AsyncClient(
            base_url=str(request.base_url), timeout=10.0
        ) as client:
            for reminder in paused:
                if reminder.cron_id is None:
                    continue
                response = await client.delete(
                    f"/runs/crons/{reminder.cron_id}", headers=headers
                )
                if response.status_code not in {200, 204, 404}:
                    return CleanupResult(
                        ready=False,
                        notice="Reminders are paused; deletion cleanup can be retried.",
                    )
            offset = 0
            while True:
                response = await client.post(
                    "/runs/crons/search",
                    headers=headers,
                    json={"limit": PAGE_SIZE, "offset": offset},
                )
                if response.status_code != 200:
                    return CleanupResult(
                        ready=False,
                        notice="Reminders are paused; deletion cleanup can be retried.",
                    )
                items = _cron_items(response.json())
                matching: list[dict[str, JsonValue]] = []
                for item in items:
                    metadata = item.get("metadata")
                    if (
                        item.get("thread_id") == thread_id
                        and isinstance(metadata, dict)
                        and metadata.get("user_id") == identity
                    ):
                        matching.append(item)
                for item in matching:
                    cron_id = item.get("cron_id")
                    if not isinstance(cron_id, str):
                        continue
                    deleted = await client.delete(
                        f"/runs/crons/{cron_id}", headers=headers
                    )
                    if deleted.status_code not in {200, 204, 404}:
                        return CleanupResult(
                            ready=False,
                            notice="Reminders are paused; deletion cleanup can be retried.",
                        )
                if len(items) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE
    except httpx.HTTPError:
        return CleanupResult(
            ready=False,
            notice="Reminders are paused; deletion cleanup can be retried.",
        )
    return CleanupResult(ready=True)


async def clear_cleanup_marker(request: Request) -> None:
    from langgraph_api.store import get_store

    identity = request.user.identity
    thread_id = _thread_id(request)
    store = await get_store()
    await store.adelete(("users", identity, "gate"), f"cleanup_pending:{thread_id}")


__all__ = ["CleanupResult", "clear_cleanup_marker", "prepare_thread_deletion"]
