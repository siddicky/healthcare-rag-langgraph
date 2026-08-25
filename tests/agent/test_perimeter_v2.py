from __future__ import annotations

from typing import Final

import pytest
from pytest import MonkeyPatch

import healthcare_rag.agent.perimeter as perimeter
from healthcare_rag.agent.perimeter import JSONValue

THREAD_ID: Final = "00000000-0000-0000-0000-000000000001"
RUN_ID: Final = "00000000-0000-0000-0000-000000000002"
ATTACHMENT_ID: Final = "00000000-0000-0000-0000-000000000003"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/threads/{THREAD_ID}/history"),
        ("GET", f"/threads/{THREAD_ID}/runs/{RUN_ID}/join"),
        ("GET", f"/threads/{THREAD_ID}/runs/{RUN_ID}/join/stream"),
        ("POST", f"/threads/{THREAD_ID}/runs/{RUN_ID}/cancel"),
    ],
)
@pytest.mark.parametrize(("mode", "allowed"), [("v1", False), ("v2", True)])
def test_member_stream_perimeter_gates_v2_routes(
    monkeypatch: MonkeyPatch,
    method: str,
    path: str,
    mode: str,
    allowed: bool,
) -> None:
    monkeypatch.setattr(perimeter, "HC_RAG_MEMBER_STREAM_PERIMETER", mode)
    if allowed:
        perimeter.validate_member_request(method, path, "", None)
    else:
        with pytest.raises(perimeter.PerimeterDenied) as denied:
            perimeter.validate_member_request(method, path, "", None)
        assert denied.value.status_code == 403


@pytest.mark.parametrize(
    "stream_mode",
    [["updates", "messages"], ["updates", "values"]],
)
@pytest.mark.parametrize(("mode", "allowed"), [("v1", False), ("v2", True)])
def test_member_stream_perimeter_gates_v2_run_envelope(
    monkeypatch: MonkeyPatch,
    stream_mode: list[JSONValue],
    mode: str,
    allowed: bool,
) -> None:
    body: dict[str, JSONValue] = {
        "assistant_id": "coach",
        "input": {"question": "What is Lipitor used for?"},
        "stream_mode": stream_mode,
        "stream_subgraphs": False,
        "stream_resumable": True,
        "durability": "exit",
        "if_not_exists": "reject",
        "multitask_strategy": "enqueue",
    }

    monkeypatch.setattr(perimeter, "HC_RAG_MEMBER_STREAM_PERIMETER", mode)
    if allowed:
        perimeter.validate_member_request(
            "POST", f"/threads/{THREAD_ID}/runs/stream", "", body
        )
    else:
        with pytest.raises(perimeter.PerimeterDenied) as denied:
            perimeter.validate_member_request(
                "POST", f"/threads/{THREAD_ID}/runs/stream", "", body
            )
        assert denied.value.status_code == 403


