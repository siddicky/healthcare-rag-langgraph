from __future__ import annotations

from typing import Final

import pytest
from pytest import MonkeyPatch

import healthcare_rag.agent.perimeter as perimeter
from healthcare_rag.agent.perimeter import JSONValue

THREAD_ID: Final = "00000000-0000-0000-0000-000000000001"
RUN_ID: Final = "00000000-0000-0000-0000-000000000002"


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
