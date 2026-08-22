from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from typing import ClassVar, Final, Literal, Protocol, TypeAlias, override
from uuid import UUID, uuid5

from anyio import to_thread
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.store.base import BaseStore
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from starlette.requests import Request

from healthcare_rag.graph.resources import get as get_resources
from healthcare_rag.processors.safety import scrub_phi

from .memory import authenticated_user_id, sanitize_memory_field
from .state import CoachState
from .store_data import (
    OpRecord,
    StoreNamespaceError,
    get_op,
    get_upload_registry,
    put_op,
    put_op_if_absent,
)

MAX_UPLOAD_BYTES: Final = 10 * 1024 * 1024
MAX_MULTIPART_OVERHEAD: Final = 64 * 1024
ALLOWED_MIME: Final = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}
RESERVATION_NS: Final = UUID("6f503f96-3957-4c48-8554-32beec98fca2")
DOCUMENT_UNAVAILABLE: Final = (
    "This document is no longer available. Please upload it again."
)
PRIVACY_DROP_NOTICE: Final = "Privacy checks failed; field was not saved."
DocumentTarget: TypeAlias = Literal["review_document", "__end__"]


class CandidateField(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    key: str
    label: str
    value: str
    needsReview: bool


class DocumentProposal(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    candidateFields: list[CandidateField] = Field(default_factory=list)
    sourceLabel: str = ""


class MemoryExtractionPayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    sourceLabel: str
    fields: list[CandidateField]


class ResumeField(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    key: str
    value: str


class DocumentDecision(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    accept: bool
    fields: list[ResumeField] | None = None


class ResolvedField(CandidateField):
    status: Literal["saved", "discarded"]
    notice: str | None = None


class MemoryExtractionResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    sourceLabel: str
    fields: list[ResolvedField]


class ThreadContext(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    thread_id: str


@dataclass(frozen=True, slots=True)
class MultipartUpload:
    upload_id: str
    thread_id: str
    mime_type: str
    extension: str
    content: bytearray


@dataclass(frozen=True, slots=True)
class UploadRejected(Exception):
    reason: str
    status_code: int = 400

    @override
    def __str__(self) -> str:
        return self.reason


class DocumentExtractor(Protocol):
    def __call__(
        self, content: bytes, mime_type: str
    ) -> Awaitable[DocumentProposal]: ...


def _disposition_parameters(value: str) -> dict[str, str]:
    parameters: dict[str, str] = {}
    for item in value.split(";")[1:]:
        key, separator, raw = item.strip().partition("=")
        if separator:
            parameters[key.lower()] = raw.strip().strip('"')
    return parameters


async def read_multipart_upload(request: Request) -> MultipartUpload:
    content_type = request.headers.get("content-type", "")
    media_type, separator, boundary_value = content_type.partition("boundary=")
    if not separator or not media_type.lower().startswith("multipart/form-data"):
        raise UploadRejected("multipart/form-data is required")
    boundary = boundary_value.strip().strip('"').encode("ascii", "strict")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD:
            raw.clear()
            raise UploadRejected("Upload is too large", status_code=413)

    marker = b"--" + boundary
    fields: dict[str, str] = {}
    file_parts: list[tuple[str, str, bytes]] = []
    for part in bytes(raw).split(marker)[1:]:
        trimmed = part.strip(b"\r\n-")
        if not trimmed:
            continue
        header_block, delimiter, payload = trimmed.partition(b"\r\n\r\n")
        if not delimiter:
            raise UploadRejected("Malformed multipart payload")
        headers: dict[str, str] = {}
        for line in header_block.decode("latin-1").split("\r\n"):
            name, colon, value = line.partition(":")
            if colon:
                headers[name.lower()] = value.strip()
        parameters = _disposition_parameters(headers.get("content-disposition", ""))
        name = parameters.get("name")
        if name is None:
            raise UploadRejected("Multipart field name is required")
        filename = parameters.get("filename")
        value_bytes = payload.removesuffix(b"\r\n")
        if filename is None:
            if name in fields:
                raise UploadRejected("Duplicate multipart field")
            fields[name] = value_bytes.decode("utf-8")
        else:
            file_parts.append((filename, headers.get("content-type", ""), value_bytes))
    raw.clear()
    if set(fields) != {"upload_id", "thread_id"} or len(file_parts) != 1:
        raise UploadRejected("Expected upload_id, thread_id, and one file")
    try:
        upload_id = str(UUID(fields["upload_id"]))
        thread_id = str(UUID(fields["thread_id"]))
    except ValueError:
        raise UploadRejected("upload_id and thread_id must be UUIDs") from None
    filename, mime_type, content = file_parts[0]
    expected_extension = ALLOWED_MIME.get(mime_type)
    extension = PurePath(filename).suffix.lower()
    if expected_extension is None:
        raise UploadRejected("Unsupported file type", status_code=415)
    valid_extensions = (
        {expected_extension, ".jpeg"}
        if mime_type == "image/jpeg"
        else {expected_extension}
    )
    if extension not in valid_extensions:
        raise UploadRejected(
            "Filename extension does not match file type", status_code=415
        )
    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadRejected("Upload is too large", status_code=413)
    return MultipartUpload(
        upload_id=upload_id,
        thread_id=thread_id,
        mime_type=mime_type,
        extension=extension,
        content=bytearray(content),
    )


async def _extract_document(content: bytes, mime_type: str) -> DocumentProposal:
    encoded = base64.b64encode(content).decode("ascii")
    content_type = "file" if mime_type == "application/pdf" else "image"
    message_block = {
        "type": content_type,
        "base64": encoded,
        "mime_type": mime_type,
    }
    model = get_resources().gateway.chat_model("default")
    structured = model.with_structured_output(DocumentProposal, method="json_schema")
    result = await structured.ainvoke(
        [
            SystemMessage(
                content="Extract candidate health-document fields. Mark uncertain values for review."
            ),
            HumanMessage(content=[message_block]),
        ]
    )
    if not isinstance(result, DocumentProposal):
        raise UploadRejected("Document extraction failed", status_code=502)
    return result


DOCUMENT_EXTRACTOR: DocumentExtractor = _extract_document


def scrub_proposal(
    proposal: DocumentProposal, extension: str, size: int
) -> dict[str, JsonValue]:
    candidate_fields: list[JsonValue] = [
        {
            "key": scrub_phi(field.key)[0],
            "label": scrub_phi(field.label)[0],
            "value": scrub_phi(field.value)[0],
            "needsReview": field.needsReview,
        }
        for field in proposal.candidateFields
    ]
    return {
        "candidateFields": candidate_fields,
        "sourceLabel": f"{extension} · {size} bytes",
    }


def reservation_id(upload_id: str) -> str:
    return str(uuid5(RESERVATION_NS, upload_id))


def _unavailable() -> Command[DocumentTarget]:
    return Command[DocumentTarget](
        update={
            "messages": [AIMessage(content=DOCUMENT_UNAVAILABLE)],
            "follow_ups": [],
        },
        goto="__end__",
    )


async def claim_document(
    state: CoachState,
    config: RunnableConfig,
    store: BaseStore,
) -> Command[DocumentTarget]:
    user_id = authenticated_user_id(config)
    thread_id = ThreadContext.model_validate(
        config.get("configurable", {})
    ).thread_id
    attachment_id = state.get("attachment_id")
    if not attachment_id or not thread_id:
        return _unavailable()
    try:
        record = await get_upload_registry(
            store, user_id, reservation_id(attachment_id)
        )
    except StoreNamespaceError:
        return _unavailable()
    if (
        record is None
        or record.status != "done"
        or record.consumed
        or record.intended_thread != thread_id
        or record.proposal is None
    ):
        return _unavailable()
    proposal = DocumentProposal.model_validate(record.proposal)
    canonical = json.dumps(
        {
            "user_id": user_id,
            "thread_id": thread_id,
            "attachment_id": attachment_id,
            "proposal": proposal.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    op_id = hashlib.sha256(canonical).hexdigest()
    payload = MemoryExtractionPayload(
        sourceLabel=proposal.sourceLabel,
        fields=proposal.candidateFields,
    ).model_dump(mode="json")
    _ = await put_op_if_absent(
        store,
        user_id,
        OpRecord(
            op_id=op_id,
            status="pending",
            result=None,
            created_ts=datetime.now(UTC),
            resolved_entry_id=None,
            frozen_request={
                "attachment_id": attachment_id,
                "proposal": proposal.model_dump(mode="json"),
            },
            interrupt_payload=payload,
        ),
    )
    return Command(
        update={"pending_document_op_id": op_id, "attachment_id": None},
        goto="review_document",
    )


def _confirmation(result: JsonValue) -> AIMessage:
    return AIMessage(
        content=json.dumps(
            {"component": "MemoryExtractionCard", "data": result},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


async def review_document(
    state: CoachState,
    config: RunnableConfig,
    store: BaseStore,
) -> CoachState:
    user_id = authenticated_user_id(config)
    op_id = state.get("pending_document_op_id")
    if not op_id:
        return {"messages": [AIMessage(content=DOCUMENT_UNAVAILABLE)], "follow_ups": []}
    locator = await get_op(store, user_id, op_id)
    if locator is None:
        return {
            "messages": [AIMessage(content=DOCUMENT_UNAVAILABLE)],
            "follow_ups": [],
            "pending_document_op_id": None,
        }
    attachment_id = locator.frozen_request.get("attachment_id")
    if isinstance(attachment_id, str):
        registry_namespace = ("users", user_id, "upload_registry")
        registry_key = reservation_id(attachment_id)
        if await store.aget(registry_namespace, registry_key) is not None:
            await store.adelete(registry_namespace, registry_key)
    op = await get_op(store, user_id, op_id)
    if op is None:
        return {
            "messages": [AIMessage(content=DOCUMENT_UNAVAILABLE)],
            "follow_ups": [],
            "pending_document_op_id": None,
        }
    if op.status != "pending":
        return {
            "messages": [_confirmation(op.result)],
            "follow_ups": [],
            "pending_document_op_id": None,
        }
    payload = MemoryExtractionPayload.model_validate(op.interrupt_payload)
    decision = DocumentDecision.model_validate(interrupt(op.interrupt_payload))
    edits = {field.key: field.value for field in decision.fields or []}
    resolved_fields: list[JsonValue] = []
    for index, field in enumerate(payload.fields):
        value = edits.get(field.key, field.value)
        clean = (
            await to_thread.run_sync(sanitize_memory_field, value)
            if decision.accept
            else None
        )
        saved = clean is not None
        if saved:
            memory_id = hashlib.sha256(
                f"{op_id}\x00{index}\x00{field.key}".encode()
            ).hexdigest()
            await store.aput(
                ("users", user_id, "profile"),
                memory_id,
                {"fact": clean, "kind": "profile"},
                index=False,
            )
        resolved: dict[str, JsonValue] = {
            **field.model_dump(mode="json"),
            "value": clean if saved else field.value,
            "status": "saved" if saved else "discarded",
        }
        if decision.accept and not saved:
            resolved["notice"] = PRIVACY_DROP_NOTICE
        resolved_fields.append(resolved)
    result = MemoryExtractionResult.model_validate(
        {"sourceLabel": payload.sourceLabel, "fields": resolved_fields}
    ).model_dump(mode="json", exclude_none=True)
    terminal = op.model_copy(
        update={
            "status": "applied" if decision.accept else "declined",
            "result": result,
        }
    )
    await put_op(store, user_id, terminal)
    return {
        "messages": [_confirmation(result)],
        "follow_ups": [],
        "pending_document_op_id": None,
    }


__all__ = [
    "ALLOWED_MIME",
    "DOCUMENT_EXTRACTOR",
    "RESERVATION_NS",
    "DocumentProposal",
    "MultipartUpload",
    "UploadRejected",
    "claim_document",
    "read_multipart_upload",
    "reservation_id",
    "review_document",
    "scrub_proposal",
]