def test_member_stream_perimeter_defaults_to_v1() -> None:
    assert perimeter.HC_RAG_MEMBER_STREAM_PERIMETER == "v1"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            f"/threads/{THREAD_ID}/stream/events",
            {
                "channels": ["values", "updates", "custom:coach"],
                "namespaces": [[], ["coach"]],
                "depth": 0,
                "since": 12,
            },
        ),
        (
            f"/threads/{THREAD_ID}/commands",
            {
                "id": 1,
                "method": "run.start",
                "params": {
                    "assistant_id": "coach",
                    "input": {"question": "What is Lipitor used for?"},
                    "config": {"configurable": {"checkpoint_id": "checkpoint-1"}},
                    "multitaskStrategy": "enqueue",
                },
            },
        ),
        (
            f"/threads/{THREAD_ID}/commands",
            {
                "id": 2,
                "method": "run.start",
                "params": {
                    "assistant_id": "coach",
                    "input": {
                        "question": perimeter.DOCUMENT_REVIEW_QUESTION,
                        "attachment_id": ATTACHMENT_ID,
                    },
                },
            },
        ),
        (
            f"/threads/{THREAD_ID}/commands",
            {
                "id": 3,
                "method": "input.respond",
                "params": {
                    "interrupt_id": "interrupt-1",
                    "response": {"accept": True, "fields": []},
                },
            },
        ),
        (
            f"/threads/{THREAD_ID}/commands",
            {
                "id": 4,
                "method": "input.respond",
                "params": {
                    "responses": [
                        {
                            "interrupt_id": "interrupt-1",
                            "response": {
                                "accept": False,
                                "fields": [{"key": "reason", "value": "No"}],
                            },
                        }
                    ],
                    "config": {"configurable": {"checkpoint_id": "checkpoint-1"}},
                },
            },
        ),
        (
            f"/threads/{THREAD_ID}/commands",
            {"id": 5, "method": "state.get", "params": {"keys": ["messages"]}},
        ),
        (
            f"/threads/{THREAD_ID}/commands",
            {
                "id": 6,
                "method": "state.listCheckpoints",
                "params": {"limit": 10, "before": "checkpoint-2"},
            },
        ),
        (
            f"/threads/{THREAD_ID}/commands",
            {
                "id": 7,
                "method": "state.fork",
                "params": {
                    "checkpoint_id": "checkpoint-1",
                    "input": {"question": "Continue from here"},
                },
            },
        ),
    ],
)
def test_v2_admits_threadstream_routes(
    monkeypatch: MonkeyPatch, path: str, body: dict[str, JSONValue]
) -> None:
    monkeypatch.setattr(perimeter, "HC_RAG_MEMBER_STREAM_PERIMETER", "v2")

    perimeter.validate_member_request("POST", path, "", body)


@pytest.mark.parametrize(
    "body",
    [
        {"channels": []},
        {"channels": ["bogus"]},
        {"channels": ["updates"], "extra": True},
        {"channels": ["updates"], "depth": True},
        {"channels": ["updates"], "namespaces": ["coach"]},
    ],
)
def test_v2_rejects_invalid_stream_event_subscriptions(
    monkeypatch: MonkeyPatch, body: dict[str, JSONValue]
) -> None:
    monkeypatch.setattr(perimeter, "HC_RAG_MEMBER_STREAM_PERIMETER", "v2")

    with pytest.raises(perimeter.PerimeterDenied) as denied:
        perimeter.validate_member_request(
            "POST", f"/threads/{THREAD_ID}/stream/events", "", body
        )

    assert denied.value.status_code == 400


@pytest.mark.parametrize(
    "body",
    [
        {"id": 1, "method": "agent.getTree", "params": {}},
        {"id": 1, "method": "subscription.create", "params": {}},
        {
            "id": 1,
            "method": "run.start",
            "params": {
                "assistant_id": "foreign",
                "input": {"question": "Hello"},
            },
        },
        {
            "id": 1,
            "method": "run.start",
            "params": {
                "assistant_id": "coach",
                "input": {"question": "Hello"},
                "metadata": {"owner": "member"},
            },
        },
        {
            "id": 1,
            "method": "run.start",
            "params": {
                "assistant_id": "coach",
                "input": {"question": "Hello"},
                "streamMode": ["updates", "messages"],
            },
        },
        {
            "id": 1,
            "method": "input.respond",
            "params": {
                "interrupt_id": "interrupt-1",
                "response": {"accept": True},
                "update": {"messages": []},
            },
        },
        {
            "id": 1,
            "method": "input.respond",
            "params": {
                "interrupt_id": "interrupt-1",
                "response": {"accept": True},
                "goto": "coach",
            },
        },
        {"id": 1, "method": "state.get", "params": {}, "extra": True},
    ],
)
def test_v2_rejects_non_member_commands(
    monkeypatch: MonkeyPatch, body: dict[str, JSONValue]
) -> None:
    monkeypatch.setattr(perimeter, "HC_RAG_MEMBER_STREAM_PERIMETER", "v2")

    with pytest.raises(perimeter.PerimeterDenied) as denied:
        perimeter.validate_member_request(
            "POST", f"/threads/{THREAD_ID}/commands", "", body
        )

    assert denied.value.status_code == 400


