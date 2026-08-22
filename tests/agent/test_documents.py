from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Final, Self, override
from uuid import UUID

import httpx
import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command
from pydantic import JsonValue
from starlette.authentication import BaseUser
from starlette.requests import Request

BOUNDARY: Final = "coach-boundary"
UPLOAD_ID: Final = "00000000-0000-0000-0000-000000000010"
THREAD_ID: Final = "00000000-0000-0000-0000-000000000020"
USER_ID: Final = "member-1"


def _multipart(*, mime_type: str, filename: str, files: int = 1) -> bytes:
    parts = [
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="upload_id"\r\n\r\n{UPLOAD_ID}\r\n',
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="thread_id"\r\n\r\n{THREAD_ID}\r\n',
    ]
    for index in range(files):
        parts.append(
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="{index}-{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        )
        parts.append("\x89PNG\r\n\x1a\ncontent\r\n")
    parts.append(f"--{BOUNDARY}--\r\n")
    return "".join(parts).encode("latin-1")


def _request(body: bytes) -> Request:
    delivered = False

    async def receive() -> dict[str, object]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/coach/uploads",
            "headers": [
                (
                    b"content-type",
                    f"multipart/form-data; boundary={BOUNDARY}".encode(),
                )
            ],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_streaming_parser_accepts_exact_fields_and_one_supported_file() -> None:
    from healthcare_rag.agent.documents import read_multipart_upload

    upload = await read_multipart_upload(
        _request(_multipart(mime_type="image/png", filename="document.png"))
    )

    assert UUID(upload.upload_id) == UUID(UPLOAD_ID)
    assert UUID(upload.thread_id) == UUID(THREAD_ID)
    assert upload.mime_type == "image/png"
    assert upload.extension == ".png"
    assert upload.content.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_streaming_parser_rejects_multiple_files() -> None:
    from healthcare_rag.agent.documents import UploadRejected, read_multipart_upload

    with pytest.raises(UploadRejected):
        await read_multipart_upload(
            _request(
                _multipart(mime_type="image/png", filename="document.png", files=2)
            )
        )


@pytest.mark.asyncio
async def test_streaming_parser_rejects_executable_extension() -> None:
    from healthcare_rag.agent.documents import UploadRejected, read_multipart_upload

    with pytest.raises(UploadRejected) as raised:
        await read_multipart_upload(
            _request(_multipart(mime_type="image/png", filename="document.exe"))
        )

    assert raised.value.status_code == 415


def test_upload_parser_never_uses_framework_multipart_spooling() -> None:
    import inspect

    from healthcare_rag.agent.documents import read_multipart_upload

    source = inspect.getsource(read_multipart_upload)
    assert "request.form" not in source
    assert "UploadFile" not in source


def _config(thread_id: str = THREAD_ID) -> RunnableConfig:
    return {
        "configurable": {
            "thread_id": thread_id,
            "langgraph_auth_user": {"identity": USER_ID, "role": "member"},
        }
    }


async def _seed_registry(
    store: InMemoryStore,
    *,
    intended_thread: str = THREAD_ID,
    owner: str = USER_ID,
    expires_at: datetime | None = None,
    consumed: bool = False,
) -> None:
    from healthcare_rag.agent.store_data import (
        UploadRegistryRecord,
        put_upload_registry,
    )
    from healthcare_rag.agent.uploads import reservation_id

    record = UploadRegistryRecord(
        owner=owner,
        intended_thread=intended_thread,
        expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=15),
        status="done",
        consumed=consumed,
        proposal={
            "sourceLabel": ".pdf · 128 bytes",
            "candidateFields": [
                {
                    "key": "goalWeight",
                    "label": "Goal weight",
                    "value": "180 lb",
                    "needsReview": True,
                },
                {
                    "key": "note",
                    "label": "Member note",
                    "value": "keep me",
                    "needsReview": False,
                },
            ],
        },
    )
    if owner == USER_ID:
        await put_upload_registry(store, USER_ID, reservation_id(UPLOAD_ID), record)
        return
    await store.aput(
        ("users", USER_ID, "upload_registry"),
        reservation_id(UPLOAD_ID),
        record.model_dump(mode="json"),
        index=False,
    )


