from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import httpx
import pytest


def test_health_is_public_but_native_routes_require_authentication(
    agent_server: str,
) -> None:
    assert httpx.get(f"{agent_server}/ok").status_code == 200
    assert httpx.post(f"{agent_server}/threads", json={}).status_code == 401


def test_member_can_create_read_and_search_only_owned_threads(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    with httpx.Client(base_url=agent_server, headers=member_headers) as client:
        created = client.post("/threads", json={})
        assert created.status_code == 200
        thread_id = created.json()["thread_id"]
        assert created.json()["metadata"] == {"user_id": "member-a"}
        assert client.get(f"/threads/{thread_id}").status_code == 200
        search = client.post(
            "/threads/search",
            json={"select": ["thread_id", "metadata"], "limit": 100, "offset": 0},
        )
        assert search.status_code == 200
        assert [thread["thread_id"] for thread in search.json()] == [thread_id], (
            search.json()
        )

    other = httpx.get(
        f"{agent_server}/threads/{thread_id}",
        headers={"authorization": "Bearer member-b"},
    )
    assert other.status_code in {403, 404}


def test_member_perimeter_rejects_unlisted_native_surface(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    response = httpx.get(
        f"{agent_server}/assistants",
        headers=member_headers,
    )
    assert response.status_code == 403


def test_member_can_copy_only_an_owned_thread(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    with httpx.Client(base_url=agent_server, headers=member_headers) as client:
        source = client.post("/threads", json={})
        assert source.status_code == 200
        copied = client.post(f"/threads/{source.json()['thread_id']}/copy")
        assert copied.status_code == 200
        assert copied.json()["metadata"] == {"user_id": "member-a"}
        private_source = client.post("/threads", json={})
        assert private_source.status_code == 200

    denied = httpx.post(
        f"{agent_server}/threads/{private_source.json()['thread_id']}/copy",
        headers={"authorization": "Bearer member-b"},
    )
    assert denied.status_code in {403, 404}, denied.text


def test_owned_delete_completes_cleanup_before_removing_thread(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    with httpx.Client(base_url=agent_server, headers=member_headers) as client:
        thread_id = client.post("/threads", json={}).json()["thread_id"]
        deleted = client.delete(f"/threads/{thread_id}")
        assert deleted.status_code == 204
        assert client.get(f"/threads/{thread_id}").status_code == 404

    unauthorized = httpx.delete(
        f"{agent_server}/threads/{thread_id}",
        headers={"authorization": "Bearer member-b"},
    )
    assert unauthorized.status_code in {403, 404}


def test_owned_delete_does_not_depend_on_the_request_host(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    """Regression: the perimeter's delete pre-check used to re-call the app
    via ``request.base_url`` (client-controlled Host). Behind a proxy that
    Host is the public domain, the self-fetch left the app and every member
    delete 403'd. The check must read the registry, not the network."""
    with httpx.Client(
        base_url=agent_server,
        headers={**member_headers, "host": "unreachable.invalid"},
    ) as client:
        thread_id = client.post("/threads", json={}).json()["thread_id"]
        deleted = client.delete(f"/threads/{thread_id}")
        assert deleted.status_code == 204


def test_internal_reservation_requires_dual_secret_and_is_hidden_from_members(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    reservation_id = str(uuid4())
    intended_thread = str(uuid4())
    payload = {
        "thread_id": reservation_id,
        "if_exists": "raise",
        "ttl": {"strategy": "delete", "ttl": 15},
        "metadata": {
            "resource_kind": "upload_reservation",
            "owner": "member-a",
            "intended_thread": intended_thread,
        },
    }
    partial = httpx.post(
        f"{agent_server}/threads",
        headers={"x-api-key": "platform-secret"},
        json=payload,
    )
    assert partial.status_code == 401
    created = httpx.post(
        f"{agent_server}/threads",
        headers={
            "x-api-key": "platform-secret",
            "x-internal-token": "internal-secret",
        },
        json=payload,
    )
    assert created.status_code == 200
    assert httpx.get(
        f"{agent_server}/threads/{reservation_id}", headers=member_headers
    ).status_code in {403, 404}


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {
            "resource_kind": "ordinary",
            "owner": "member-a",
            "intended_thread": "00000000-0000-0000-0000-000000000001",
        },
        {
            "resource_kind": "upload_reservation",
            "owner": "member-a",
            "intended_thread": "00000000-0000-0000-0000-000000000001",
            "extra": True,
        },
    ],
)
def test_internal_reservation_rejects_non_exact_metadata(
    agent_server: str,
    metadata: dict[str, object],
) -> None:
    response = httpx.post(
        f"{agent_server}/threads",
        headers={
            "x-api-key": "platform-secret",
            "x-internal-token": "internal-secret",
        },
        json={"thread_id": str(uuid4()), "metadata": metadata},
    )
    assert response.status_code == 403


def test_reservation_credentials_cannot_access_ordinary_threads(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    created = httpx.post(
        f"{agent_server}/threads", headers=member_headers, json={}
    ).json()
    response = httpx.get(
        f"{agent_server}/threads/{created['thread_id']}",
        headers={
            "x-api-key": "platform-secret",
            "x-internal-token": "internal-secret",
        },
    )
    assert response.status_code in {403, 404}


def test_internal_owner_claim_is_forbidden_on_reservation_signature(
    agent_server: str,
) -> None:
    response = httpx.post(
        f"{agent_server}/threads",
        headers={
            "x-api-key": "platform-secret",
            "x-internal-token": "internal-secret",
            "x-internal-owner": "member-a",
        },
        json={
            "thread_id": str(uuid4()),
            "metadata": {
                "resource_kind": "upload_reservation",
                "owner": "member-a",
                "intended_thread": str(uuid4()),
            },
        },
    )
    assert response.status_code == 403


def test_cron_ops_are_owner_scoped_and_member_cron_calls_are_denied(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    thread_id = httpx.post(
        f"{agent_server}/threads", headers=member_headers, json={}
    ).json()["thread_id"]
    wake = {
        "reminder_id": str(uuid4()),
        "user_id": "member-a",
        "thread_id": thread_id,
        "wake_token": "fixture-token",
    }
    payload = {
        "schedule": "0 0 1 1 *",
        "assistant_id": "coach",
        "input": {"cron_wake": wake},
        "metadata": {"user_id": "member-a", "reminder_id": wake["reminder_id"]},
        "enabled": False,
        "multitask_strategy": "enqueue",
    }
    assert (
        httpx.post(
            f"{agent_server}/threads/{thread_id}/runs/crons",
            headers=member_headers,
            json=payload,
        ).status_code
        == 403
    )
    cron_headers = {
        "x-api-key": "platform-secret",
        "x-internal-token": "internal-secret",
        "x-internal-owner": "member-a",
    }
    created = httpx.post(
        f"{agent_server}/threads/{thread_id}/runs/crons",
        headers=cron_headers,
        json=payload,
    )
    assert created.status_code == 200, created.text
    cron_id = created.json()["cron_id"]
    search = httpx.post(
        f"{agent_server}/runs/crons/search",
        headers=cron_headers,
        json={"limit": 100, "offset": 0},
    )
    assert search.status_code == 200
    assert any(item["cron_id"] == cron_id for item in search.json())
    assert (
        httpx.delete(
            f"{agent_server}/runs/crons/{cron_id}", headers=cron_headers
        ).status_code
        == 204
    )


def test_cron_owner_must_match_payload_metadata(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    thread_id = httpx.post(
        f"{agent_server}/threads", headers=member_headers, json={}
    ).json()["thread_id"]
    response = httpx.post(
        f"{agent_server}/threads/{thread_id}/runs/crons",
        headers={
            "x-api-key": "platform-secret",
            "x-internal-token": "internal-secret",
            "x-internal-owner": "member-b",
        },
        json={
            "schedule": "0 0 1 1 *",
            "assistant_id": "coach",
            "input": {},
            "metadata": {"user_id": "member-a"},
            "enabled": False,
        },
    )
    assert response.status_code == 403


def test_cors_preflight_allows_only_configured_origin(agent_server: str) -> None:
    allowed = httpx.options(
        f"{agent_server}/threads",
        headers={
            "origin": "https://coach.test",
            "access-control-request-method": "POST",
            "access-control-request-headers": "authorization,content-type",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://coach.test"

    denied = httpx.options(
        f"{agent_server}/threads",
        headers={
            "origin": "https://evil.test",
            "access-control-request-method": "POST",
        },
    )
    assert denied.status_code == 400


def test_upload_is_streamed_extracted_and_idempotent(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    with httpx.Client(base_url=agent_server, headers=member_headers) as client:
        thread = client.post("/threads", json={})
        assert thread.status_code == 200
        thread_id = thread.json()["thread_id"]
        upload_id = str(uuid4())
        files = {"file": ("document.png", b"\x89PNG\r\n\x1a\nfixture", "image/png")}
        data = {"upload_id": upload_id, "thread_id": thread_id}
        created = client.post("/coach/uploads", data=data, files=files, timeout=30)
        assert created.status_code == 201
        assert created.json() == {"stage": "done"}
        status = client.get(f"/coach/uploads/{upload_id}/status")
        assert status.status_code == 200
        assert status.json() == {"stage": "done"}
        duplicate = client.post("/coach/uploads", data=data, files=files, timeout=30)
        assert duplicate.status_code == 200
        assert duplicate.json() == {"stage": "done"}


def test_simultaneous_uploads_extract_exactly_once(
    agent_server: str,
    member_headers: dict[str, str],
    extraction_call_count: Callable[[], int],
) -> None:
    thread_id = httpx.post(
        f"{agent_server}/threads", headers=member_headers, json={}
    ).json()["thread_id"]
    upload_id = str(uuid4())
    barrier = Barrier(3)

    def upload() -> httpx.Response:
        barrier.wait()
        return httpx.post(
            f"{agent_server}/coach/uploads",
            headers=member_headers,
            data={"upload_id": upload_id, "thread_id": thread_id},
            files={
                "file": (
                    "document.png",
                    b"\x89PNG\r\n\x1a\nfixture",
                    "image/png",
                )
            },
            timeout=30,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(upload)
        second = executor.submit(upload)
        barrier.wait()
        responses = [first.result(), second.result()]

    assert sorted(response.status_code for response in responses) == [200, 201]
    assert extraction_call_count() == 1


def test_upload_id_cannot_cross_owner_or_intended_thread(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    thread_a = httpx.post(
        f"{agent_server}/threads", headers=member_headers, json={}
    ).json()["thread_id"]
    thread_b = httpx.post(
        f"{agent_server}/threads", headers=member_headers, json={}
    ).json()["thread_id"]
    upload_id = str(uuid4())
    files = {"file": ("document.png", b"\x89PNG\r\n\x1a\nfixture", "image/png")}
    created = httpx.post(
        f"{agent_server}/coach/uploads",
        headers=member_headers,
        data={"upload_id": upload_id, "thread_id": thread_a},
        files=files,
        timeout=30,
    )
    assert created.status_code == 201
    wrong_thread = httpx.post(
        f"{agent_server}/coach/uploads",
        headers=member_headers,
        data={"upload_id": upload_id, "thread_id": thread_b},
        files=files,
        timeout=30,
    )
    assert wrong_thread.status_code == 409

    member_b_headers = {"authorization": "Bearer member-b"}
    member_b_thread = httpx.post(
        f"{agent_server}/threads", headers=member_b_headers, json={}
    ).json()["thread_id"]
    wrong_owner = httpx.post(
        f"{agent_server}/coach/uploads",
        headers=member_b_headers,
        data={"upload_id": upload_id, "thread_id": member_b_thread},
        files=files,
        timeout=30,
    )
    assert wrong_owner.status_code == 409


@pytest.mark.parametrize(
    ("filename", "content", "mime_type", "expected"),
    [
        ("document.exe", b"fixture", "application/octet-stream", 415),
        (
            "document.png",
            b"\x89PNG\r\n\x1a\n" + b"x" * (11 * 1024 * 1024),
            "image/png",
            413,
        ),
    ],
)
def test_upload_rejects_unsafe_file_shapes(
    agent_server: str,
    member_headers: dict[str, str],
    filename: str,
    content: bytes,
    mime_type: str,
    expected: int,
) -> None:
    thread_id = httpx.post(
        f"{agent_server}/threads", headers=member_headers, json={}
    ).json()["thread_id"]
    response = httpx.post(
        f"{agent_server}/coach/uploads",
        headers=member_headers,
        data={"upload_id": str(uuid4()), "thread_id": thread_id},
        files={"file": (filename, content, mime_type)},
        timeout=30,
    )
    assert response.status_code == expected


def test_attachment_is_thread_bound_and_single_use(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    with httpx.Client(base_url=agent_server, headers=member_headers) as client:
        intended = client.post("/threads", json={}).json()["thread_id"]
        other = client.post("/threads", json={}).json()["thread_id"]
        upload_id = str(uuid4())
        upload = client.post(
            "/coach/uploads",
            data={"upload_id": upload_id, "thread_id": intended},
            files={
                "file": (
                    "document.png",
                    b"\x89PNG\r\n\x1a\nfixture",
                    "image/png",
                )
            },
            timeout=30,
        )
        assert upload.status_code == 201
        envelope = {
            "assistant_id": "coach",
            "input": {
                "question": "Please review this document.",
                "attachment_id": upload_id,
            },
            "stream_mode": ["updates"],
            "stream_subgraphs": False,
            "stream_resumable": False,
            "durability": "exit",
            "if_not_exists": "reject",
            "multitask_strategy": "reject",
        }
        wrong_thread = client.post(
            f"/threads/{other}/runs/stream", json=envelope, timeout=30
        )
        assert wrong_thread.status_code == 403
        accepted = client.post(
            f"/threads/{intended}/runs/stream", json=envelope, timeout=30
        )
        assert accepted.status_code == 200
        consumed = client.post(
            f"/threads/{intended}/runs/stream", json=envelope, timeout=30
        )
        assert consumed.status_code == 403


def test_feedback_targets_owned_latest_state_message_and_uses_langsmith_only_key(
    agent_server: str,
    member_headers: dict[str, str],
    feedback_requests: list[tuple[dict[str, str], dict[str, object]]],
) -> None:
    with httpx.Client(base_url=agent_server, headers=member_headers) as client:
        thread_id = client.post("/threads", json={}).json()["thread_id"]
        run = client.post(
            f"/threads/{thread_id}/runs/stream",
            json={
                "assistant_id": "coach",
                "input": {"question": "fixture question"},
                "stream_mode": ["updates"],
                "stream_subgraphs": False,
                "stream_resumable": False,
                "durability": "exit",
                "if_not_exists": "reject",
                "multitask_strategy": "reject",
            },
            timeout=30,
        )
        assert run.status_code == 200, run.text
        state = client.get(f"/threads/{thread_id}/state")
        assert state.status_code == 200
        message_id = state.json()["values"]["messages"][-1]["id"]
        feedback = client.post(
            "/coach/feedback",
            json={
                "thread_id": thread_id,
                "message_id": message_id,
                "score": 1,
                "comment": "helpful",
            },
        )
        assert feedback.status_code == 201, feedback.text

    assert len(feedback_requests) == 1
    headers, payload = feedback_requests[0]
    assert headers["x-api-key"] == "platform-secret"
    assert "x-internal-token" not in {key.lower() for key in headers}
    assert payload["session_id"] == "00000000-0000-4000-8000-000000000fee"
    assert payload.get("run_id") is None


def test_feedback_rejects_absent_message_and_other_users_thread(
    agent_server: str,
    member_headers: dict[str, str],
) -> None:
    thread_id = httpx.post(
        f"{agent_server}/threads", headers=member_headers, json={}
    ).json()["thread_id"]
    absent = httpx.post(
        f"{agent_server}/coach/feedback",
        headers=member_headers,
        json={"thread_id": thread_id, "message_id": "absent", "score": -1},
    )
    assert absent.status_code == 403
    other = httpx.post(
        f"{agent_server}/coach/feedback",
        headers={"authorization": "Bearer member-b"},
        json={"thread_id": thread_id, "message_id": "absent", "score": -1},
    )
    assert other.status_code == 403