@pytest.mark.parametrize(
    ("body", "path_thread_id"),
    [
        (
            {
                "id": 1,
                "method": "run.start",
                "params": {
                    "assistant_id": "coach",
                    "input": {"question": "Hello"},
                    "config": {
                        "configurable": {"thread_id": THREAD_ID, "checkpoint_id": "cp-1"}
                    },
                },
            },
            THREAD_ID,
        ),
        (
            {
                "id": 2,
                "method": "input.respond",
                "params": {
                    "namespace": [],
                    "interrupt_id": "interrupt-1",
                    "response": {"accept": True},
                },
            },
            THREAD_ID,
        ),
    ],
)
def test_v2_admits_sdk_wire_shapes(
    monkeypatch: MonkeyPatch, body: dict[str, JSONValue], path_thread_id: str
) -> None:
    monkeypatch.setattr(perimeter, "HC_RAG_MEMBER_STREAM_PERIMETER", "v2")

    perimeter.validate_member_request(
        "POST", f"/threads/{path_thread_id}/commands", "", body
    )


def test_v2_rejects_cross_thread_config_thread_id(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(perimeter, "HC_RAG_MEMBER_STREAM_PERIMETER", "v2")

    with pytest.raises(perimeter.PerimeterDenied) as denied:
        perimeter.validate_member_request(
            "POST",
            f"/threads/{THREAD_ID}/commands",
            "",
            {
                "id": 1,
                "method": "run.start",
                "params": {
                    "assistant_id": "coach",
                    "input": {"question": "Hello"},
                    "config": {
                        "configurable": {
                            "thread_id": "00000000-0000-0000-0000-00000000dead"
                        }
                    },
                },
            },
        )

    assert denied.value.status_code == 400


@pytest.mark.parametrize("strategy", ["reject", "enqueue", "interrupt", None])
def test_v2_run_start_admits_sdk_multitask_strategies(
    monkeypatch: MonkeyPatch, strategy: str | None
) -> None:
    monkeypatch.setattr(perimeter, "HC_RAG_MEMBER_STREAM_PERIMETER", "v2")

    params: dict[str, JSONValue] = {
        "assistant_id": "coach",
        "input": {"question": "Hello"},
    }
    if strategy is not None:
        params["multitaskStrategy"] = strategy

    perimeter.validate_member_request(
        "POST",
        f"/threads/{THREAD_ID}/commands",
        "",
        {"id": 1, "method": "run.start", "params": params},
    )


def test_v2_rejects_unknown_multitask_strategy(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(perimeter, "HC_RAG_MEMBER_STREAM_PERIMETER", "v2")

    with pytest.raises(perimeter.PerimeterDenied) as denied:
        perimeter.validate_member_request(
            "POST",
            f"/threads/{THREAD_ID}/commands",
            "",
            {
                "id": 1,
                "method": "run.start",
                "params": {
                    "assistant_id": "coach",
                    "input": {"question": "Hello"},
                    "multitaskStrategy": "rollback",
                },
            },
        )

    assert denied.value.status_code == 400


@pytest.mark.parametrize(
    "path",
    [
        f"/threads/{THREAD_ID}/stream/events",
        f"/threads/{THREAD_ID}/commands",
    ],
)
def test_v1_rejects_threadstream_routes(
    monkeypatch: MonkeyPatch, path: str
) -> None:
    monkeypatch.setattr(perimeter, "HC_RAG_MEMBER_STREAM_PERIMETER", "v1")

    with pytest.raises(perimeter.PerimeterDenied) as denied:
        perimeter.validate_member_request("POST", path, "", {})

    assert denied.value.status_code == 403


@pytest.mark.parametrize(
    ("query", "allowed"),
    [
        ("wait=true&action=interrupt&timeout=30", True),
        ("foo=1", False),
        ("action=bogus", False),
    ],
)
def test_v2_cancel_accepts_only_sdk_query_parameters(
    monkeypatch: MonkeyPatch, query: str, allowed: bool
) -> None:
    monkeypatch.setattr(perimeter, "HC_RAG_MEMBER_STREAM_PERIMETER", "v2")
    path = f"/threads/{THREAD_ID}/runs/{RUN_ID}/cancel"

    if allowed:
        perimeter.validate_member_request("POST", path, query, None)
        return

    with pytest.raises(perimeter.PerimeterDenied):
        perimeter.validate_member_request("POST", path, query, None)
