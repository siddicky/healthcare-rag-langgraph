from __future__ import annotations

import json
from datetime import date
from typing import Annotated, ClassVar, Literal, TypeAlias, assert_never

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator
from pydantic_core import PydanticCustomError

from healthcare_rag.agent.store_data import (
    TIME_PATTERN,
    AddMutation,
    ApprovalEvent,
    CancelMutation,
    RescheduleMutation,
    ScheduleEntry,
)

ScheduleKind: TypeAlias = Literal["injection", "check-in", "appointment"]
JsonObject: TypeAlias = dict[str, JsonValue]


class RequestModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class Destination(RequestModel):
    date: date
    time: str | None = None

    @field_validator("time")
    @classmethod
    def valid_time(cls, value: str | None) -> str | None:
        if value is not None and TIME_PATTERN.fullmatch(value) is None:
            raise PydanticCustomError("time_format", "time must use HH:MM")
        return value


class AddRequest(RequestModel):
    action: Literal["add"]
    date: date
    time: str | None = None
    kind: ScheduleKind
    description: str | None = None

    @field_validator("time")
    @classmethod
    def valid_time(cls, value: str | None) -> str | None:
        if value is not None and TIME_PATTERN.fullmatch(value) is None:
            raise PydanticCustomError("time_format", "time must use HH:MM")
        return value


class RescheduleRequest(RequestModel):
    action: Literal["reschedule"]
    target: str
    destination: Destination


class CancelRequest(RequestModel):
    action: Literal["cancel"]
    target: str


ScheduleRequest: TypeAlias = Annotated[
    AddRequest | RescheduleRequest | CancelRequest,
    Field(discriminator="action"),
]


class ChangeScheduleInput(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="allow")

    request: ScheduleRequest


class ResumeDecision(RequestModel):
    accept: bool


def canonical_request(request: ScheduleRequest) -> str:
    return json.dumps(
        request.model_dump(mode="json", exclude_unset=True),
        sort_keys=True,
        separators=(",", ":"),
    )


def _date_label(day: date, event_time: str | None) -> str:
    prefix = f"{day.strftime('%a, %b')} {day.day}"
    if event_time is None:
        return f"{prefix} · all day UTC"
    hour_text, minute = event_time.split(":")
    hour = int(hour_text)
    suffix = "AM" if hour < 12 else "PM"
    return f"{prefix} · {hour % 12 or 12}:{minute} {suffix} UTC"


def card_payload(request: ScheduleRequest, current: ScheduleEntry | None) -> JsonObject:
    match request:
        case AddRequest(date=day, time=event_time, kind=kind, description=description):
            return {
                "eventLabel": description or kind,
                "fromLabel": "Not scheduled",
                "toLabel": _date_label(day, event_time),
                "reason": "Add this event to your schedule.",
                "status": "pending",
            }
        case RescheduleRequest(destination=destination):
            assert current is not None
            return {
                "eventLabel": current.description or current.kind,
                "fromLabel": _date_label(current.date, current.time),
                "toLabel": _date_label(destination.date, destination.time),
                "reason": "Move this event to the proposed time.",
                "status": "pending",
            }
        case CancelRequest():
            assert current is not None
            return {
                "eventLabel": current.description or current.kind,
                "fromLabel": _date_label(current.date, current.time),
                "toLabel": "Cancelled",
                "reason": "Remove this event from your schedule.",
                "status": "pending",
            }
        case unreachable:
            assert_never(unreachable)


def event_mutation(
    request: ScheduleRequest,
) -> AddMutation | RescheduleMutation | CancelMutation:
    match request:
        case AddRequest(date=day, time=event_time, kind=kind, description=description):
            return AddMutation(
                action="add",
                date=day,
                time=event_time,
                kind=kind,
                description=description,
            )
        case RescheduleRequest(destination=destination):
            return RescheduleMutation(
                action="reschedule",
                destination_date=destination.date,
                destination_time=destination.time,
            )
        case CancelRequest():
            return CancelMutation(action="cancel")
        case unreachable:
            assert_never(unreachable)


def fold_event(
    state: dict[str, ScheduleEntry], event: ApprovalEvent
) -> dict[str, ScheduleEntry]:
    folded = dict(state)
    if event.decision != "approved":
        return folded
    match event.mutation:
        case AddMutation(date=day, time=event_time, kind=kind, description=description):
            folded[event.entry_id] = ScheduleEntry(
                entry_id=event.entry_id,
                date=day,
                time=event_time,
                kind=kind,
                description=description,
                active=True,
                created_ts=event.created_ts,
            )
        case RescheduleMutation(destination_date=day, destination_time=event_time):
            current = folded.get(event.entry_id)
            if current is not None and current.active:
                folded[event.entry_id] = current.model_copy(
                    update={"date": day, "time": event_time}
                )
        case CancelMutation():
            current = folded.get(event.entry_id)
            if current is not None and current.active:
                folded[event.entry_id] = current.model_copy(update={"active": False})
        case unreachable:
            assert_never(unreachable)
    return folded
