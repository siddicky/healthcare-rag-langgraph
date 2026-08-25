from __future__ import annotations

import json
import time
from collections import OrderedDict
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Final, NamedTuple

import anyio
from starlette.requests import Request

from server.run_engine import JSONValue, RunEngine

SUPPORTED_CHANNELS: Final = frozenset(
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
SEQ_CACHE_LIMIT: Final = 512


@dataclass(slots=True)
class ProtocolThreadState:
    next_seq: int = 1
    seq_by_event: OrderedDict[str, int] = field(default_factory=OrderedDict)
    lock: anyio.Lock = field(default_factory=anyio.Lock)


@dataclass(slots=True)
class MessageAssembly:
    message_id: str | None = None
    text: str = ""
    node: str | None = None


@dataclass(frozen=True, slots=True)
class StreamFilter:
    channels: frozenset[str]
    namespaces: tuple[tuple[str, ...], ...] | None
    depth: int | None
    since: int | None


class NormalizedEvent(NamedTuple):
    method: str
    data: JSONValue
    node: str | None = None


@dataclass(frozen=True, slots=True)
class ProtocolFailure(Exception):
    code: str
    message: str
    status_code: int = 400


def protocol_state(request: Request, thread_id: str) -> ProtocolThreadState:
    registries: dict[str, ProtocolThreadState] | None = getattr(
        request.app.state, "protocol_streams", None
    )
    if registries is None:
        registries = {}
        request.app.state.protocol_streams = registries
    state = registries.get(thread_id)
    if state is None:
        state = ProtocolThreadState()
        registries[thread_id] = state
    return state


def parse_stream_filter(body: JSONValue) -> StreamFilter:
    if not isinstance(body, dict):
        raise ProtocolFailure("invalid_argument", "Invalid JSON body")
    channels = body.get("channels")
    if not isinstance(channels, list) or not channels:
        raise ProtocolFailure(
            "invalid_argument", "channels is required and must be a non-empty array"
        )
    bad = [
        value
        for value in channels
        if not isinstance(value, str)
        or not (value in SUPPORTED_CHANNELS or value.startswith("custom:"))
    ]
    if bad:
        raise ProtocolFailure(
            "invalid_argument", f"channels contains unsupported entries: {bad[:5]}"
        )
    valid_channels = frozenset(value for value in channels if isinstance(value, str))
    namespaces_value = body.get("namespaces")
    namespaces = None
    if isinstance(namespaces_value, list):
        valid_namespaces = tuple(
            tuple(segment for segment in namespace if isinstance(segment, str))
            for namespace in namespaces_value
            if isinstance(namespace, list)
            and all(isinstance(segment, str) for segment in namespace)
        )
        namespaces = valid_namespaces or None
    depth_value = body.get("depth")
    depth = (
        depth_value
        if isinstance(depth_value, int)
        and not isinstance(depth_value, bool)
        and depth_value >= 0
        else None
    )
    since_value = body.get("since")
    since = (
        since_value
        if isinstance(since_value, int)
        and not isinstance(since_value, bool)
        and since_value >= 0
        else None
    )
    return StreamFilter(valid_channels, namespaces, depth, since)


def _channel(method: str, data: JSONValue) -> str:
    if method == "input.requested":
        return "input"
    if method == "custom" and isinstance(data, dict):
        name = data.get("name")
        if isinstance(name, str):
            return f"custom:{name}"
    return method


def _matches(filter_: StreamFilter, event: dict[str, JSONValue]) -> bool:
    params = event["params"]
    assert isinstance(params, dict)
    channel = _channel(str(event["method"]), params["data"])
    if channel not in filter_.channels and not (
        channel.startswith("custom:") and "custom" in filter_.channels
    ):
        return False
    if not filter_.namespaces:
        return True
    namespace = params["namespace"]
    assert isinstance(namespace, list)
    return any(
        namespace[: len(prefix)] == list(prefix)
        and (filter_.depth is None or len(namespace) - len(prefix) <= filter_.depth)
        for prefix in filter_.namespaces
    )


def _interrupt_events(value: JSONValue) -> list[NormalizedEvent]:
    entries = value if isinstance(value, list) else []
    return [
        NormalizedEvent(
            "input.requested",
            {"interrupt_id": entry["id"], "payload": entry.get("value")},
        )
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    ]


def _message_list(update: JSONValue) -> list[dict[str, JSONValue]]:
    if not isinstance(update, dict):
        return []
    messages = update.get("messages")
    if isinstance(messages, dict):
        return [messages]
    if isinstance(messages, list):
        return [message for message in messages if isinstance(message, dict)]
    return []


def _tool_events(update: JSONValue, node: str) -> list[NormalizedEvent]:
    events: list[NormalizedEvent] = []
    for message in _message_list(update):
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                    continue
                events.append(
                    NormalizedEvent(
                        "tools",
                        {
                            "event": "tool-started",
                            "tool_call_id": call["id"],
                            "tool_name": call.get("name", ""),
                            "input": call.get("args", {}),
                        },
                        node,
                    )
                )
        tool_call_id = message.get("tool_call_id")
        if isinstance(tool_call_id, str):
            events.append(
                NormalizedEvent(
                    "tools",
                    {
                        "event": "tool-finished",
                        "tool_call_id": tool_call_id,
                        "output": message.get("content"),
                    },
                    node,
                )
            )
    return events


def _finish_message(assembly: MessageAssembly) -> list[NormalizedEvent]:
    if assembly.message_id is None:
        return []
    events = [
        NormalizedEvent(
            "messages",
            {
                "event": "content-block-finish",
                "index": 0,
                "content": {"type": "text", "text": assembly.text},
            },
            assembly.node,
        ),
        NormalizedEvent(
            "messages",
            {"event": "message-finish", "id": assembly.message_id},
            assembly.node,
        ),
    ]
    assembly.message_id = None
    assembly.text = ""
    assembly.node = None
    return events


def _message_events(
    data: JSONValue, assembly: MessageAssembly
) -> list[NormalizedEvent]:
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return []
    message = data[0]
    metadata = data[1] if len(data) > 1 and isinstance(data[1], dict) else {}
    message_id = str(message.get("id") or f"message-{id(message)}")
    node_value = metadata.get("langgraph_node")
    node = node_value if isinstance(node_value, str) else None
    events: list[NormalizedEvent] = []
    if assembly.message_id != message_id:
        events.extend(_finish_message(assembly))
        assembly.message_id = message_id
        assembly.node = node
        events.extend(
            [
                NormalizedEvent(
                    "messages",
                    {"event": "message-start", "role": "ai", "id": message_id},
                    node,
                ),
                NormalizedEvent(
                    "messages",
                    {
                        "event": "content-block-start",
                        "index": 0,
                        "content": {"type": "text", "text": ""},
                    },
                    node,
                ),
            ]
        )
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    if text:
        assembly.text += text
        events.append(
            NormalizedEvent(
                "messages",
                {
                    "event": "content-block-delta",
                    "index": 0,
                    "delta": {"type": "text-delta", "text": text},
                },
                node,
            )
        )
    return events


def normalize_event(
    mode: str, data: JSONValue, assembly: MessageAssembly
) -> list[NormalizedEvent]:
    match mode:
        case "updates":
            if not isinstance(data, dict):
                return []
            events: list[NormalizedEvent] = []
            for node, update in data.items():
                if node == "__interrupt__":
                    events.extend(_interrupt_events(update))
                    continue
                events.extend(_tool_events(update, node))
                events.append(
                    NormalizedEvent("updates", {"node": node, "values": update}, node)
                )
            return events
        case "values":
            if not isinstance(data, dict):
                return []
            events = _interrupt_events(data.get("__interrupt__"))
            values = {
                key: value for key, value in data.items() if key != "__interrupt__"
            }
            if values:
                events.append(NormalizedEvent("values", values))
            return events
        case "messages":
            return _message_events(data, assembly)
        case "custom":
            return [NormalizedEvent("custom", {"payload": data})]
        case "tasks" | "checkpoints":
            return [NormalizedEvent(mode, data)]
        case _:
            return []


async def _assign_event(
    state: ProtocolThreadState, event_id: str, normalized: NormalizedEvent
) -> dict[str, JSONValue]:
    async with state.lock:
        seq = state.seq_by_event.get(event_id)
        if seq is None:
            seq = state.next_seq
            state.next_seq += 1
            state.seq_by_event[event_id] = seq
            if len(state.seq_by_event) > SEQ_CACHE_LIMIT:
                _ = state.seq_by_event.popitem(last=False)
    params: dict[str, JSONValue] = {
        "namespace": [],
        "timestamp": int(time.time() * 1000),
        "data": normalized.data,
    }
    if normalized.node is not None:
        params["node"] = normalized.node
    return {
        "type": "event",
        "event_id": event_id,
        "seq": seq,
        "method": normalized.method,
        "params": params,
    }


def _frame(event: dict[str, JSONValue]) -> bytes:
    compact = json.dumps(event, separators=(",", ":"), default=str)
    return f"event: {event['method']}\nid: {event['seq']}\ndata: {compact}\n\n".encode()


def _seq(event: dict[str, JSONValue]) -> int:
    seq = event["seq"]
    assert isinstance(seq, int) and not isinstance(seq, bool)
    return seq


async def stream_runtime(
    engine: RunEngine,
    run_id: str,
    filter_: StreamFilter,
    state: ProtocolThreadState,
) -> AsyncGenerator[bytes, None]:
    runtime = engine.runtime[run_id]
    assembly = MessageAssembly()
    started_event = await _assign_event(
        state,
        f"{run_id}:lc:start",
        NormalizedEvent(
            "lifecycle",
            {"event": "started", "graph_name": runtime.request.assistant_id},
        ),
    )
    if (filter_.since is None or _seq(started_event) > filter_.since) and _matches(
        filter_, started_event
    ):
        yield _frame(started_event)
    next_event = runtime.event_count - len(runtime.events)
    while True:
        buffer_start = runtime.event_count - len(runtime.events)
        next_event = max(next_event, buffer_start)
        if next_event < runtime.event_count:
            raw_index = next_event
            mode, data = runtime.events[next_event - buffer_start]
            next_event += 1
            normalized_events = normalize_event(mode, data, assembly)
            for index, normalized in enumerate(normalized_events):
                suffix = "" if len(normalized_events) == 1 else f":{index}"
                event = await _assign_event(
                    state, f"{run_id}:{raw_index}{suffix}", normalized
                )
                if filter_.since is not None and _seq(event) <= filter_.since:
                    continue
                if _matches(filter_, event):
                    yield _frame(event)
            continue
        if runtime.done.is_set():
            break
        await runtime.changed.wait()
    for index, normalized in enumerate(_finish_message(assembly)):
        event = await _assign_event(state, f"{run_id}:msg:end:{index}", normalized)
        if filter_.since is not None and _seq(event) <= filter_.since:
            continue
        if _matches(filter_, event):
            yield _frame(event)
    record = await engine.storage.runs.get(run_id)
    status = record.get("status") if record is not None else "error"
    terminal = {
        "success": "completed",
        "interrupted": "interrupted",
        "error": "failed",
        "timeout": "failed",
    }.get(str(status), "failed")
    end = await _assign_event(
        state,
        f"{run_id}:lc:end",
        NormalizedEvent(
            "lifecycle",
            {"event": terminal, "graph_name": runtime.request.assistant_id},
        ),
    )
    if (filter_.since is None or _seq(end) > filter_.since) and _matches(filter_, end):
        yield _frame(end)
