from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from langgraph.store.memory import InMemoryStore

from healthcare_rag.agent.state import CronWakePayload
from healthcare_rag.agent.store_data import (
    ReminderRecord,
    UploadRegistryRecord,
    Weekday,
)
from healthcare_rag.agent.uploads import reservation_id


async def seed_document_fixture(
    store: InMemoryStore,
    user_id: str,
    attachment_id: str,
    thread_id: str,
) -> None:
    record = UploadRegistryRecord(
        owner=user_id,
        intended_thread=thread_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        status="done",
        consumed=False,
        proposal={
            "sourceLabel": ".pdf · offline fixture",
            "candidateFields": [
                {
                    "key": "goalWeight",
                    "label": "Goal weight",
                    "value": "180 lb",
                    "needsReview": True,
                }
            ],
        },
    )
    await store.aput(
        ("users", user_id, "upload_registry"),
        reservation_id(attachment_id),
        record.model_dump(mode="json"),
        index=False,
    )


async def seed_reminder_fixture(
    store: InMemoryStore, user_id: str, thread_id: str
) -> CronWakePayload:
    wake = CronWakePayload(
        reminder_id="offline-reminder",
        user_id=user_id,
        thread_id=thread_id,
        wake_token="offline-token",
    )
    record = ReminderRecord(
        reminder_id=wake["reminder_id"],
        title="Weekly check-in",
        weekday=Weekday.MON,
        time="09:00",
        active=True,
        cron_id="offline-cron",
        thread_id=thread_id,
        wake_token=wake["wake_token"],
        next_run_date=date(2026, 8, 24),
        created_ts=datetime(2026, 8, 21, tzinfo=UTC),
    )
    await store.aput(
        ("users", user_id, "reminders"),
        record.reminder_id,
        record.model_dump(mode="json"),
        index=False,
    )
    return wake


__all__ = ["seed_document_fixture", "seed_reminder_fixture"]