@pytest.mark.asyncio
async def test_claim_commits_before_review_consumes_and_accepts_scrubbed_edits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from healthcare_rag.agent import documents
    from healthcare_rag.agent.build import build_coach_graph
    from healthcare_rag.agent.documents import MemoryExtractionResult
    from healthcare_rag.agent.store_data import get_op
    from healthcare_rag.agent.uploads import reservation_id

    store = InMemoryStore()
    await _seed_registry(store)
    monkeypatch.setattr(
        documents,
        "sanitize_memory_field",
        lambda value: None if value == "drop me" else value.replace("Alice", "[PERSON]"),
    )
    graph = build_coach_graph().compile(checkpointer=InMemorySaver(), store=store)

    await graph.ainvoke(
        {"question": "Please review this document.", "attachment_id": UPLOAD_ID},
        _config(),
        interrupt_after=["claim_document"],
    )
    claimed = await graph.aget_state(_config())
    op_id = claimed.values["pending_document_op_id"]

    assert isinstance(op_id, str)
    assert claimed.values.get("attachment_id") is None
    assert await store.aget(
        ("users", USER_ID, "upload_registry"), reservation_id(UPLOAD_ID)
    ) is not None

    await graph.ainvoke(None, _config())
    interrupted = await graph.aget_state(_config())
    payload = interrupted.interrupts[0].value

    assert payload == {
        "sourceLabel": ".pdf · 128 bytes",
        "fields": [
            {
                "key": "goalWeight",
                "label": "Goal weight",
                "value": "180 lb",
                "needsReview": True,
            },
            {
                "key": "note",
                "label": "Member note",
                "value": "keep me",
                "needsReview": False,
            },
        ],
    }
    assert await store.aget(
        ("users", USER_ID, "upload_registry"), reservation_id(UPLOAD_ID)
    ) is None

    result = await graph.ainvoke(
        Command(
            resume={
                "accept": True,
                "fields": [
                    {"key": "goalWeight", "value": "Alice 175 lb"},
                    {"key": "note", "value": "drop me"},
                ],
            }
        ),
        _config(),
    )
    memories = await store.asearch(("users", USER_ID, "profile"), limit=10)
    terminal = await get_op(store, USER_ID, op_id)

    assert [item.value["fact"] for item in memories] == ["[PERSON] 175 lb"]
    assert terminal is not None
    assert terminal.status == "applied"
    confirmation = MemoryExtractionResult.model_validate(terminal.result)
    assert confirmation.fields[0].status == "saved"
    assert confirmation.fields[1].status == "discarded"
    assert confirmation.fields[1].notice == "Privacy checks failed; field was not saved."
    assert result["follow_ups"] == []
    assert (await graph.aget_state(_config())).values["pending_document_op_id"] is None


@pytest.mark.asyncio
async def test_document_review_discard_stores_nothing() -> None:
    from healthcare_rag.agent.build import build_coach_graph
    from healthcare_rag.agent.documents import MemoryExtractionResult
    from healthcare_rag.agent.store_data import get_op

    store = InMemoryStore()
    await _seed_registry(store)
    graph = build_coach_graph().compile(checkpointer=InMemorySaver(), store=store)

    await graph.ainvoke(
        {"question": "Please review this document.", "attachment_id": UPLOAD_ID},
        _config(),
    )
    interrupted = await graph.aget_state(_config())
    op_id = interrupted.values["pending_document_op_id"]
    await graph.ainvoke(Command(resume={"accept": False}), _config())

    terminal = await get_op(store, USER_ID, op_id)
    assert await store.asearch(("users", USER_ID, "profile"), limit=10) == []
    assert terminal is not None
    assert terminal.status == "declined"
    confirmation = MemoryExtractionResult.model_validate(terminal.result)
    assert all(field.status == "discarded" for field in confirmation.fields)


