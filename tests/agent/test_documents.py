from __future__ import annotations

from typing import Final
from uuid import UUID

import pytest
from starlette.requests import Request

BOUNDARY: Final = "coach-boundary"
UPLOAD_ID: Final = "00000000-0000-0000-0000-000000000010"
THREAD_ID: Final = "00000000-0000-0000-0000-000000000020"


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
