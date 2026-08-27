from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from healthcare_rag.agent.perimeter import JSONValue

THREAD_ID = "00000000-0000-0000-0000-000000000001"
COPILOTKIT_RUN: dict[str, JSONValue] = {
    "assistant_id": "coach",
    "input": {
        "question": "What is Lipitor used for?",
        "messages": [
            {"id": "msg-1", "role": "user", "content": "What is Lipitor used for?"}
        ],
        "tools": [],
        "copilotkit": {"actions": [], "context": []},
    },
    "stream_mode": ["messages", "updates"],
    "stream_subgraphs": True,
}
COPILOTKIT_RESUME: dict[str, JSONValue] = {
    "assistant_id": "coach",
    "command": {"resume": {"accept": True}},
    "stream_mode": ["messages", "updates"],
    "stream_subgraphs": True,
}


def test_copilotkit_captured_upstream_routes_are_admitted() -> None:
    from healthcare_rag.agent.perimeter import validate_member_request

    validate_member_request(
        "POST",
        "/assistants/search",
        "",
        {"graph_id": "coach", "limit": 10, "offset": 0},
    )
    validate_member_request("POST", "/threads", "", {"metadata": {}, "thread_id": THREAD_ID})
    validate_member_request("GET", f"/threads/{THREAD_ID}", "", None)
    validate_member_request("GET", f"/threads/{THREAD_ID}/state", "", None)
    validate_member_request("GET", "/assistants/coach/schemas", "", None)
    validate_member_request("GET", "/assistants/coach/graph", "", None)
    validate_member_request(
        "POST", f"/threads/{THREAD_ID}/runs/stream", "", COPILOTKIT_RUN
    )
    validate_member_request(
        "POST", f"/threads/{THREAD_ID}/runs/stream", "", COPILOTKIT_RESUME
    )


def test_copilotkit_locked_adapter_default_envelope_is_admitted() -> None:
    """The locked @ag-ui/langgraph adapter streams with its own default
    stream modes and no stream_subgraphs key (measured in e2e, todo 10)."""
    from healthcare_rag.agent.perimeter import validate_member_request

    adapter_default: dict[str, JSONValue] = {
        "assistant_id": "coach",
        "input": {
            **COPILOTKIT_RUN["input"],
            "question": "hello there",
            "ag-ui": {"tools": [], "context": []},
        },
        "stream_mode": ["events", "values", "updates", "messages-tuple"],
    }
    validate_member_request(
        "POST", f"/threads/{THREAD_ID}/runs/stream", "", adapter_default
    )
    with pytest.raises(Exception, match="Invalid run input"):
        validate_member_request(
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            "",
            {
                **adapter_default,
                "input": {**adapter_default["input"], "ag-ui": {"bogus": True}},
            },
        )
    with pytest.raises(Exception, match="Invalid run input"):
        validate_member_request(
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            "",
            {
                **adapter_default,
                "input": {**adapter_default["input"], "ag-ui": {"tools": "nope"}},
            },
        )
    validate_member_request(
        "POST",
        f"/threads/{THREAD_ID}/runs/stream",
        "",
        {**adapter_default, "assistant_id": "cf4bdb04-27e2-5eb0-b5f0-29eedf7f9d39"},
    )
    validate_member_request(
        "POST",
        f"/threads/{THREAD_ID}/runs/stream",
        "",
        {
            "assistant_id": "coach",
            "command": {"resume": {"accept": False}},
            "stream_mode": ["events", "values", "updates", "messages-tuple"],
        },
    )
    # The engine forwards the member run-envelope options through AG-UI
    # forwardedProps; the SDK serializes them into the upstream envelope.
    forwarded: dict[str, JSONValue] = {
        "assistant_id": "coach",
        "input": {
            **COPILOTKIT_RUN["input"],
            "question": "hello there",
            "ag-ui": {"tools": [], "context": []},
        },
        "stream_mode": ["updates"],
        "stream_subgraphs": False,
        "stream_resumable": False,
        "durability": "exit",
        "if_not_exists": "reject",
        "multitask_strategy": "reject",
    }
    validate_member_request(
        "POST", f"/threads/{THREAD_ID}/runs/stream", "", forwarded
    )
    # Measured resume shape: the adapter sends the merged input AND the
    # command, echoing the interrupt payload back as `interruptEvent`.
    validate_member_request(
        "POST",
        f"/threads/{THREAD_ID}/runs/stream",
        "",
        {
            **forwarded,
            "command": {
                "resume": {"accept": True},
                "interruptEvent": {"eventLabel": "Friday check-in"},
            },
        },
    )
    with pytest.raises(Exception, match="Invalid resume command"):
        validate_member_request(
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            "",
            {
                **forwarded,
                "command": {
                    "resume": {"accept": True},
                    "interruptEvent": {"x": 1},
                    "bogus": True,
                },
            },
        )


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/assistants/search", {"graph_id": "healthcare_rag"}),
        ("POST", "/assistants/search", {"graph_id": "coach", "limit": 0}),
        ("POST", "/assistants/search", {"graph_id": "coach", "offset": -1}),
        ("POST", "/assistants/search", {"graph_id": "coach", "extra": True}),
        ("POST", "/threads", {"metadata": {}, "thread_id": "not-a-uuid"}),
        ("POST", "/threads", {"metadata": {"owner": "x"}, "thread_id": THREAD_ID}),
        ("POST", "/threads", {"thread_id": THREAD_ID}),
        ("GET", "/assistants/coach/subgraphs", None),
        ("GET", "/assistants/coach", None),
        ("POST", "/assistants/count", {}),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {**COPILOTKIT_RUN, "assistant_id": "healthcare_rag"},
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {**COPILOTKIT_RUN, "durability": "bogus"},
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {**COPILOTKIT_RUN, "multitask_strategy": "bogus"},
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {**COPILOTKIT_RUN, "if_not_exists": "bogus"},
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {**COPILOTKIT_RUN, "stream_resumable": "yes"},
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {**COPILOTKIT_RUN, "stream_mode": ["bogus"]},
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {**COPILOTKIT_RUN, "stream_subgraphs": "yes"},
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {**COPILOTKIT_RUN, "config": "yes"},
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {**COPILOTKIT_RUN, "config": {"bogus": True}},
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {
                **COPILOTKIT_RUN,
                "config": {"configurable": {"thread_id": THREAD_ID}},
            },
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {
                **COPILOTKIT_RUN,
                "config": {
                    "configurable": {"copilotkit_forwarded_headers": {"x-a": 1}}
                },
            },
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {**COPILOTKIT_RUN, "config": {"recursion_limit": 0}},
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {**COPILOTKIT_RUN, "config": {"recursion_limit": True}},
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {
                **COPILOTKIT_RUN,
                "input": {"question": "hi", "cron_wake": {}},
            },
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {
                **COPILOTKIT_RUN,
                "input": {"question": "hi", "messages": [{}]},
            },
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {
                **COPILOTKIT_RUN,
                "input": {"question": "hi", "copilotkit": {"actions": [], "jailbreak": []}},
            },
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {
                **COPILOTKIT_RUN,
                "input": {"messages": [{"role": "user"}], "copilotkit": {}},
            },
        ),
        (
            "POST",
            f"/threads/{THREAD_ID}/runs/stream",
            {
                "assistant_id": "coach",
                "command": {"resume": {"accept": "yes"}},
                "stream_mode": ["updates"],
                "stream_subgraphs": True,
            },
        ),
    ],
)
def test_copilotkit_shapes_outside_the_captured_contract_are_denied(
    method: str,
    path: str,
    body: dict[str, JSONValue] | None,
) -> None:
    from healthcare_rag.agent.perimeter import PerimeterDenied, validate_member_request

    with pytest.raises(PerimeterDenied):
        validate_member_request(method, path, "", body)