@pytest.mark.asyncio
async def test_crash_before_claim_commit_reclaims_with_one_idempotent_op() -> None:
    from healthcare_rag.agent.build import build_coach_graph
    from healthcare_rag.agent.documents import claim_document
    from healthcare_rag.agent.state import CoachState
    from healthcare_rag.agent.uploads import reservation_id

    class PlannedCrash(RuntimeError):
        pass

    store = InMemoryStore()
    await _seed_registry(store)

    async def crash_after_side_effects(state: CoachState, config: RunnableConfig) -> None:
        _ = await claim_document(state, config, store=store)
        raise PlannedCrash

    builder = StateGraph(CoachState)
    builder.add_node("claim", crash_after_side_effects)
    builder.add_edge(START, "claim")
    builder.add_edge("claim", END)
    crashing = builder.compile(checkpointer=InMemorySaver(), store=store)
    with pytest.raises(PlannedCrash):
        await crashing.ainvoke({"attachment_id": UPLOAD_ID}, _config())

    assert await store.aget(
        ("users", USER_ID, "upload_registry"), reservation_id(UPLOAD_ID)
    ) is not None
    first_ops = await store.asearch(("users", USER_ID, "ops"), limit=10)
    graph = build_coach_graph().compile(checkpointer=InMemorySaver(), store=store)
    await graph.ainvoke(
        {"question": "Please review this document.", "attachment_id": UPLOAD_ID},
        _config(),
        interrupt_after=["claim_document"],
    )
    second_ops = await store.asearch(("users", USER_ID, "ops"), limit=10)

    assert len(first_ops) == len(second_ops) == 1
    assert first_ops[0].key == second_ops[0].key


@pytest.mark.asyncio
async def test_review_replays_persisted_payload_after_registry_consumption() -> None:
    from healthcare_rag.agent.build import build_coach_graph

    store = InMemoryStore()
    await _seed_registry(store)
    graph = build_coach_graph().compile(checkpointer=InMemorySaver(), store=store)
    await graph.ainvoke(
        {"question": "Please review this document.", "attachment_id": UPLOAD_ID},
        _config(),
    )
    first = (await graph.aget_state(_config())).interrupts[0].value
    first_bytes = json.dumps(first, sort_keys=True, separators=(",", ":")).encode()

    await graph.ainvoke(None, _config())
    replay = (await graph.aget_state(_config())).interrupts[0].value
    replay_bytes = json.dumps(replay, sort_keys=True, separators=(",", ":")).encode()

    assert replay_bytes == first_bytes


@pytest.mark.asyncio
async def test_resume_replay_has_exactly_once_memory_outcome() -> None:
    from healthcare_rag.agent.build import build_coach_graph

    store = InMemoryStore()
    await _seed_registry(store)
    graph = build_coach_graph().compile(checkpointer=InMemorySaver(), store=store)
    await graph.ainvoke(
        {"question": "Please review this document.", "attachment_id": UPLOAD_ID},
        _config(),
    )
    resume = Command(resume={"accept": True})
    await graph.ainvoke(resume, _config())
    await graph.ainvoke(resume, _config())

    memories = await store.asearch(("users", USER_ID, "profile"), limit=10)
    assert len(memories) == 2


@pytest.mark.parametrize(
    ("intended_thread", "owner", "expires_at", "consumed"),
    [
        ("other-thread", USER_ID, None, False),
        (THREAD_ID, USER_ID, None, True),
        (THREAD_ID, "other-user", None, False),
        (THREAD_ID, USER_ID, datetime(2000, 1, 1, tzinfo=UTC), False),
    ],
)
@pytest.mark.asyncio
async def test_invalid_attachment_records_fail_closed_with_reupload_message(
    intended_thread: str,
    owner: str,
    expires_at: datetime | None,
    consumed: bool,
) -> None:
    from healthcare_rag.agent.build import build_coach_graph

    store = InMemoryStore()
    await _seed_registry(
        store,
        intended_thread=intended_thread,
        owner=owner,
        expires_at=expires_at,
        consumed=consumed,
    )
    graph = build_coach_graph().compile(checkpointer=InMemorySaver(), store=store)

    result = await graph.ainvoke(
        {"question": "Please review this document.", "attachment_id": UPLOAD_ID},
        _config(),
    )

    assert result["messages"][-1].content == "This document is no longer available. Please upload it again."
    assert await store.asearch(("users", USER_ID, "ops"), limit=10) == []


