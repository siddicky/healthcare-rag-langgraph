from __future__ import annotations

import base64
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import PurePath
from typing import ClassVar, Final, Protocol, override
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from starlette.requests import Request

from healthcare_rag.graph.resources import get as get_resources
from healthcare_rag.processors.safety import scrub_phi

MAX_UPLOAD_BYTES: Final = 10 * 1024 * 1024
MAX_MULTIPART_OVERHEAD: Final = 64 * 1024
ALLOWED_MIME: Final = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}


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


__all__ = [
    "ALLOWED_MIME",
    "DOCUMENT_EXTRACTOR",
    "DocumentProposal",
    "MultipartUpload",
    "UploadRejected",
    "read_multipart_upload",
    "scrub_proposal",
]
