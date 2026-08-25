from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, NoReturn, TypeAlias, override
from urllib.parse import parse_qsl

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
_STREAM_EVENTS: Final = re.compile(rf"^/threads/{_UUID}/stream/events$")
_COMMANDS: Final = re.compile(rf"^/threads/{_UUID}/commands$")
_UPLOAD_STATUS: Final = re.compile(r"^/coach/uploads/[0-9a-fA-F-]+/status$")
_ASSISTANT_SUBRESOURCE: Final = re.compile(
    r"^/assistants/[A-Za-z0-9_-]+/(schemas|graph)$"
)
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
_THREADSTREAM_CHANNELS: Final = frozenset(
    {
        "values",
        "updates",
        "messages",
        "tools",
        "lifecycle",
        "input",
        "custom",
        "tasks",
        "checkpoints",
    }
)
_PRIVATE_SENTINELS: Final = frozenset(
    {"question", "attachment_id", "cron_wake", "pending_document_op_id"}
)
_ASSISTANT_SEARCH_KEYS: Final = frozenset({"graph_id", "limit", "offset"})
# CopilotKit runtime v1.69.1 run envelope (task-2 captured contract): the
# AG-UI adapter posts exactly these top-level keys, with `input` for a new
# turn and `command` for an interrupt resume.
_COPILOTKIT_RUN_FIXED_KEYS: Final = frozenset(
    {"assistant_id", "stream_mode", "stream_subgraphs"}
)
_COPILOTKIT_INPUT_KEYS: Final = frozenset(
    {"question", "attachment_id", "messages", "tools", "copilotkit"}
)
_COPILOTKIT_STREAM_MODES: Final = frozenset(
    {"updates", "messages", "values", "custom", "tasks"}
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


def _validate_fields(fields: JSONValue, status_code: int = 403) -> None:
    if not isinstance(fields, list):
        _deny("Resume fields must be a list", status_code)
    for field in fields:
        if not isinstance(field, dict) or frozenset(field) != frozenset(
            {"key", "value"}
        ):
            _deny("Invalid resume field", status_code)
        if not isinstance(field["key"], str) or not isinstance(field["value"], str):
            _deny("Invalid resume field", status_code)


def _validate_resume(command: JSONValue, status_code: int = 403) -> None:
    if not isinstance(command, dict) or frozenset(command) != frozenset({"resume"}):
        _deny("Invalid resume command", status_code)
    resume = command["resume"]
    if not isinstance(resume, dict) or set(resume) - {"accept", "fields"}:
        _deny("Invalid resume payload", status_code)
    if "accept" not in resume or not isinstance(resume["accept"], bool):
        _deny("Resume accept is required", status_code)
    fields = resume.get("fields")
    if fields is not None:
        _validate_fields(fields, status_code)


def _validate_checkpoint_config(config: JSONValue, path_thread_id: str) -> None:
    if not isinstance(config, dict) or frozenset(config) - {"configurable"}:
        _deny("Invalid checkpoint config", 400)
    configurable = config.get("configurable")
    if configurable is None:
        return
    if not isinstance(configurable, dict) or frozenset(configurable) - {
        "thread_id",
        "checkpoint_id",
    }:
        _deny("Invalid checkpoint config", 400)
    bound_thread = configurable.get("thread_id")
    if bound_thread is not None and (
        not isinstance(bound_thread, str)
        or bound_thread.casefold() != path_thread_id.casefold()
    ):
        _deny("Config thread_id must match the thread being run", 400)
    if "checkpoint_id" in configurable and not isinstance(
        configurable["checkpoint_id"], str
    ):
        _deny("Invalid checkpoint config", 400)


def _validate_member_input(
    run_input: JSONValue,
    *,
    allow_attachment: bool,
    require_attachment_uuid: bool,
    status_code: int = 403,
) -> None:
    allowed = {"question", "attachment_id"} if allow_attachment else {"question"}
    if not isinstance(run_input, dict) or frozenset(run_input) - allowed:
        _deny("Invalid run input", status_code)
    question = run_input.get("question")
    if not isinstance(question, str) or not question:
        _deny("Question is required", status_code)
    attachment = run_input.get("attachment_id")
    if attachment is None:
        return
    if (
        not isinstance(attachment, str)
        or not attachment
        or (require_attachment_uuid and re.fullmatch(_UUID, attachment) is None)
    ):
        _deny("Invalid attachment id", status_code)
    if question != DOCUMENT_REVIEW_QUESTION:
        _deny("Attachments require the document-review question", status_code)


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
    _validate_member_input(
        value["input"],
        allow_attachment=True,
        require_attachment_uuid=HC_RAG_MEMBER_STREAM_PERIMETER == "v2",
    )


def _validate_assistant_search(body: JSONBody) -> None:
    value = _require_body_mapping(body)
    if frozenset(value) - _ASSISTANT_SEARCH_KEYS or "graph_id" not in value:
        _deny("Invalid assistant search body")
    if value["graph_id"] != "coach":
        _deny("Invalid assistant search graph")
    limit = value.get("limit", 10)
    offset = value.get("offset", 0)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        _deny("Invalid assistant search limit")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        _deny("Invalid assistant search offset")


def _validate_copilotkit_run(body: JSONBody) -> None:
    value = _require_body_mapping(body)
    expected = _COPILOTKIT_RUN_FIXED_KEYS | (
        {"input"} if "input" in value else {"command"}
    )
    if frozenset(value) != expected:
        _deny("Invalid run envelope")
    assistant_id = value["assistant_id"]
    if assistant_id != "coach" and re.fullmatch(_UUID, str(assistant_id)) is None:
        # The runtime sends either the graph id or the assistant UUID it
        # resolved through /assistants/search (task-2 capture).
        _deny("Invalid run envelope")
    stream_mode = value["stream_mode"]
    if (
        not isinstance(stream_mode, list)
        or not stream_mode
        or len(stream_mode) != len(set(stream_mode))
        or any(mode not in _COPILOTKIT_STREAM_MODES for mode in stream_mode)
    ):
        _deny("Invalid run envelope")
    if not isinstance(value["stream_subgraphs"], bool):
        _deny("Invalid run envelope")
    if "command" in value:
        _validate_resume(value["command"])
        return
    run_input = value["input"]
    if not isinstance(run_input, dict) or "copilotkit" not in run_input:
        _deny("Invalid run input")
    if frozenset(run_input) - _COPILOTKIT_INPUT_KEYS:
        _deny("Invalid run input")
    _validate_member_input(
        {key: run_input[key] for key in ("question", "attachment_id") if key in run_input},
        allow_attachment=True,
        require_attachment_uuid=HC_RAG_MEMBER_STREAM_PERIMETER == "v2",
    )
    messages = run_input.get("messages")
    if messages is not None and (
        not isinstance(messages, list)
        or any(
            not isinstance(message, dict) or not message
            for message in messages
        )
    ):
        _deny("Invalid run input")
    tools = run_input.get("tools")
    if tools is not None and not isinstance(tools, list):
        _deny("Invalid run input")
    copilotkit = run_input["copilotkit"]
    if not isinstance(copilotkit, dict) or frozenset(copilotkit) - {"actions", "context"}:
        _deny("Invalid run input")
    for key in ("actions", "context"):
        item = copilotkit.get(key)
        if item is not None and not isinstance(item, list):
            _deny("Invalid run input")


def _validate_stream_events(body: JSONBody) -> None:
    value = _require_body_mapping(body)
    if frozenset(value) - {"channels", "namespaces", "depth", "since"}:
        _deny("Invalid stream subscription", 400)
    channels = value.get("channels")
    if (
        not isinstance(channels, list)
        or not channels
        or any(
            not isinstance(channel, str)
            or (
                channel not in _THREADSTREAM_CHANNELS
                and not (channel.startswith("custom:") and len(channel) > len("custom:"))
            )
            for channel in channels
        )
    ):
        _deny("Invalid stream channels", 400)
    namespaces = value.get("namespaces")
    if namespaces is not None and (
        not isinstance(namespaces, list)
        or any(
            not isinstance(namespace, list)
            or any(not isinstance(part, str) for part in namespace)
            for namespace in namespaces
        )
    ):
        _deny("Invalid stream namespaces", 400)
    for key in ("depth", "since"):
        item = value.get(key)
        if item is not None and (
            not isinstance(item, int) or isinstance(item, bool) or item < 0
        ):
            _deny(f"Invalid stream {key}", 400)


def _validate_run_start(
    params: dict[str, JSONValue], path_thread_id: str
) -> None:
    if frozenset(params) - {"assistant_id", "input", "config", "multitaskStrategy"}:
        _deny("Invalid run.start params", 400)
    if "assistant_id" not in params or "input" not in params:
        _deny("Invalid run.start params", 400)
    if params["assistant_id"] != "coach":
        _deny("Invalid run.start assistant", 400)
    _validate_member_input(
        params["input"],
        allow_attachment=True,
        require_attachment_uuid=True,
        status_code=400,
    )
    config = params.get("config")
    if config is not None:
        _validate_checkpoint_config(config, path_thread_id)
    strategy = params.get("multitaskStrategy")
    if strategy is not None and strategy not in {"reject", "enqueue", "interrupt"}:
        _deny("Invalid run.start multitask strategy", 400)


def _validate_response_item(item: JSONValue) -> None:
    if not isinstance(item, dict) or frozenset(item) - frozenset(
        {"interrupt_id", "response", "namespace"}
    ):
        _deny("Invalid input.respond response", 400)
    namespace = item.get("namespace")
    if namespace is not None and namespace != []:
        _deny("Members may only respond on the root namespace", 400)
    if not isinstance(item["interrupt_id"], str):
        _deny("Invalid input.respond interrupt", 400)
    _validate_resume({"resume": item["response"]}, 400)


def _validate_input_respond(
    params: dict[str, JSONValue], path_thread_id: str
) -> None:
    if "responses" in params:
        if frozenset(params) - {"responses", "config"}:
            _deny("Invalid input.respond params", 400)
        responses = params["responses"]
        if not isinstance(responses, list) or not responses:
            _deny("Invalid input.respond responses", 400)
        for response in responses:
            _validate_response_item(response)
        config = params.get("config")
        if config is not None:
            _validate_checkpoint_config(config, path_thread_id)
        return
    _validate_response_item(params)


def _validate_command_params(
    method: str, params: dict[str, JSONValue], path_thread_id: str
) -> None:
    match method:
        case "run.start":
            _validate_run_start(params, path_thread_id)
        case "input.respond":
            _validate_input_respond(params, path_thread_id)
        case "state.get":
            if frozenset(params) - {"keys"}:
                _deny("Invalid state.get params", 400)
            keys = params.get("keys")
            if keys is not None and (
                not isinstance(keys, list)
                or any(not isinstance(key, str) for key in keys)
            ):
                _deny("Invalid state.get keys", 400)
        case "state.listCheckpoints":
            if frozenset(params) - {"limit", "before"}:
                _deny("Invalid state.listCheckpoints params", 400)
            limit = params.get("limit")
            before = params.get("before")
            if limit is not None and (
                not isinstance(limit, int) or isinstance(limit, bool)
            ):
                _deny("Invalid state.listCheckpoints limit", 400)
            if before is not None and not isinstance(before, str):
                _deny("Invalid state.listCheckpoints before", 400)
        case "state.fork":
            if frozenset(params) - {"checkpoint_id", "input"} or "checkpoint_id" not in params:
                _deny("Invalid state.fork params", 400)
            if not isinstance(params["checkpoint_id"], str):
                _deny("Invalid state.fork checkpoint", 400)
            fork_input = params.get("input")
            if fork_input is not None:
                _validate_member_input(
                    fork_input,
                    allow_attachment=False,
                    require_attachment_uuid=False,
                    status_code=400,
                )
        case _:
            _deny("Command is not available", 400)


def _validate_command(body: JSONBody, path_thread_id: str) -> None:
    value = _require_body_mapping(body)
    if frozenset(value) != frozenset({"id", "method", "params"}):
        _deny("Invalid command envelope", 400)
    command_id = value["id"]
    method = value["method"]
    params = value["params"]
    if not isinstance(command_id, int) or isinstance(command_id, bool):
        _deny("Invalid command id", 400)
    if not isinstance(method, str) or not isinstance(params, dict):
        _deny("Invalid command envelope", 400)
    _validate_command_params(method, params, path_thread_id)


def _validate_cancel_query(query: str) -> None:
    if not query:
        return
    try:
        entries = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        _deny("Invalid cancel query", 400)
    if len(entries) != len({key for key, _value in entries}):
        _deny("Invalid cancel query", 400)
    values = dict(entries)
    if frozenset(values) - {"wait", "action", "timeout"}:
        _deny("Invalid cancel query", 400)
    if "wait" in values and values["wait"] not in {"true", "false"}:
        _deny("Invalid cancel wait", 400)
    if "action" in values and values["action"] not in {"interrupt", "rollback"}:
        _deny("Invalid cancel action", 400)
    if "timeout" in values and re.fullmatch(r"[0-9]+", values["timeout"]) is None:
        _deny("Invalid cancel timeout", 400)


def validate_member_request(method: str, path: str, query: str, body: JSONBody) -> None:
    if "%" in path or "//" in path or (path != "/" and path.endswith("/")):
        _deny("Non-canonical path")
    if method == "GET" and path == "/ok" and not query:
        return
    if method == "POST" and path == "/threads" and not query:
        if body == {}:
            return
        if (
            isinstance(body, dict)
            and frozenset(body) == frozenset({"metadata", "thread_id"})
            and body["metadata"] == {}
            and isinstance(body["thread_id"], str)
            and re.fullmatch(_UUID, body["thread_id"]) is not None
        ):
            return
    if method == "POST" and path == "/threads/search" and not query:
        _validate_search(body)
        return
    if method == "POST" and path == "/assistants/search" and not query:
        _validate_assistant_search(body)
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
        method == "GET"
        and _ASSISTANT_SUBRESOURCE.fullmatch(path)
        and not query
        and body is None
    ):
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
        and body is None
    ):
        _validate_cancel_query(query)
        return
    if (
        HC_RAG_MEMBER_STREAM_PERIMETER == "v2"
        and method == "POST"
        and _STREAM_EVENTS.fullmatch(path)
        and not query
    ):
        _validate_stream_events(body)
        return
    if (
        HC_RAG_MEMBER_STREAM_PERIMETER == "v2"
        and method == "POST"
        and (commands_match := re.fullmatch(rf"/threads/({_UUID})/commands", path))
        and not query
    ):
        _validate_command(body, str(commands_match.group(1)))
        return
    if method == "POST" and _STREAM.fullmatch(path) and not query:
        body_keys = frozenset(body) if isinstance(body, dict) else None
        if body_keys in (
            _COPILOTKIT_RUN_FIXED_KEYS | {"input"},
            _COPILOTKIT_RUN_FIXED_KEYS | {"command"},
        ):
            _validate_copilotkit_run(body)
        else:
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
