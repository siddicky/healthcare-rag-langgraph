#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Export the deployment URL, two member tokens, and server credentials.
# 3. Run: uv run python scripts/deployed_smoke.py --url "$LANGGRAPH_DEPLOYMENT_URL"
# ──────────────────

from __future__ import annotations

# noqa: SIZE_OK - the acceptance plan requires all ten deployed checks in one script.
import argparse
import json
import os
import re
import socket
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, TypeAlias, override
from urllib.parse import urlparse
from uuid import UUID, uuid4

import anyio
import httpx
from anyio import to_thread
from langsmith import Client as LangSmithClient
from pydantic import JsonValue, TypeAdapter

JSONValue: TypeAlias = JsonValue
EXPECTED_API_VERSION: Final = "0.13.0"
ASSISTANT_ID: Final = "coach"
DOCUMENT_QUESTION: Final = "Please review this document."
ERASE_MARKER: Final = "erase_confirmation_v1"
PAGE_SIZE: Final = 100
RUN_FIXED: Final[dict[str, JSONValue]] = {
    "assistant_id": ASSISTANT_ID,
    "stream_mode": ["updates"],
    "stream_subgraphs": False,
    "stream_resumable": False,
    "durability": "exit",
    "if_not_exists": "reject",
    "multitask_strategy": "reject",
}
PROJECT_ROOT: Final = Path(__file__).parents[1]


