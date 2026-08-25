from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, NoReturn, TypeAlias, override

from pydantic import JsonValue

JSONValue: TypeAlias = JsonValue
JSONBody: TypeAlias = dict[str, JSONValue] | list[JSONValue] | None

DOCUMENT_REVIEW_QUESTION: Final = "Please review this document."
HC_RAG_MEMBER_STREAM_PERIMETER: Final = os.getenv(
    "HC_RAG_MEMBER_STREAM_PERIMETER", "v1"
)
_UUID: Final = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_THREAD: Final = re.compile(rf"^/threads/{_UUID}$")
_COPY: Final = re.compile(rf"^/threads/{_UUID}/copy$")
_STATE: Final = re.compile(rf"^/threads/{_UUID}/state$")
_HISTORY: Final = re.compile(rf"^/threads/{_UUID}/history$")
_STREAM: Final = re.compile(rf"^/threads/{_UUID}/runs/stream$")
_JOIN: Final = re.compile(rf"^/threads/{_UUID}/runs/{_UUID}/join$")
_JOIN_STREAM: Final = re.compile(rf"^/threads/{_UUID}/runs/{_UUID}/join/stream$")
_CANCEL: Final = re.compile(rf"^/threads/{_UUID}/runs/{_UUID}/cancel$")
_UPLOAD_STATUS: Final = re.compile(r"^/coach/uploads/[0-9a-fA-F-]+/status$")
_SELECT_FIELDS: Final = frozenset(
    {"thread_id", "created_at", "updated_at", "metadata", "status"}
)
_SEARCH_KEYS: Final = frozenset({"select", "limit", "offset", "sort_by", "sort_order"})
_RUN_FIXED: Final = {
    "assistant_id": "coach",
    "stream_subgraphs": False,
    "durability": "exit",
    "if_not_exists": "reject",
}
_RUN_V1_FIXED: Final = {
    "stream_mode": ["updates"],
    "stream_resumable": False,
    "multitask_strategy": "reject",
}
_RUN_V2_FIXED: Final = {
    "stream_resumable": True,
    "multitask_strategy": "enqueue",
}
_RUN_V2_STREAM_MODES: Final = frozenset({"updates", "messages", "values"})
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
        if not isinstance(field, dict) or frozenset(field) != frozenset(
            {"key", "value"}
        ):
            _deny("Invalid resume field")
        if not isinstance(field["key"], str) or not isinstance(field["value"], str):
            _deny("Invalid resume field")


def _validate_resume(command: JSONValue) -> None:
    if not isinstance(command, dict) or frozenset(command) != frozenset({"resume"}):
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
    version_fixed = (
        _RUN_V2_FIXED
        if HC_RAG_MEMBER_STREAM_PERIMETER == "v2"
        else _RUN_V1_FIXED
    )
    expected = (
        frozenset(_RUN_FIXED)
        | frozenset(version_fixed)
        | frozenset({"stream_mode"})
        | frozenset({"input"} if "input" in value else {"command"})
    )
    if frozenset(value) != expected:
        _deny("Invalid run envelope")
    for key, required in (_RUN_FIXED | version_fixed).items():
        if value.get(key) != required:
            _deny("Invalid run envelope")
    stream_mode = value.get("stream_mode")
    if HC_RAG_MEMBER_STREAM_PERIMETER == "v2":
        if (
            not isinstance(stream_mode, list)
            or not stream_mode
            or "updates" not in stream_mode
            or len(stream_mode) != len(set(stream_mode))
            or any(mode not in _RUN_V2_STREAM_MODES for mode in stream_mode)
        ):
            _deny("Invalid run envelope")
    elif stream_mode != _RUN_V1_FIXED["stream_mode"]:
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
    if (
        HC_RAG_MEMBER_STREAM_PERIMETER == "v2"
        and method == "GET"
        and (_HISTORY.fullmatch(path) or _JOIN.fullmatch(path) or _JOIN_STREAM.fullmatch(path))
        and not query
        and body is None
    ):
        return
    if (
        HC_RAG_MEMBER_STREAM_PERIMETER == "v2"
        and method == "POST"
        and _CANCEL.fullmatch(path)
        and not query
        and body is None
    ):
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
        if _PRIVATE_SENTINELS.intersection(
            key for key, item in value.items() if item is not None
        ):
            _deny("State contains a private channel", 500)
        for item in value.values():
            _sweep(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for item in value:
            _sweep(item)


def _filter_pending_document_op_id(value: JSONValue) -> JSONValue:
    if isinstance(value, dict):
        return {
            key: _filter_pending_document_op_id(item)
            for key, item in value.items()
            if key != "pending_document_op_id"
        }
    if isinstance(value, list):
        return [_filter_pending_document_op_id(item) for item in value]
    return value


def project_state(payload: JSONBody) -> dict[str, JSONValue]:
    value = _require_body_mapping(payload)
    projected: dict[str, JSONValue] = {
        "values": _filter_pending_document_op_id(value.get("values", {})),
        "interrupts": value.get("interrupts", []),
    }
    _sweep(projected)
    return projected


__all__ = [
    "DOCUMENT_REVIEW_QUESTION",
    "HC_RAG_MEMBER_STREAM_PERIMETER",
    "PerimeterDenied",
    "project_state",
    "validate_member_request",
]