def test_copilotkit_forwarded_header_config_envelope_is_admitted() -> None:
    """The locked adapter lifts incoming x-* request headers (every Vercel
    request carries x-vercel-*) into config.configurable.copilotkit_forwarded_headers
    and streams with its default modes plus stream_subgraphs=True. Captured
    from the live runtime on 2026-08-26; the shape 403'd as 'Invalid run
    envelope' before the perimeter learned it (local/e2e requests carry no
    x- headers, so only production ever produced the key)."""
    from healthcare_rag.agent.perimeter import validate_member_request

    captured: dict[str, JSONValue] = {
        "assistant_id": "coach",
        "input": {
            "question": "Log today's weight",
            "messages": [
                {"id": "m-1", "role": "user", "content": "Log today's weight"}
            ],
            "tools": [],
            "ag-ui": {"tools": [], "context": []},
            "copilotkit": {"actions": [], "context": []},
        },
        "config": {
            "configurable": {
                "copilotkit_forwarded_headers": {
                    "x-vercel-id": "iad1::abc-123",
                    "x-matched-path": "/api/copilotkit/[[...slug]]",
                }
            }
        },
        "stream_mode": ["events", "values", "updates", "messages-tuple"],
        "stream_subgraphs": True,
        "stream_resumable": True,
        "multitask_strategy": "enqueue",
        "if_not_exists": "reject",
        "durability": "exit",
    }
    validate_member_request(
        "POST", f"/threads/{THREAD_ID}/runs/stream", "", captured
    )
    validate_member_request(
        "POST",
        f"/threads/{THREAD_ID}/runs/stream",
        "",
        {**captured, "config": {"recursion_limit": 100}},
    )
    validate_member_request(
        "POST",
        f"/threads/{THREAD_ID}/runs/stream",
        "",
        {**captured, "command": {"resume": {"accept": True}}},
    )


def test_v1_member_run_envelope_is_unchanged_by_copilotkit_admission() -> None:
    from healthcare_rag.agent.perimeter import validate_member_request

    v1_envelope: dict[str, JSONValue] = {
        "assistant_id": "coach",
        "input": {"question": "What is Metformin?"},
        "stream_mode": ["updates"],
        "stream_subgraphs": False,
        "stream_resumable": False,
        "durability": "exit",
        "if_not_exists": "reject",
        "multitask_strategy": "reject",
    }
    validate_member_request(
        "POST", f"/threads/{THREAD_ID}/runs/stream", "", v1_envelope
    )


