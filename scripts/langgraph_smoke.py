"""Smoke-test the LangGraph SDK against the local healthcare graph.

Requires ``make dev`` running on 127.0.0.1:2024 and Weaviate running with the
Lipitor and Metformin collections populated. The graph may take 5-20 seconds
per turn because this script exercises the real LLM and retrieval path.

Run with:

    .venv/bin/python scripts/langgraph_smoke.py
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Final, Literal, Protocol, TypedDict

import anyio
import httpx
import langgraph_sdk
from langgraph_sdk.schema import Run

type JSONValue = str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]


class ThreadResponse(TypedDict):
    thread_id: str


class ThreadStateResponse(TypedDict):
    values: dict[str, JSONValue]


class SmokeEvent(Protocol):
    event: str
    data: JSONValue


class ThreadsAPI(Protocol):
    async def create(self) -> ThreadResponse: ...

    async def get_state(self, thread_id: str) -> ThreadStateResponse: ...


class RunsAPI(Protocol):
    async def wait(
        self,
        thread_id: str,
        assistant_id: str,
        *,
        input: Mapping[str, JSONValue],
        durability: Literal["exit"],
    ) -> dict[str, JSONValue]: ...

    async def create(
        self,
        thread_id: str,
        assistant_id: str,
        *,
        input: Mapping[str, JSONValue],
        multitask_strategy: Literal["enqueue"] | None = None,
        durability: Literal["exit"],
    ) -> Run: ...

    def stream(
        self,
        thread_id: str,
        assistant_id: str,
        *,
        input: Mapping[str, JSONValue],
        stream_mode: Literal["updates"],
        durability: Literal["exit"],
    ) -> AsyncIterator[SmokeEvent]: ...

    async def get(self, thread_id: str, run_id: str) -> Run: ...

    async def cancel(
        self,
        thread_id: str,
        run_id: str,
        *,
        wait: bool,
        action: Literal["rollback"],
    ) -> None: ...

    async def list(self, thread_id: str) -> list[Run]: ...

    async def join(self, thread_id: str, run_id: str) -> dict[str, JSONValue]: ...


class SmokeClient(Protocol):
    threads: ThreadsAPI
    runs: RunsAPI


class ClientFactory(Protocol):
    def __call__(self, *, url: str) -> SmokeClient: ...

SERVER_URL: Final = "http://127.0.0.1:2024"
ASSISTANT_ID: Final = "healthcare_rag"
STATUSES: Final = frozenset(
    {"pending", "running", "error", "success", "timeout", "interrupted"}
)
TERMINAL_STATUSES: Final = frozenset({"error", "success", "timeout", "interrupted"})
PII_MARKERS: Final = ("John Smith", "12345")
QUEUE_ATTEMPTS: Final = 3
QUEUE_POLL_SECONDS: Final = 120.0


class SmokeFailure(AssertionError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise SmokeFailure(reason)


def contains_key(value: JSONValue, target: str) -> bool:
    if isinstance(value, Mapping):
        return target in value or any(contains_key(item, target) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(contains_key(item, target) for item in value)
    return False


def assert_no_pii(rendered: str, label: str) -> None:
    for marker in PII_MARKERS:
        require(marker not in rendered, f"{label} echoed PII marker {marker!r}")


def assert_no_nonempty_question(value: JSONValue, label: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            require(key != "question" or not item, f"{label} exposed non-empty question")
            assert_no_nonempty_question(item, label)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            assert_no_nonempty_question(item, label)


def run_status(run: Run, label: str) -> str:
    status = run["status"]
    require(status in STATUSES, f"{label} returned undocumented status {status!r}")
    return status


async def wait_turns(client: SmokeClient) -> None:
    thread = await client.threads.create()
    thread_id = thread["thread_id"]
    first = await client.runs.wait(
        thread_id,
        ASSISTANT_ID,
        input={"question": "What is the usual starting dose of Lipitor?"},
        durability="exit",
    )
    first_answer = first.get("answer")
    require(isinstance(first_answer, str) and bool(first_answer), "turn 1 answer is empty")
    require(not contains_key(first, "question"), "turn 1 output contains a question key")
    assert_no_pii(str(first), "turn 1 output")
    print("PASS 1: first thread turn returned a non-empty answer")

    second = await client.runs.wait(
        thread_id,
        ASSISTANT_ID,
        input={"question": "What about the other drug?"},
        durability="exit",
    )
    second_answer = second.get("answer")
    require(isinstance(second_answer, str) and bool(second_answer), "turn 2 answer is empty")
    state = await client.threads.get_state(thread_id)
    messages = state["values"].get("messages")
    require(isinstance(messages, list) and len(messages) == 4, "turn 2 did not return exactly four history messages")
    require(not contains_key(second, "question"), "turn 2 output contains a question key")
    assert_no_pii(str(second), "turn 2 output")
    print("PASS 2: second turn carried history (four messages) without public question/PII")


async def stream_redaction(client: SmokeClient) -> None:
    thread = await client.threads.create()
    nodes: list[str] = []
    stream = client.runs.stream(
        thread["thread_id"],
        ASSISTANT_ID,
        input={"question": "I am John Smith, MRN 12345, what is Lipitor used for?"},
        stream_mode="updates",
        durability="exit",
    )
    async for event in stream:
        assert_no_pii(str(event), "stream event")
        assert_no_nonempty_question(event.data, "stream event")
        if event.event == "updates" and isinstance(event.data, Mapping):
            nodes.extend(str(name) for name in event.data if not str(name).startswith("__"))
    require(bool(nodes), "updates stream returned no node names")
    require(nodes[0] == "safety_gate", f"first streamed node was {nodes[0]!r}")
    require(nodes[-1] == "finalize", f"last streamed node was {nodes[-1]!r}")
    print(f"PASS 3: updates stream redacted every event; nodes={nodes}")


async def observe_queue(client: SmokeClient) -> tuple[Run, Run]:
    for attempt in range(1, QUEUE_ATTEMPTS + 1):
        thread = await client.threads.create()
        thread_id = thread["thread_id"]
        run_a = await client.runs.create(
            thread_id,
            ASSISTANT_ID,
            input={"question": "Compare the uses and common side effects of Lipitor and Metformin."},
            durability="exit",
        )
        run_b = await client.runs.create(
            thread_id,
            ASSISTANT_ID,
            input={"question": "What is Lipitor used for?"},
            multitask_strategy="enqueue",
            durability="exit",
        )
        with anyio.fail_after(QUEUE_POLL_SECONDS):
            while True:
                current_a = await client.runs.get(thread_id, run_a["run_id"])
                current_b = await client.runs.get(thread_id, run_b["run_id"])
                status_a = run_status(current_a, "run A")
                status_b = run_status(current_b, "run B")
                if status_a == "running" and status_b == "pending":
                    print(f"PASS 4: attempt {attempt} observed run A running and run B pending")
                    return current_a, current_b
                if status_a in TERMINAL_STATUSES:
                    await client.runs.cancel(thread_id, run_b["run_id"], wait=True, action="rollback")
                    break
                await anyio.sleep(0.05)
    raise SmokeFailure(f"never observed the queued state in {QUEUE_ATTEMPTS} attempts")


async def cancel_queued_run(client: SmokeClient) -> None:
    run_a, run_b = await observe_queue(client)
    thread_id = run_b["thread_id"]
    await client.runs.cancel(thread_id, run_b["run_id"], wait=True, action="rollback")
    # The documented contract says rollback DELETES the run. The installed
    # in-memory Agent Server (langgraph-api 0.12.6) does not implement deletion
    # for a queued run it never started: the run survives as `interrupted`
    # (verified 2026-08-19; user-approved deviation recorded as a journey
    # finding). Assert the observed behavior; re-assert deletion after a
    # server upgrade.
    cancelled_b = await client.runs.get(thread_id, run_b["run_id"])
    require(
        run_status(cancelled_b, "cancelled run B") in TERMINAL_STATUSES,
        "cancelled queued run B did not reach a terminal status",
    )
    _ = await client.runs.join(run_a["thread_id"], run_a["run_id"])
    completed_a = await client.runs.get(run_a["thread_id"], run_a["run_id"])
    require(run_status(completed_a, "joined run A") == "success", "run A did not finish successfully")
    fresh = await client.runs.wait(
        thread_id,
        ASSISTANT_ID,
        input={"question": "What is Metformin used for?"},
        durability="exit",
    )
    fresh_answer = fresh.get("answer")
    require(isinstance(fresh_answer, str) and bool(fresh_answer), "fresh run after rollback is empty")
    print(
        "PASS 5: rollback cancelled run B "
        f"(terminal status {run_status(cancelled_b, 'run B')}); "
        "run A succeeded; B's thread accepted a fresh run"
    )


async def smoke(client: SmokeClient) -> None:
    await wait_turns(client)
    await stream_redaction(client)
    await cancel_queued_run(client)


def main() -> int:  # noqa: BLE001 - CLI boundary reports any failed live assertion.
    try:
        client_factory: ClientFactory = getattr(langgraph_sdk, "get_client")
        anyio.run(smoke, client_factory(url=SERVER_URL))
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("PASS: all LangGraph SDK smoke checks completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
