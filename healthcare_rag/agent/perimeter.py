from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, NoReturn, TypeAlias, override

from pydantic import JsonValue

JSONValue: TypeAlias = JsonValue
JSONBody: TypeAlias = dict[str, JSONValue] | list[JSONValue] | None

DOCUMENT_REVIEW_QUESTION: Final = "Please review this document."
_UUID: Final = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_THREAD: Final = re.compile(rf"^/threads/{_UUID}$")
_COPY: Final = re.compile(rf"^/threads/{_UUID}/copy$")
_STATE: Final = re.compile(rf"^/threads/{_UUID}/state$")
_STREAM: Final = re.compile(rf"^/threads/{_UUID}/runs/stream$")
_UPLOAD_STATUS: Final = re.compile(r"^/coach/uploads/[0-9a-fA-F-]+/status$")
_SELECT_FIELDS: Final = frozenset(
    {"thread_id", "created_at", "updated_at", "metadata", "status"}
)
_SEARCH_KEYS: Final = frozenset({"select", "limit", "offset", "sort_by", "sort_order"})
_RUN_FIXED: Final = {
    "assistant_id": "coach",
    "stream_mode": ["updates"],
    "stream_subgraphs": False,
    "stream_resumable": False,
    "durability": "exit",
    "if_not_exists": "reject",
    "multitask_strategy": "reject",
}
_PRIVATE_SENTINELS: Final = frozenset(
    {"question", "attachment_id", "cron_wake", "pending_document_op_id"}
)


@dataclass(frozen=True, slots=True)
class PerimeterDenied(Exception):
    reason: str
    status_code: int = 403

    @override
    def __str__(self) -> str:
        return self.reason


def _deny(reason: str, status_code: int = 403) -> NoReturn:
    raise PerimeterDenied(reason=reason, status_code=status_code)


def _require_body_mapping(body: JSONBody) -> dict[str, JSONValue]:
    if not isinstance(body, dict):
        _deny("JSON object required", 400)
    return body


def _validate_search(body: JSONBody) -> None:
    value = _require_body_mapping(body)
    if frozenset(value) - _SEARCH_KEYS or "select" not in value:
        _deny("Invalid thread search body")
    select = value["select"]
    if (
        not isinstance(select, list)
        or not select
        or any(
            not isinstance(field, str) or field not in _SELECT_FIELDS
            for field in select
        )
    ):
        _deny("Invalid thread search projection")
    limit = value.get("limit", 10)
    offset = value.get("offset", 0)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        _deny("Invalid thread search limit")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        _deny("Invalid thread search offset")
    sort_by = value.get("sort_by")
    sort_order = value.get("sort_order")
    if sort_by is not None and sort_by not in {"thread_id", "created_at", "updated_at"}:
        _deny("Invalid thread search sort")
    if sort_order is not None and sort_order not in {"asc", "desc"}:
        _deny("Invalid thread search order")


def _validate_fields(fields: JSONValue) -> None:
    if not isinstance(fields, list):
        _deny("Resume fields must be a list")
    for field in fields:
        if not isinstance(field, dict) or frozenset(field) != {"key", "value"}:
            _deny("Invalid resume field")
        if not isinstance(field["key"], str) or not isinstance(field["value"], str):
            _deny("Invalid resume field")


def _validate_resume(command: JSONValue) -> None:
    if not isinstance(command, dict) or frozenset(command) != {"resume"}:
        _deny("Invalid resume command")
    resume = command["resume"]
    if not isinstance(resume, dict) or set(resume) - {"accept", "fields"}:
        _deny("Invalid resume payload")
    if "accept" not in resume or not isinstance(resume["accept"], bool):
        _deny("Resume accept is required")
    fields = resume.get("fields")
    if fields is not None:
        _validate_fields(fields)


def _validate_run(body: JSONBody) -> None:
    value = _require_body_mapping(body)
    expected = frozenset(_RUN_FIXED) | ({"input"} if "input" in value else {"command"})
    if frozenset(value) != expected:
        _deny("Invalid run envelope")
    for key, required in _RUN_FIXED.items():
        if value.get(key) != required:
            _deny("Invalid run envelope")
    if "command" in value:
        _validate_resume(value["command"])
        return
    run_input = value["input"]
    if not isinstance(run_input, dict) or frozenset(run_input) - {
        "question",
        "attachment_id",
    }:
        _deny("Invalid run input")
    question = run_input.get("question")
    if not isinstance(question, str) or not question:
        _deny("Question is required")
    attachment = run_input.get("attachment_id")
    if attachment is not None:
        if not isinstance(attachment, str) or not attachment:
            _deny("Invalid attachment id")
        if question != DOCUMENT_REVIEW_QUESTION:
            _deny("Attachments require the document-review question")


def validate_member_request(method: str, path: str, query: str, body: JSONBody) -> None:
    if "%" in path or "//" in path or (path != "/" and path.endswith("/")):
        _deny("Non-canonical path")
    if method == "GET" and path == "/ok" and not query:
        return
    if method == "POST" and path == "/threads" and not query and body == {}:
        return
    if method == "POST" and path == "/threads/search" and not query:
        _validate_search(body)
        return
    if (
        method in {"GET", "DELETE"}
        and _THREAD.fullmatch(path)
        and not query
        and body is None
    ):
        return
    if method == "POST" and _COPY.fullmatch(path) and not query and body is None:
        return
    if method == "GET" and _STATE.fullmatch(path) and not query and body is None:
        return
    if method == "POST" and _STREAM.fullmatch(path) and not query:
        _validate_run(body)
        return
    if method == "POST" and path in {"/coach/uploads", "/coach/feedback"} and not query:
        return
    if (
        method == "GET"
        and _UPLOAD_STATUS.fullmatch(path)
        and not query
        and body is None
    ):
        return
    _deny("Route is not available")


def _sweep(value: JSONValue) -> None:
    if isinstance(value, Mapping):
        if _PRIVATE_SENTINELS.intersection(value):
            _deny("State contains a private channel", 500)
        for item in value.values():
            _sweep(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for item in value:
            _sweep(item)


def project_state(payload: JSONBody) -> dict[str, JSONValue]:
    value = _require_body_mapping(payload)
    projected: dict[str, JSONValue] = {
        "values": value.get("values", {}),
        "interrupts": value.get("interrupts", []),
    }
    _sweep(projected)
    return projected


__all__ = [
    "DOCUMENT_REVIEW_QUESTION",
    "PerimeterDenied",
    "project_state",
    "validate_member_request",
]
