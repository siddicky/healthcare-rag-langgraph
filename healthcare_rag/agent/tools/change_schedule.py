from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Literal, assert_never, override

from langchain.tools import ToolRuntime, tool
from langgraph.store.base import BaseStore
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from healthcare_rag.agent.state import CoachState
from healthcare_rag.agent.store_data import (
    ApprovalEvent,
    OpRecord,
    ScheduleEntry,
    append_event,
    get_event_for_op,
    get_op,
    list_schedule,
    make_envelope,
    put_op,
    put_op_if_absent,
    resolve_target,
    schedule_state,
)

from ._change_schedule_contract import (
    AddRequest,
    CancelRequest,
    ChangeScheduleInput,
    Destination,
    JsonObject,
    RescheduleRequest,
    ResumeDecision,
    ScheduleRequest,
    canonical_request,
    card_payload,
    event_mutation,
    fold_event,
)

_MALFORMED_RESUME = "Schedule change remains pending: the decision was malformed."


class AuthPrincipal(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    identity: str


@dataclass(frozen=True, slots=True)
class ScheduleRuntimeError(Exception):
    code: str

    @override
    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class TargetResolutionError(Exception):
    candidates: str

    @override
    def __str__(self) -> str:
        return f"Schedule target was not unique. Candidates: {self.candidates}."


def _runtime_ids(
    runtime: ToolRuntime[None, CoachState],
) -> tuple[str, str, str, str, BaseStore]:
    configurable = runtime.config.get("configurable", {})
    principal = configurable.get("langgraph_auth_user")
    try:
        user_id = AuthPrincipal.model_validate(principal).identity
    except ValidationError as error:
        raise ScheduleRuntimeError("SCHEDULE_USER_ID_REQUIRED") from error
    thread_id = configurable.get("thread_id")
    human_msg_id = configurable.get("coach_human_msg_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise ScheduleRuntimeError("SCHEDULE_THREAD_ID_REQUIRED")
    if not isinstance(human_msg_id, str) or not human_msg_id:
        raise ScheduleRuntimeError("SCHEDULE_HUMAN_MESSAGE_ID_REQUIRED")
    if runtime.tool_call_id is None or not runtime.tool_call_id:
        raise ScheduleRuntimeError("SCHEDULE_TOOL_CALL_ID_REQUIRED")
    if runtime.store is None:
        raise ScheduleRuntimeError("SCHEDULE_STORE_REQUIRED")
    return user_id, thread_id, human_msg_id, runtime.tool_call_id, runtime.store


async def _pending_op(
    request: ScheduleRequest, runtime: ToolRuntime[None, CoachState]
) -> tuple[OpRecord, str, str, str, BaseStore]:
    user_id, thread_id, human_msg_id, tool_call_id, store = _runtime_ids(runtime)
    canonical = canonical_request(request)
    op_id = hashlib.sha256(
        (canonical + user_id + thread_id + tool_call_id).encode()
    ).hexdigest()
    existing = await get_op(store, user_id, op_id)
    if existing is not None:
        return existing, user_id, thread_id, human_msg_id, store
    current: ScheduleEntry | None = None
    match request:
        case AddRequest():
            entry_id = hashlib.sha256((user_id + canonical).encode()).hexdigest()
        case RescheduleRequest(target=target) | CancelRequest(target=target):
            current = await resolve_target(store, user_id, target)
            if current is None:
                entries = await list_schedule(store, user_id)
                candidates = [
                    entry
                    for entry in entries
                    if target.casefold() in entry.kind.casefold()
                    or (
                        entry.description is not None
                        and target.casefold() in entry.description.casefold()
                    )
                ]
                named = candidates or entries
                labels = (
                    ", ".join(
                        f"{entry.entry_id} ({entry.description or entry.kind})"
                        for entry in named
                    )
                    or "none"
                )
                raise TargetResolutionError(labels)
            entry_id = current.entry_id
        case unreachable:
            assert_never(unreachable)
    op = OpRecord(
        op_id=op_id,
        status="pending",
        result=None,
        created_ts=datetime.now(UTC),
        resolved_entry_id=entry_id,
        frozen_request=request.model_dump(mode="json"),
        interrupt_payload=card_payload(request, current),
    )
    _ = await put_op_if_absent(store, user_id, op)
    stored = await get_op(store, user_id, op_id)
    if stored is None:
        raise ScheduleRuntimeError("SCHEDULE_PENDING_OP_MISSING")
    return stored, user_id, thread_id, human_msg_id, store


@tool(args_schema=ChangeScheduleInput)
async def change_schedule(
    request: ScheduleRequest, runtime: ToolRuntime[None, CoachState]
) -> str:
    """Propose one schedule change and wait for the member's decision."""
    try:
        op, user_id, thread_id, human_msg_id, store = await _pending_op(
            request, runtime
        )
    except TargetResolutionError as error:
        return str(error)
    try:
        decision = ResumeDecision.model_validate(interrupt(op.interrupt_payload))
    except ValidationError:
        return _MALFORMED_RESUME
    event = await get_event_for_op(store, user_id, op.op_id)
    if event is None:
        before = await schedule_state(store, user_id)
        event = await append_event(
            store,
            user_id,
            ApprovalEvent(
                op_id=op.op_id,
                event_key="",
                decision="approved" if decision.accept else "declined",
                decision_ts=datetime.now(UTC),
                entry_id=op.resolved_entry_id or "",
                mutation=event_mutation(
                    type(request).model_validate(op.frozen_request)
                ),
                created_ts=datetime.now(UTC),
            ),
        )
        after = fold_event(before, event)
    else:
        after = await schedule_state(store, user_id)
    status: Literal["confirmed", "declined"] = (
        "confirmed" if event.decision == "approved" else "declined"
    )
    card: JsonObject = {**op.interrupt_payload, "status": status}
    snapshot: JsonValue = [
        entry.model_dump(mode="json")
        for entry in sorted(
            after.values(), key=lambda item: (item.date, item.time or "", item.entry_id)
        )
    ]
    data: JsonObject = {"card": card, "schedule": snapshot}
    result = make_envelope(
        thread_id,
        human_msg_id,
        f"calendar-change:{op.op_id}",
        data,
        "Schedule change confirmed."
        if status == "confirmed"
        else "Schedule change declined.",
    )
    terminal_status: Literal["applied", "declined", "declined-stale"] = (
        "applied" if event.decision == "approved" else event.decision
    )
    await put_op(
        store,
        user_id,
        op.model_copy(update={"status": terminal_status, "result": data}),
    )
    return result


__all__ = [
    "AddRequest",
    "CancelRequest",
    "ChangeScheduleInput",
    "Destination",
    "RescheduleRequest",
    "change_schedule",
]