@dataclass(frozen=True, slots=True)
class SmokeConfigurationError(RuntimeError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class SmokeFailure(AssertionError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class SmokeSettings:
    url: str
    u1_token: str
    u2_token: str
    platform_key: str
    internal_token: str
    feedback_project_id: str

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str],
        *,
        url: str | None = None,
        allow_insecure_staging: bool = False,
    ) -> SmokeSettings:
        values = {
            "LANGGRAPH_DEPLOYMENT_URL": url
            or environment.get("LANGGRAPH_DEPLOYMENT_URL", ""),
            "LANGGRAPH_U1_TOKEN": environment.get("LANGGRAPH_U1_TOKEN", ""),
            "LANGGRAPH_U2_TOKEN": environment.get("LANGGRAPH_U2_TOKEN", ""),
            "LANGSMITH_API_KEY": environment.get("LANGSMITH_API_KEY", ""),
            "COACH_INTERNAL_TOKEN": environment.get("COACH_INTERNAL_TOKEN", ""),
            "LANGSMITH_FEEDBACK_PROJECT_ID": environment.get(
                "LANGSMITH_FEEDBACK_PROJECT_ID", ""
            ),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise SmokeConfigurationError(
                f"missing required environment variable: {missing[0]}"
            )
        parsed = urlparse(values["LANGGRAPH_DEPLOYMENT_URL"])
        is_local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if not allow_insecure_staging and (parsed.scheme != "https" or is_local):
            raise SmokeConfigurationError(
                "--url must be a deployed HTTPS URL, never langgraph dev; "
                "use --allow-insecure-staging only for an isolated staging deployment"
            )
        try:
            feedback_id = str(UUID(values["LANGSMITH_FEEDBACK_PROJECT_ID"]))
        except ValueError:
            raise SmokeConfigurationError(
                "LANGSMITH_FEEDBACK_PROJECT_ID must be a UUID"
            ) from None
        return cls(
            url=values["LANGGRAPH_DEPLOYMENT_URL"].rstrip("/"),
            u1_token=values["LANGGRAPH_U1_TOKEN"],
            u2_token=values["LANGGRAPH_U2_TOKEN"],
            platform_key=values["LANGSMITH_API_KEY"],
            internal_token=values["COACH_INTERNAL_TOKEN"],
            feedback_project_id=feedback_id,
        )


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise SmokeFailure(reason)


def json_body(response: httpx.Response) -> JSONValue:
    try:
        return TypeAdapter(JsonValue).validate_json(response.content)
    except ValueError as error:
        raise SmokeFailure(
            f"{response.request.method} {response.request.url.path} returned invalid JSON"
        ) from error


def mapping_body(response: httpx.Response) -> dict[str, JSONValue]:
    payload = json_body(response)
    require(isinstance(payload, dict), "expected a JSON object")
    assert isinstance(payload, dict)
    return payload


def list_body(response: httpx.Response) -> list[JSONValue]:
    payload = json_body(response)
    if isinstance(payload, dict):
        payload = payload.get("items", [])
    require(isinstance(payload, list), "expected a JSON array")
    assert isinstance(payload, list)
    return payload


def message_text(message: JSONValue) -> str:
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    return json.dumps(content, sort_keys=True)


class DeployedSmoke:
    def __init__(self, settings: SmokeSettings, client: httpx.AsyncClient) -> None:
        self.settings: SmokeSettings = settings
        self.client: httpx.AsyncClient = client
        self.u1_threads: list[str] = []
        self.u2_threads: list[str] = []

    def member_headers(self, token: str) -> dict[str, str]:
        return {"authorization": f"Bearer {token}"}

    def internal_headers(self, owner: str | None = None) -> dict[str, str]:
        headers = {
            "x-api-key": self.settings.platform_key,
            "x-internal-token": self.settings.internal_token,
        }
        if owner is not None:
            headers["x-internal-owner"] = owner
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_value: JSONValue | None = None,
        expected: int | set[int] = 200,
        data: Mapping[str, str] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
    ) -> httpx.Response:
        response = await self.client.request(
            method,
            path,
            headers=headers,
            json=json_value,
            data=data,
            files=files,
        )
        expected_codes = {expected} if isinstance(expected, int) else expected
        require(
            response.status_code in expected_codes,
            f"{method} {path}: expected {sorted(expected_codes)}, got "
            f"{response.status_code}: {response.text[:300]}",
        )
        return response

    async def create_thread(self, token: str) -> str:
        response = await self.request(
            "POST", "/threads", headers=self.member_headers(token), json_value={}
        )
        thread_id = mapping_body(response).get("thread_id")
        if not isinstance(thread_id, str):
            raise SmokeFailure("thread creation omitted thread_id")
        return thread_id

    async def state(self, token: str, thread_id: str) -> dict[str, JSONValue]:
        response = await self.request(
            "GET", f"/threads/{thread_id}/state", headers=self.member_headers(token)
        )
        return mapping_body(response)

    async def run_turn(
        self,
        token: str,
        thread_id: str,
        *,
        question: str | None = None,
        attachment_id: str | None = None,
        command: Mapping[str, JSONValue] | None = None,
        expected: int | set[int] = 200,
    ) -> httpx.Response:
        body = dict(RUN_FIXED)
        if command is not None:
            body["command"] = dict(command)
        else:
            run_input: dict[str, JSONValue] = {"question": question or ""}
            if attachment_id is not None:
                run_input["attachment_id"] = attachment_id
            body["input"] = run_input
        return await self.request(
            "POST",
            f"/threads/{thread_id}/runs/stream",
            headers=self.member_headers(token),
            json_value=body,
            expected=expected,
        )

    async def messages(self, token: str, thread_id: str) -> list[JSONValue]:
        state = await self.state(token, thread_id)
        values = state.get("values")
        require(isinstance(values, dict), "state omitted values")
        assert isinstance(values, dict)
        raw_messages = values.get("messages", [])
        require(isinstance(raw_messages, list), "state messages are not a list")
        assert isinstance(raw_messages, list)
        return raw_messages

    async def verify_version_gate(self) -> None:
        path = "/coach/internal/version"
        unauthorized = [
            {},
            {"x-api-key": self.settings.platform_key},
            {"x-internal-token": self.settings.internal_token},
            self.member_headers(self.settings.u1_token),
        ]
        for headers in unauthorized:
            _ = await self.request("GET", path, headers=headers, expected={401, 403})
        response = await self.request("GET", path, headers=self.internal_headers())
        version = mapping_body(response).get("version")
        require(
            version == EXPECTED_API_VERSION,
            f"remote Agent Server version must be {EXPECTED_API_VERSION}, got {version!r}",
        )
        print(f"PASS version gate: remote langgraph_api=={EXPECTED_API_VERSION}")

    async def check_memory(self) -> None:
        thread_id = await self.create_thread(self.settings.u1_token)
        self.u1_threads.append(thread_id)
        fact = f"deployment-smoke-{uuid4()}"
        _ = await self.run_turn(
            self.settings.u1_token,
            thread_id,
            question=f"Remember that my preferred coaching label is {fact}.",
        )
        _ = await self.run_turn(
            self.settings.u1_token,
            thread_id,
            question="What coaching label did I ask you to remember?",
        )
        rendered = "\n".join(
            message_text(item)
            for item in await self.messages(self.settings.u1_token, thread_id)
        )
        require(fact in rendered, "remember_fact did not round-trip through chat")
        _ = await self.request(
            "PUT",
            "/store/items",
            headers=self.member_headers(self.settings.u1_token),
            json_value={
                "namespace": ["users", "u1", "profile"],
                "key": "forbidden",
                "value": {"text": "must not persist"},
            },
            expected={401, 403, 404, 405},
        )
        print("PASS 1: memory round-trip; direct member store put denied")

    async def check_isolation(self) -> None:
        u1_thread = self.u1_threads[-1]
        denied = await self.request(
            "GET",
            f"/threads/{u1_thread}/state",
            headers=self.member_headers(self.settings.u2_token),
            expected={403, 404},
        )
        require(denied.status_code in {403, 404}, "u2 read u1 state")
        search = await self.request(
            "POST",
            "/threads/search",
            headers=self.member_headers(self.settings.u2_token),
            json_value={"select": ["thread_id"], "limit": 100, "offset": 0},
        )
        require(
            all(
                not isinstance(item, Mapping) or item.get("thread_id") != u1_thread
                for item in list_body(search)
            ),
            "u2 thread search exposed u1",
        )
        print("PASS 2: u2 is blind to u1 threads and state")

    async def check_interrupts(self) -> None:
        thread_id = await self.create_thread(self.settings.u1_token)
        self.u1_threads.append(thread_id)
        _ = await self.run_turn(
            self.settings.u1_token,
            thread_id,
            question="Move my Monday injection schedule to Tuesday at 09:00.",
        )
        before = await self.state(self.settings.u1_token, thread_id)
        interrupts = before.get("interrupts")
        require(
            isinstance(interrupts, list) and bool(interrupts),
            "schedule change did not interrupt",
        )
        resume_payload: dict[str, JSONValue] = {"accept": False}
        resume: dict[str, JSONValue] = {"resume": resume_payload}
        _ = await self.run_turn(self.settings.u1_token, thread_id, command=resume)
        first = await self.state(self.settings.u1_token, thread_id)
        replay = await self.run_turn(
            self.settings.u1_token, thread_id, command=resume, expected={200, 409}
        )
        after = await self.state(self.settings.u1_token, thread_id)
        require(
            replay.status_code == 409 or first.get("values") == after.get("values"),
            "interrupt replay changed the recorded outcome",
        )
        statuses: list[int] = []

        async def submit(question: str) -> None:
            response = await self.run_turn(
                self.settings.u1_token,
                thread_id,
                question=question,
                expected={200, 409},
            )
            statuses.append(response.status_code)

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(submit, "Show my schedule.")
            task_group.start_soon(submit, "Show my reminders.")
        require(409 in statuses, "concurrent second run on one thread was not rejected")
        print("PASS 3: interrupt resume/replay recorded; concurrent run rejected")

    async def check_projection(self) -> None:
        thread_id = await self.create_thread(self.settings.u2_token)
        self.u2_threads.append(thread_id)
        _ = await self.run_turn(
            self.settings.u2_token, thread_id, question="What is Lipitor used for?"
        )
        state = await self.state(self.settings.u2_token, thread_id)
        require(
            set(state) == {"values", "interrupts"},
            "latest-state response was not projected",
        )
        values = state.get("values")
        require(isinstance(values, Mapping), "projected state omitted values")
        assert isinstance(values, Mapping)
        require(
            set(values).issubset({"messages", "follow_ups"}),
            f"projected state exposed private values keys: {set(values) - {'messages', 'follow_ups'}}",
        )
        require(
            "pending_document_op_id" not in json.dumps(state),
            "projection exposed pending document id",
        )
        print("PASS 4: u2 latest-state full response and values projection are safe")

    async def check_perimeter(self) -> None:
        thread_id = self.u2_threads[-1]
        headers = self.member_headers(self.settings.u2_token)
        messages_input: dict[str, JSONValue] = {"messages": []}
        update_command: dict[str, JSONValue] = {"update": {}}
        question_input: dict[str, JSONValue] = {"question": "x"}
        cron_input: dict[str, JSONValue] = {"cron_wake": {}}
        messages_run = dict(RUN_FIXED)
        messages_run["input"] = messages_input
        update_run = dict(RUN_FIXED)
        update_run["command"] = update_command
        webhook_run = dict(RUN_FIXED)
        webhook_run["input"] = question_input
        webhook_run["webhook"] = "https://example.test"
        cron_run = dict(RUN_FIXED)
        cron_run["input"] = cron_input
        forbidden: Sequence[tuple[str, str, JSONValue | None]] = (
            ("POST", f"/threads/{thread_id}/runs/stream", messages_run),
            ("POST", f"/threads/{thread_id}/runs/stream", update_run),
            ("POST", f"/threads/{thread_id}/runs/stream", webhook_run),
            ("GET", "/mcp", None),
            ("POST", "/a2a/coach", {}),
            ("GET", "/assistants/search", None),
            ("PATCH", f"/threads/{thread_id}", {}),
            ("GET", f"/threads/{thread_id}/state?checkpoint_id=x", None),
            ("POST", "/runs/crons/search", {}),
            ("POST", f"/threads/{thread_id}/runs/stream", cron_run),
        )
        for method, path, body in forbidden:
            _ = await self.request(
                method,
                path,
                headers=headers,
                json_value=body,
                expected={401, 403, 404, 405},
            )
        wrong_graph = dict(RUN_FIXED)
        wrong_graph["assistant_id"] = "healthcare_rag"
        wrong_input: dict[str, JSONValue] = {"question": "What is Lipitor?"}
        wrong_graph["input"] = wrong_input
        _ = await self.request(
            "POST",
            f"/threads/{thread_id}/runs/stream",
            headers=headers,
            json_value=wrong_graph,
            expected=403,
        )
        print("PASS 5: native and alternate-protocol perimeter rejections held")

    async def check_route_a(self) -> None:
        thread_id = await self.create_thread(self.settings.u2_token)
        self.u2_threads.append(thread_id)
        _ = await self.run_turn(
            self.settings.u2_token,
            thread_id,
            question="What does the Lipitor monograph say about common side effects?",
        )
        first = await self.messages(self.settings.u2_token, thread_id)
        _ = await self.run_turn(
            self.settings.u2_token,
            thread_id,
            question="What does it say about the other medicine?",
        )
        second = await self.messages(self.settings.u2_token, thread_id)
        require(
            len(second) > len(first), "Route-A follow-up did not carry prior history"
        )
        require(
            all(item in second for item in first),
            "Route-A follow-up replaced prior checkpointed messages",
        )
        print("PASS 6: two-turn Route-A carry-over retained subgraph history")

    async def _search_threads_internal(self, owner: str) -> list[JSONValue]:
        items: list[JSONValue] = []
        offset = 0
        while True:
            response = await self.request(
                "POST",
                "/threads/search",
                headers=self.internal_headers(),
                json_value={
                    "metadata": {"resource_kind": "upload_reservation", "owner": owner},
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "sort_by": "thread_id",
                    "sort_order": "asc",
                },
            )
            page = list_body(response)
            items.extend(page)
            if len(page) < PAGE_SIZE:
                return items
            offset += PAGE_SIZE

    async def check_erasure(self) -> None:
        thread_id = await self.create_thread(self.settings.u1_token)
        self.u1_threads.append(thread_id)
        for index in range(PAGE_SIZE + 1):
            _ = await self.run_turn(
                self.settings.u1_token,
                thread_id,
                question=f"Remember fact smoke-{index}: value-{index}.",
            )
        u2_before = await self.request(
            "POST",
            "/threads/search",
            headers=self.member_headers(self.settings.u2_token),
            json_value={"select": ["thread_id"], "limit": 100, "offset": 0},
        )
        _ = await self.run_turn(
            self.settings.u1_token, thread_id, question="Delete all my saved data."
        )
        messages = await self.messages(self.settings.u1_token, thread_id)
        require(
            any(
                isinstance(item, Mapping) and item.get("name") == ERASE_MARKER
                for item in messages
            ),
            "erasure completion marker was not observed",
        )
        reservations = await self._search_threads_internal("u1")
        require(not reservations, "u1 upload reservation threads survived erasure")
        registry = await self.request(
            "POST",
            "/store/items/search",
            headers=self.internal_headers(),
            json_value={
                "namespace_prefix": ["users", "u1", "upload_registry"],
                "limit": PAGE_SIZE,
                "offset": 0,
            },
        )
        require(not list_body(registry), "u1 upload registry survived erasure")
        for existing in list(self.u1_threads):
            _ = await self.request(
                "DELETE",
                f"/threads/{existing}",
                headers=self.member_headers(self.settings.u1_token),
                expected={204, 404},
            )
        u2_after = await self.request(
            "POST",
            "/threads/search",
            headers=self.member_headers(self.settings.u2_token),
            json_value={"select": ["thread_id"], "limit": 100, "offset": 0},
        )
        require(
            list_body(u2_before) == list_body(u2_after), "u1 erasure changed u2 threads"
        )
        require(
            not await self._search_threads_internal("u1"),
            "post-wipe reservation re-sweep was nonzero",
        )
        print(
            "PASS 7: paginated store/reservation erasure and snapshot-delete isolation held"
        )

    async def check_disabled_protocols(self) -> None:
        for method, path in (("GET", "/mcp"), ("POST", "/a2a/coach")):
            response = await self.request(
                method,
                path,
                headers=self.internal_headers(),
                json_value={} if method == "POST" else None,
                expected={404, 405},
            )
            require(response.status_code in {404, 405}, f"{path} is live")
        print("PASS 8: MCP and A2A routes are disabled in the live deployment")

    async def _crons(
        self, owner: str, metadata: Mapping[str, str] | None = None
    ) -> list[JSONValue]:
        response = await self.request(
            "POST",
            "/runs/crons/search",
            headers=self.internal_headers(owner),
            json_value={"metadata": dict(metadata or {}), "limit": 100, "offset": 0},
        )
        return list_body(response)

    async def check_reminders(self) -> None:
        thread_id = await self.create_thread(self.settings.u2_token)
        self.u2_threads.append(thread_id)
        title = f"Smoke reminder {uuid4()}"
        _ = await self.run_turn(
            self.settings.u2_token,
            thread_id,
            question=f"Create a reminder titled {title} every Monday at 09:00 UTC.",
        )
        crons = await self._crons("u2")
        require(len(crons) == 1, "create_reminder did not create exactly one cron")
        cron = crons[0]
        require(isinstance(cron, Mapping), "cron response shape invalid")
        assert isinstance(cron, Mapping)
        require(cron.get("schedule") == "0 9 * * 1", "created cron schedule mismatch")
        require(
            isinstance(cron.get("next_run_date"), str),
            "created cron omitted next_run_date",
        )
        _ = await self.run_turn(
            self.settings.u2_token,
            thread_id,
            question=f"Pause my {title} reminder.",
        )
        paused = await self._crons("u2")
        require(
            len(paused) == 1
            and isinstance(paused[0], Mapping)
            and paused[0].get("enabled") is False,
            "toggle did not disable cron",
        )

        pending_thread = await self.create_thread(self.settings.u2_token)
        self.u2_threads.append(pending_thread)
        _ = await self.run_turn(
            self.settings.u2_token,
            pending_thread,
            question="Move my Monday injection schedule to Tuesday at 09:00.",
        )
        pending_before = await self.state(self.settings.u2_token, pending_thread)
        pending_bytes = json.dumps(
            pending_before.get("interrupts"), sort_keys=True, separators=(",", ":")
        )
        payload = cron.get("payload")
        require(isinstance(payload, Mapping), "cron payload unavailable for fire test")
        assert isinstance(payload, Mapping)
        run_input = payload.get("input")
        require(isinstance(run_input, Mapping), "cron wake input unavailable")
        assert isinstance(run_input, Mapping)
        wake = run_input.get("cron_wake")
        require(isinstance(wake, Mapping), "cron wake unavailable")
        assert isinstance(wake, Mapping)
        wake_value: dict[str, JSONValue] = dict(wake)
        wake_value["thread_id"] = pending_thread
        next_minute = datetime.now(UTC) + timedelta(minutes=1)
        schedule = f"{next_minute.minute} {next_minute.hour} * * *"
        fire_input: dict[str, JSONValue] = {"cron_wake": wake_value}
        fire_metadata: dict[str, JSONValue] = {
            "user_id": "u2",
            "reminder_id": wake_value.get("reminder_id"),
        }
        fire_body: dict[str, JSONValue] = {
            "schedule": schedule,
            "timezone": "UTC",
            "assistant_id": ASSISTANT_ID,
            "input": fire_input,
            "metadata": fire_metadata,
            "enabled": True,
            "multitask_strategy": "enqueue",
        }
        fired = await self.request(
            "POST",
            f"/threads/{pending_thread}/runs/crons",
            headers=self.internal_headers("u2"),
            json_value=fire_body,
        )
        fire_id = mapping_body(fired).get("cron_id")
        require(isinstance(fire_id, str), "fire-test cron omitted id")
        before_count = len(await self.messages(self.settings.u2_token, pending_thread))
        with anyio.fail_after(125):
            while True:
                after_state = await self.state(self.settings.u2_token, pending_thread)
                current_count = len(
                    await self.messages(self.settings.u2_token, pending_thread)
                )
                if current_count > before_count:
                    break
                await anyio.sleep(2)
        after_bytes = json.dumps(
            after_state.get("interrupts"), sort_keys=True, separators=(",", ":")
        )
        require(after_bytes == pending_bytes, "cron clobbered pending interrupt bytes")
        require(
            current_count - before_count in {0, 1},
            "cron emitted duplicate reminder messages",
        )
        decision = (
            "delivered once while preserving interrupt"
            if current_count - before_count == 1
            else "platform no-op while preserving interrupt"
        )
        _ = await self.request(
            "DELETE",
            f"/runs/crons/{fire_id}",
            headers=self.internal_headers("u2"),
            expected={200, 204},
        )
        _ = await self.run_turn(
            self.settings.u2_token, thread_id, question=f"Cancel my {title} reminder."
        )
        metadata = cron.get("metadata")
        require(isinstance(metadata, Mapping), "created cron omitted metadata")
        assert isinstance(metadata, Mapping)
        reminder_id = metadata.get("reminder_id")
        require(isinstance(reminder_id, str), "created cron omitted reminder_id")
        assert isinstance(reminder_id, str)
        require(
            not await self._crons("u2", {"reminder_id": reminder_id}),
            "cancel left cron behind",
        )
        _ = await self.request(
            "POST",
            "/runs/crons/search",
            headers=self.member_headers(self.settings.u2_token),
            json_value={"limit": 10, "offset": 0},
            expected=403,
        )
        print(f"PASS 9: reminder lifecycle and pending-interrupt decision: {decision}")

    async def check_documents_and_feedback(self) -> None:
        thread_id = await self.create_thread(self.settings.u2_token)
        self.u2_threads.append(thread_id)
        upload_id = str(uuid4())
        pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
        stages: list[str] = []

        async def upload() -> None:
            response = await self.request(
                "POST",
                "/coach/uploads",
                headers=self.member_headers(self.settings.u2_token),
                expected={200, 201},
                data={"upload_id": upload_id, "thread_id": thread_id},
                files={"file": ("smoke.pdf", pdf, "application/pdf")},
            )
            stage = mapping_body(response).get("stage")
            if isinstance(stage, str):
                stages.append(stage)

        async def poll() -> None:
            with anyio.move_on_after(30):
                while "done" not in stages:
                    response = await self.request(
                        "GET",
                        f"/coach/uploads/{upload_id}/status",
                        headers=self.member_headers(self.settings.u2_token),
                        expected={200, 404},
                    )
                    if response.status_code == 200:
                        stage = mapping_body(response).get("stage")
                        if isinstance(stage, str) and (
                            not stages or stages[-1] != stage
                        ):
                            stages.append(stage)
                    await anyio.sleep(0.05)

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(upload)
            task_group.start_soon(poll)
        require(
            bool(stages) and stages[-1] == "done",
            f"upload stages did not complete: {stages}",
        )
        accepted_fields: list[JSONValue] = [{"key": "medication", "value": "Lipitor"}]
        accepted_resume: dict[str, JSONValue] = {
            "accept": True,
            "fields": accepted_fields,
        }
        _ = await self.run_turn(
            self.settings.u2_token,
            thread_id,
            question=DOCUMENT_QUESTION,
            attachment_id=upload_id,
        )
        state = await self.state(self.settings.u2_token, thread_id)
        interrupt_blob = json.dumps(state.get("interrupts"), sort_keys=True)
        require(
            "MemoryExtractionCard" in interrupt_blob,
            "document did not produce MemoryExtractionCard",
        )
        _ = await self.run_turn(
            self.settings.u2_token,
            thread_id,
            command={"resume": accepted_resume},
        )
        _ = await self.run_turn(
            self.settings.u2_token,
            thread_id,
            question=DOCUMENT_QUESTION,
            attachment_id=upload_id,
            expected=403,
        )
        unsafe_uploads = (
            (str(uuid4()), "bad.exe", b"MZ", "application/octet-stream", 415),
            (
                str(uuid4()),
                "large.pdf",
                b"%PDF" + b"x" * (11 * 1024 * 1024),
                "application/pdf",
                413,
            ),
        )
        for bad_id, name, content, mime, status in unsafe_uploads:
            _ = await self.request(
                "POST",
                "/coach/uploads",
                headers=self.member_headers(self.settings.u2_token),
                expected=status,
                data={"upload_id": bad_id, "thread_id": thread_id},
                files={"file": (name, content, mime)},
            )
        source = "\n".join(
            path.read_text(errors="replace")
            for path in (
                PROJECT_ROOT / "healthcare_rag/agent/uploads.py",
                PROJECT_ROOT / "healthcare_rag/agent/documents.py",
            )
        )
        forbidden_writes = (
            "write_bytes(",
            "NamedTemporaryFile",
            "mkstemp(",
            "open(.*wb",
        )
        require(
            all(re.search(pattern, source) is None for pattern in forbidden_writes),
            "static source scan found a byte persistence path",
        )
        messages = await self.messages(self.settings.u2_token, thread_id)
        assistant = next(
            (
                item
                for item in reversed(messages)
                if isinstance(item, Mapping) and item.get("type") in {"ai", "assistant"}
            ),
            None,
        )
        require(
            isinstance(assistant, Mapping), "feedback target assistant message absent"
        )
        assert isinstance(assistant, Mapping)
        message_id = assistant.get("id")
        require(isinstance(message_id, str), "feedback target omitted message id")
        _ = await self.request(
            "POST",
            "/coach/feedback",
            headers=self.member_headers(self.settings.u2_token),
            json_value={"thread_id": thread_id, "message_id": message_id, "score": 1},
            expected=201,
        )

        def read_feedback() -> list[JSONValue]:
            client = LangSmithClient(api_key=self.settings.platform_key)
            records: list[JSONValue] = []
            for feedback in client.list_feedback(
                feedback_key=["member_feedback"],
                session=[self.settings.feedback_project_id],
            ):
                extra = feedback.extra or {}
                if (
                    extra.get("thread_id") == thread_id
                    and extra.get("message_id") == message_id
                ):
                    require(
                        str(feedback.session_id) == self.settings.feedback_project_id,
                        "feedback session id mismatch",
                    )
                    require(
                        feedback.run_id is None,
                        "run-less feedback unexpectedly has run_id",
                    )
                    records.append({"id": str(feedback.id)})
            return records

        feedback = await to_thread.run_sync(read_feedback)
        require(
            len(feedback) == 1, "feedback read-back did not match exactly one record"
        )
        print(
            "PASS 10: document lifecycle, byte-artifact checks, and feedback read-back held"
        )

    async def run(self) -> None:
        await self.verify_version_gate()
        checks = (
            self.check_memory,
            self.check_isolation,
            self.check_interrupts,
            self.check_projection,
            self.check_perimeter,
            self.check_route_a,
            self.check_erasure,
            self.check_disabled_protocols,
            self.check_reminders,
            self.check_documents_and_feedback,
        )
        for check in checks:
            await check()


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Run the ten-check smoke against a deployed Coach Agent Server."
    )
    value.add_argument("--url", help="deployed Agent Server HTTPS URL")
    value.add_argument(
        "--allow-insecure-staging",
        action="store_true",
        help="allow HTTP for an isolated non-local staging deployment",
    )
    return value


async def async_main(settings: SmokeSettings) -> None:
    limits = httpx.Limits(
        max_connections=20, max_keepalive_connections=10, keepalive_expiry=30
    )
    timeout = httpx.Timeout(connect=10, read=180, write=30, pool=10)
    transport = httpx.AsyncHTTPTransport(
        retries=3,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    async with httpx.AsyncClient(
        base_url=settings.url,
        timeout=timeout,
        limits=limits,
        transport=transport,
        follow_redirects=True,
    ) as client:
        await DeployedSmoke(settings, client).run()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if not arguments.url:
        parser().error("--url is required (usually $LANGGRAPH_DEPLOYMENT_URL)")
    try:
        settings = SmokeSettings.from_environment(
            os.environ,
            url=arguments.url,
            allow_insecure_staging=arguments.allow_insecure_staging,
        )
        anyio.run(async_main, settings)
    except (SmokeConfigurationError, SmokeFailure, httpx.HTTPError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: all ten deployed smoke checks completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
