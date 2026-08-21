from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from pydantic import JsonValue

from healthcare_rag.agent.compose_ui import validate_composition
from healthcare_rag.agent.store_data import (
    MAX_ACTIVE_REMINDERS,
    ReminderCapError,
    ReminderRecord,
    Weekday,
    create_reminder,
)

from .coach_engine import DocumentDecision, build_offline_coach_engine

JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class AgentCaseResult:
    case_id: str
    tag: str
    passed: bool


DOCUMENT_CASES: Final[tuple[tuple[str, DocumentDecision, str | None], ...]] = (
    (
        "document_accept",
        {"accept": True, "fields": [{"key": "goalWeight", "value": "180 lb"}]},
        "180 lb",
    ),
    (
        "document_edit",
        {"accept": True, "fields": [{"key": "goalWeight", "value": "175 lb"}]},
        "175 lb",
    ),
    ("document_discard", {"accept": False}, None),
)


async def _document_cases() -> tuple[AgentCaseResult, ...]:
    results: list[AgentCaseResult] = []
    for index, (case_id, decision, expected) in enumerate(DOCUMENT_CASES, start=1):
        engine = build_offline_coach_engine()
        thread_id = f"agent-document-{index}"
        attachment_id = f"00000000-0000-0000-0000-{index:012d}"
        await engine.seed_document(attachment_id, thread_id=thread_id)
        routed = await engine.run_turn(
            "Review this document.",
            thread_id=thread_id,
            attachment_id=attachment_id,
        )
        _ = await engine.resume_document(thread_id=thread_id, decision=decision)
        facts = await engine.profile_facts()
        passed = routed.route == "claim_document" and routed.route_a_leaf is None
        passed = passed and ((expected in facts) if expected is not None else not facts)
        results.append(AgentCaseResult(case_id, "document", passed))
    return tuple(results)


async def _reminder_cases() -> tuple[AgentCaseResult, ...]:
    engine = build_offline_coach_engine()
    wake = await engine.seed_reminder(thread_id="agent-reminder-delivery")
    delivered = await engine.run_wake(wake)
    delivery = AgentCaseResult(
        "reminder_delivery",
        "reminder",
        delivered.route == "reminder_delivery" and delivered.route_a_leaf is None,
    )

    capped = build_offline_coach_engine()
    for index in range(MAX_ACTIVE_REMINDERS):
        _ = await create_reminder(
            capped.store,
            "offline-member",
            ReminderRecord(
                reminder_id=f"cap-{index}",
                title=f"Reminder {index}",
                weekday=Weekday.MON,
                time="09:00",
                active=True,
                cron_id=f"cron-{index}",
                thread_id="agent-reminder-cap",
                wake_token=f"token-{index}",
                next_run_date=None,
                created_ts=datetime(2026, 8, 21, tzinfo=UTC),
            ),
        )
    cap_enforced = False
    try:
        _ = await create_reminder(
            capped.store,
            "offline-member",
            ReminderRecord(
                reminder_id="over-cap",
                title="Over cap",
                weekday=Weekday.MON,
                time="09:00",
                active=True,
                cron_id="over-cap-cron",
                thread_id="agent-reminder-cap",
                wake_token="over-cap-token",
                next_run_date=None,
                created_ts=datetime(2026, 8, 21, tzinfo=UTC),
            ),
        )
    except ReminderCapError:
        cap_enforced = True
    return delivery, AgentCaseResult("reminder_cap", "reminder", cap_enforced)


def _catalog_cases() -> tuple[AgentCaseResult, ...]:
    scope = "catalog-scope"
    envelope = json.dumps(
        {
            "turn_scope_id": scope,
            "block_id": "trend:weight",
            "data": {
                "label": "Weight",
                "value": "180",
                "unit": "lb",
                "delta": "-1.0 lb",
                "deltaGood": True,
                "points": [181.0, 180.0],
            },
        }
    )

    def reference(pointer: str) -> JsonObject:
        return {
            "__ref": {
                "turn_scope_id": scope,
                "block_id": "trend:weight",
                "pointer": pointer,
            }
        }

    hydrated: JsonObject = {
        "tree": [
            {
                "component": "TrendCard",
                "props": {
                    key: reference(f"/{key}")
                    for key in (
                        "label",
                        "value",
                        "unit",
                        "delta",
                        "deltaGood",
                        "points",
                    )
                },
            }
        ]
    }
    literal: JsonObject = {
        "tree": [{"component": "TrendCard", "props": {"value": "180 lb"}}]
    }
    return (
        AgentCaseResult(
            "catalog_ref_hydrated",
            "catalog",
            validate_composition(hydrated, [envelope], scope).valid,
        ),
        AgentCaseResult(
            "catalog_literal_rejected",
            "catalog",
            not validate_composition(literal, [envelope], scope).valid,
        ),
    )


async def run_agent_cases() -> tuple[AgentCaseResult, ...]:
    return (*await _document_cases(), *await _reminder_cases(), *_catalog_cases())


__all__ = ["AgentCaseResult", "run_agent_cases"]
