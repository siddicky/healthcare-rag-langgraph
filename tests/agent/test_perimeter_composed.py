from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import pytest

THREAD_ID: Final = "00000000-0000-0000-0000-000000000001"
RUN_ENVELOPE: Final = {
    "assistant_id": "coach",
    "input": {"question": "What is Lipitor used for?"},
    "stream_mode": ["updates"],
    "stream_subgraphs": False,
    "stream_resumable": False,
    "durability": "exit",
    "if_not_exists": "reject",
    "multitask_strategy": "reject",
}


def test_langgraph_config_mounts_fail_closed_http_app() -> None:
    config = json.loads(Path("langgraph.json").read_text())

    assert config["auth"]["path"] == "./healthcare_rag/agent/auth.py:auth"
    assert config["http"] == {
        "app": "./healthcare_rag/agent/http_app.py:app",
        "middleware_order": "auth_first",
        "enable_custom_route_auth": True,
        "disable_mcp": True,
        "disable_a2a": True,
    }


@pytest.mark.parametrize(
    ("method", "path", "query", "body"),
    [
        ("POST", "/threads", "", {}),
        (
            "POST",
            "/threads/search",
            "",
            {"select": ["thread_id", "metadata"], "limit": 100, "offset": 0},
        ),
        ("GET", f"/threads/{THREAD_ID}", "", None),
        ("DELETE", f"/threads/{THREAD_ID}", "", None),
        ("POST", f"/threads/{THREAD_ID}/copy", "", None),
        ("GET", f"/threads/{THREAD_ID}/state", "", None),
        ("POST", f"/threads/{THREAD_ID}/runs/stream", "", RUN_ENVELOPE),
    ],
)
def test_member_native_allow_list_accepts_only_contract_routes(
    method: str,
    path: str,
    query: str,
    body: dict[str, object] | None,
) -> None:
    from healthcare_rag.agent.perimeter import validate_member_request

    validate_member_request(method, path, query, body)


@pytest.mark.parametrize(
    ("method", "path", "query", "body"),
    [
        ("POST", "/threads", "", {"metadata": {}}),
        ("POST", "/threads/search", "", {}),
        ("POST", "/threads/search", "", {"select": ["values"]}),
        ("POST", "/threads/search", "", {"select": ["thread_id"], "ids": []}),
        ("POST", "/threads/search", "", {"select": ["thread_id"], "limit": 0}),
        ("POST", "/threads/search", "", {"select": ["thread_id"], "offset": -1}),
        ("GET", f"/threads/{THREAD_ID}", "rogue=true", None),
        ("PATCH", f"/threads/{THREAD_ID}", "", {}),
        ("GET", f"/threads/{THREAD_ID}/state", "checkpoint_id=x", None),
        ("GET", f"/threads/{THREAD_ID}/state", "subgraphs=true", None),
        ("POST", f"/threads/{THREAD_ID}/copy", "", {}),
        ("GET", f"/threads/{THREAD_ID}/history", "", None),
        ("POST", "/runs", "", {}),
        ("POST", "/runs/batch", "", []),
        ("POST", f"/threads/{THREAD_ID}/runs/crons", "", {}),
        ("GET", "/mcp", "", None),
        ("POST", "/a2a/coach", "", {}),
        ("GET", f"/threads/{THREAD_ID}/", "", None),
        ("GET", f"/threads//{THREAD_ID}", "", None),
        ("GET", f"/threads/%30{THREAD_ID[1:]}", "", None),
    ],
)
def test_member_native_allow_list_rejects_every_unlisted_surface(
    method: str,
    path: str,
    query: str,
    body: dict[str, object] | list[object] | None,
) -> None:
    from healthcare_rag.agent.perimeter import PerimeterDenied, validate_member_request

    with pytest.raises(PerimeterDenied):
        validate_member_request(method, path, query, body)


@pytest.mark.parametrize(
    ("replacement", "value"),
    [
        ("webhook", "https://example.test"),
        ("metadata", {}),
        ("feedback_keys", ["x"]),
        ("after_seconds", 1),
        ("on_disconnect", "cancel"),
        ("checkpoint_during", True),
        ("config", {}),
        ("context", {}),
        ("checkpoint", {}),
        ("interrupt_before", "*"),
        ("interrupt_after", "*"),
    ],
)
def test_run_envelope_rejects_extra_keys(replacement: str, value: object) -> None:
    from healthcare_rag.agent.perimeter import PerimeterDenied, validate_member_request

    body = {**RUN_ENVELOPE, replacement: value}
    with pytest.raises(PerimeterDenied):
        validate_member_request("POST", f"/threads/{THREAD_ID}/runs/stream", "", body)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("stream_resumable", True),
        ("durability", "async"),
        ("if_not_exists", "create"),
        ("multitask_strategy", "enqueue"),
        ("stream_subgraphs", True),
        ("stream_mode", ["values"]),
        ("assistant_id", "healthcare_rag"),
    ],
)
def test_run_envelope_rejects_wrong_fixed_value(key: str, value: object) -> None:
    from healthcare_rag.agent.perimeter import PerimeterDenied, validate_member_request

    body = {**RUN_ENVELOPE, key: value}
    with pytest.raises(PerimeterDenied):
        validate_member_request("POST", f"/threads/{THREAD_ID}/runs/stream", "", body)


def test_attachment_requires_exact_document_review_sentinel() -> None:
    from healthcare_rag.agent.perimeter import PerimeterDenied, validate_member_request

    body = {
        **RUN_ENVELOPE,
        "input": {"question": "Delete my records", "attachment_id": "upload-id"},
    }
    with pytest.raises(PerimeterDenied):
        validate_member_request("POST", f"/threads/{THREAD_ID}/runs/stream", "", body)


def test_resume_accepts_only_unified_resume_shape() -> None:
    from healthcare_rag.agent.perimeter import validate_member_request

    body = {key: value for key, value in RUN_ENVELOPE.items() if key != "input"} | {
        "command": {
            "resume": {
                "accept": True,
                "fields": [{"key": "dose", "value": "unknown"}],
            }
        }
    }

    validate_member_request("POST", f"/threads/{THREAD_ID}/runs/stream", "", body)


def test_state_projection_rejects_private_sentinels_recursively() -> None:
    from healthcare_rag.agent.perimeter import PerimeterDenied, project_state

    assert project_state({"values": {"messages": []}, "interrupts": []}) == {
        "values": {"messages": []},
        "interrupts": [],
    }
    with pytest.raises(PerimeterDenied):
        project_state({"values": {"nested": {"question": "private"}}, "interrupts": []})


def test_state_projection_allows_cleared_private_channels() -> None:
    from healthcare_rag.agent.perimeter import PerimeterDenied, project_state

    assert project_state(
        {"values": {"messages": [], "pending_document_op_id": None}, "interrupts": []}
    ) == {
        "values": {"messages": [], "pending_document_op_id": None},
        "interrupts": [],
    }
    with pytest.raises(PerimeterDenied):
        project_state(
            {"values": {"pending_document_op_id": "sha256-op"}, "interrupts": []}
        )