def test_member_can_drive_every_captured_runtime_route(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    with httpx.Client(base_url=agent_server, headers=member_headers) as client:
        search = client.post(
            "/assistants/search", json={"graph_id": "coach", "limit": 10, "offset": 0}
        )
        assert search.status_code == 200, search.text
        assert any(item["graph_id"] == "coach" for item in search.json())

        thread_id = str(uuid4())
        created = client.post(
            "/threads", json={"metadata": {}, "thread_id": thread_id}
        )
        assert created.status_code == 200, created.text
        assert created.json()["thread_id"] == thread_id
        assert created.json()["metadata"] == {"user_id": "member-a"}

        assert client.get(f"/threads/{thread_id}").status_code == 200
        state = client.get(f"/threads/{thread_id}/state")
        assert state.status_code == 200, state.text
        assistant_id = next(
            item["assistant_id"]
            for item in search.json()
            if item.get("graph_id") == "coach"
        )
        schemas = client.get(f"/assistants/{assistant_id}/schemas")
        assert schemas.status_code == 200, schemas.text
        graph = client.get(f"/assistants/{assistant_id}/graph")
        assert graph.status_code == 200, graph.text

        run = client.post(
            f"/threads/{thread_id}/runs/stream",
            json={**COPILOTKIT_RUN, "assistant_id": assistant_id},
            timeout=30,
        )
        assert run.status_code == 200, run.text

        assert client.delete(f"/threads/{thread_id}").status_code == 204


def test_runtime_thread_creation_forces_owner_metadata_but_keeps_client_thread_id(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    thread_id = str(uuid4())
    created = httpx.post(
        f"{agent_server}/threads",
        headers=member_headers,
        json={"metadata": {}, "thread_id": thread_id},
    )
    assert created.status_code == 200
    assert created.json()["metadata"] == {"user_id": "member-a"}
    assert (
        httpx.delete(
            f"{agent_server}/threads/{thread_id}", headers=member_headers
        ).status_code
        == 204
    )


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/assistants/search", {"graph_id": "coach", "limit": 10, "offset": 0}),
        ("POST", "/threads", {"metadata": {}, "thread_id": str(uuid4())}),
        ("GET", f"/threads/{THREAD_ID}", None),
        ("GET", f"/threads/{THREAD_ID}/state", None),
        ("GET", "/assistants/coach/schemas", None),
        ("GET", "/assistants/coach/graph", None),
        ("POST", f"/threads/{THREAD_ID}/runs/stream", COPILOTKIT_RUN),
    ],
)
@pytest.mark.parametrize(
    "headers",
    [{}, {"authorization": "Basic Zm9v"}, {"authorization": "Bearer someone-else"}],
)
def test_every_admitted_route_requires_a_valid_member_bearer(
    agent_server: str,
    method: str,
    path: str,
    body: dict[str, JSONValue] | None,
    headers: dict[str, str],
) -> None:
    request = httpx.request(
        method, f"{agent_server}{path}", headers=headers, json=body, timeout=30
    )
    assert request.status_code == 401, (path, request.status_code, request.text)


def test_member_b_cannot_reach_member_a_threads_through_the_runtime_paths(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    thread_id = httpx.post(
        f"{agent_server}/threads", headers=member_headers, json={}
    ).json()["thread_id"]
    other = {"authorization": "Bearer member-b"}

    assert (
        httpx.get(f"{agent_server}/threads/{thread_id}", headers=other).status_code
        in {403, 404}
    )
    assert (
        httpx.get(
            f"{agent_server}/threads/{thread_id}/state", headers=other
        ).status_code
        in {403, 404}
    )
    run = httpx.post(
        f"{agent_server}/threads/{thread_id}/runs/stream",
        headers=other,
        json=COPILOTKIT_RUN,
        timeout=30,
    )
    assert run.status_code in {403, 404}, run.text
    assert (
        httpx.delete(
            f"{agent_server}/threads/{thread_id}", headers=member_headers
        ).status_code
        == 204
    )


def test_surfaces_outside_the_captured_inventory_stay_denied(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    thread_id = httpx.post(
        f"{agent_server}/threads", headers=member_headers, json={}
    ).json()["thread_id"]

    assert (
        httpx.get(f"{agent_server}/assistants/coach/subgraphs", headers=member_headers).status_code
        == 403
    )
    assert (
        httpx.post(f"{agent_server}/assistants/count", headers=member_headers, json={}).status_code
        == 403
    )
    assert (
        httpx.get(f"{agent_server}/threads/{thread_id}/history", headers=member_headers).status_code
        == 403
    )
    assert (
        httpx.post(
            f"{agent_server}/threads/{thread_id}/runs/crons",
            headers=member_headers,
            json={},
        ).status_code
        == 403
    )
    assert (
        httpx.delete(
            f"{agent_server}/threads/{thread_id}", headers=member_headers
        ).status_code
        == 204
    )
