from __future__ import annotations

import socket
import sys
import threading
import time
from types import ModuleType
from uuid import uuid4

import httpx
import pytest
import uvicorn
from langgraph_sdk import Auth

from server.config import ServerConfig


class Principal(Auth.types.MinimalUserDict, total=False):
    role: str


def _auth() -> Auth:
    auth = Auth()

    @auth.authenticate
    async def authenticate(
        method: str,
        path: str,
        headers: dict[bytes, bytes],
        authorization: str | None,
    ) -> Principal:
        del method, path
        if b"x-api-key" in headers and b"x-internal-token" in headers:
            return {"identity": "internal", "role": "internal"}
        if authorization == "Bearer member":
            return {"identity": "member-a", "role": "member"}
        raise Auth.exceptions.HTTPException(status_code=401)

    @auth.on
    async def allow(
        ctx: Auth.types.AuthContext, value: Auth.types.on.value
    ) -> Auth.types.HandlerResult:
        del ctx, value

    return auth


def _config() -> ServerConfig:
    return ServerConfig(
        graphs={},
        auth_path="fixture:auth",
        http_app="./healthcare_rag/agent/http_app.py:app",
        http_flags={},
        store_index={},
        api_version="0.12.6",
    )


def verify_upload_reservation_uses_shared_shim_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import healthcare_rag.agent.http_app as custom
    import server.app as app_module
    from healthcare_rag.agent import uploads
    from healthcare_rag.agent.documents import DocumentProposal
    from server import _compat

    async def extract(_content: bytes, _mime: str) -> DocumentProposal:
        return DocumentProposal()

    monkeypatch.setattr(app_module, "load_auth_instance", lambda _path: _auth())
    monkeypatch.setattr(custom, "validate_feedback_project", lambda: "fixture")
    monkeypatch.setattr(uploads, "DOCUMENT_EXTRACTOR", extract)
    monkeypatch.setenv("LANGSMITH_API_KEY", "platform-secret")
    monkeypatch.setenv("COACH_INTERNAL_TOKEN", "internal-secret")
    original = {
        name: module
        for name, module in sys.modules.items()
        if name in {"langgraph_api", "langgraph_api.store"}
    }
    for name in ("langgraph_api", "langgraph_api.store"):
        sys.modules.pop(name, None)

    def missing(name: str) -> ModuleType:
        raise ModuleNotFoundError(name=name)

    monkeypatch.setattr(_compat, "import_module", missing)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(app_module.create_app(_config()), log_level="error")
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
            member = {"authorization": "Bearer member"}
            thread_id = client.post("/threads", headers=member, json={}).json()[
                "thread_id"
            ]
            upload_id = str(uuid4())
            uploaded = client.post(
                "/coach/uploads",
                headers=member,
                data={"upload_id": upload_id, "thread_id": thread_id},
                files={
                    "file": ("fixture.png", b"\x89PNG\r\n\x1a\nfixture", "image/png")
                },
            )
            status = client.get(f"/coach/uploads/{upload_id}/status", headers=member)
        assert uploaded.status_code == 201, uploaded.text
        assert status.json() == {"stage": "done"}
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        for name in ("langgraph_api", "langgraph_api.store"):
            sys.modules.pop(name, None)
        sys.modules.update(original)
    assert not thread.is_alive()