def test_agent_package_has_no_persistent_raw_byte_write_path() -> None:
    agent_dir = Path(__file__).parents[2] / "healthcare_rag" / "agent"
    permitted = {"documents.py", "uploads.py"}
    forbidden = ("open(", ".write_bytes(", ".write_text(", "tempfile", "UploadFile")

    violations = {
        path.name: token
        for path in agent_dir.glob("*.py")
        if path.name not in permitted
        for token in forbidden
        if token in path.read_text()
    }

    assert violations == {}


@pytest.mark.asyncio
async def test_upload_buffer_is_released_in_finally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from healthcare_rag.agent import uploads
    from healthcare_rag.agent.documents import DocumentProposal, MultipartUpload

    buffer = bytearray(b"%PDF-sensitive")
    upload = MultipartUpload(
        upload_id=UPLOAD_ID,
        thread_id=THREAD_ID,
        mime_type="application/pdf",
        extension=".pdf",
        content=buffer,
    )
    persisted: list[dict[str, JsonValue]] = []

    async def put_record(
        _namespace: tuple[str, ...],
        _key: str,
        _value: dict[str, JsonValue],
        *,
        index: bool,
        ttl: float,
    ) -> None:
        del index, ttl
        persisted.append(dict(_value))

    store = SimpleNamespace(aput=put_record)

    async def read_upload(_request: Request) -> MultipartUpload:
        return upload

    async def member_get(_request: Request, _path: str) -> httpx.Response:
        return httpx.Response(200)

    async def extract(_content: bytes, _mime: str) -> DocumentProposal:
        raise RuntimeError("planned extraction failure")

    class FakeClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, _path: str, **_kwargs: object) -> httpx.Response:
            return httpx.Response(200)

    class TestUser(BaseUser):
        @property
        @override
        def is_authenticated(self) -> bool:
            return True

        @property
        @override
        def display_name(self) -> str:
            return USER_ID

        @property
        @override
        def identity(self) -> str:
            return USER_ID

    monkeypatch.setattr(uploads, "read_multipart_upload", read_upload)
    monkeypatch.setattr(uploads, "_member_get", member_get)
    monkeypatch.setattr(uploads, "DOCUMENT_EXTRACTOR", extract)
    monkeypatch.setattr(uploads.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    monkeypatch.setitem(
        sys.modules,
        "langgraph_api.store",
        SimpleNamespace(get_store=lambda: _async_value(store)),
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/coach/uploads",
            "headers": [],
            "user": TestUser(),
        }
    )

    response = await uploads.post_upload(request)

    assert response.status_code == 502
    assert buffer == bytearray()
    assert "%PDF-sensitive" not in json.dumps(persisted)


async def _async_value(value: object) -> object:
    return value


@pytest.mark.asyncio
async def test_claim_proceeds_when_perimeter_already_admitted_the_upload() -> None:
    """The perimeter marks `admitted` before the graph runs; claim must accept it.

    This pins the handoff between _consume_attachment (admission marker,
    re-send stays a perimeter 403) and claim_document (which still rejects
    `consumed` records): a first review send must reach the interrupt.
    """
    # Given
    from healthcare_rag.agent.build import build_coach_graph
    from healthcare_rag.agent.store_data import put_upload_registry
    from healthcare_rag.agent.uploads import reservation_id

    store = InMemoryStore()
    await _seed_registry(store)
    namespace = ("users", USER_ID, "upload_registry")
    item = await store.aget(namespace, reservation_id(UPLOAD_ID))
    assert item is not None
    await store.aput(
        namespace,
        reservation_id(UPLOAD_ID),
        {**item.value, "admitted": True},
    )
    graph = build_coach_graph().compile(checkpointer=InMemorySaver(), store=store)

    # When
    result = await graph.ainvoke(
        {"question": "Please review this document.", "attachment_id": UPLOAD_ID},
        _config(),
    )

    # Then
    assert "no longer available" not in str(result["messages"][-1].content)
    ops = await store.asearch(("users", USER_ID, "ops"), limit=10)
    assert len(ops) == 1
