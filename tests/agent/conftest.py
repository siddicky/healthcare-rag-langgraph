from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, Final

import httpx
import pytest

ROOT: Final = Path(__file__).parents[2]


class _FixtureHandler(BaseHTTPRequestHandler):
    feedback_requests: ClassVar[list[tuple[dict[str, str], dict[str, object]]]] = []
    extraction_calls: ClassVar[int] = 0

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: int, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path == "/auth/v1/user":
            authorization = self.headers.get("authorization", "")
            identity = authorization.removeprefix("Bearer ")
            if identity in {"member-a", "member-b"}:
                self._json(200, {"id": identity})
                return
        self._json(401, {"detail": "Unauthorized"})

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(length)
        if self.path.endswith("/chat/completions"):
            type(self).extraction_calls += 1
            self._json(
                200,
                {
                    "id": "fixture-completion",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "candidateFields": [
                                            {
                                                "key": "medication",
                                                "label": "Medication",
                                                "value": "Lipitor",
                                                "needsReview": False,
                                            }
                                        ],
                                        "sourceLabel": "ignored",
                                    }
                                ),
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )
            return
        if self.path.endswith("/feedback"):
            payload = json.loads(raw_body) if raw_body else {}
            self.feedback_requests.append((dict(self.headers), payload))
            self._json(
                200,
                {
                    "id": "00000000-0000-4000-8000-000000000fed",
                    "created_at": "2026-01-01T00:00:00Z",
                    "modified_at": "2026-01-01T00:00:00Z",
                    "key": "member_feedback",
                    "score": payload.get("score"),
                },
            )
            return
        self.send_response(204)
        self.end_headers()


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture(scope="session")
def agent_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    dependency_server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    dependency_thread = threading.Thread(
        target=dependency_server.serve_forever,
        daemon=True,
    )
    dependency_thread.start()
    dependency_url = f"http://127.0.0.1:{dependency_server.server_port}"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = tmp_path_factory.mktemp("agent-server") / "server.log"
    graph_path = log_path.parent / "fixture_graphs.py"
    graph_path.write_text(
        "from typing import Annotated, TypedDict\n"
        "from langchain_core.messages import AIMessage, AnyMessage\n"
        "from langgraph.graph import END, START, StateGraph, add_messages\n\n"
        "class State(TypedDict, total=False):\n"
        "    messages: Annotated[list[AnyMessage], add_messages]\n\n"
        "def respond(state: State) -> State:\n"
        "    return {'messages': [AIMessage(content='fixture response')]}\n\n"
        "builder = StateGraph(State)\n"
        "builder.add_node('respond', respond)\n"
        "builder.add_edge(START, 'respond')\n"
        "builder.add_edge('respond', END)\n"
        "coach = builder.compile()\n"
        "graph = coach\n"
    )
    guard_path = log_path.parent / "sitecustomize.py"
    marker_path = log_path.parent / "socket-guard.active"
    guard_path.write_text(
        "import os, socket\n"
        "_original_connect = socket.socket.connect\n"
        "def _guarded_connect(self, address):\n"
        "    host = address[0] if isinstance(address, tuple) else ''\n"
        "    if host not in {'127.0.0.1', '::1', 'localhost'}:\n"
        "        raise PermissionError(f'external socket blocked: {host}')\n"
        "    return _original_connect(self, address)\n"
        "socket.socket.connect = _guarded_connect\n"
        "try:\n"
        "    socket.create_connection(('8.8.8.8', 443), timeout=0.01)\n"
        "except PermissionError:\n"
        "    open(os.environ['COACH_SOCKET_GUARD_MARKER'], 'w').write('active')\n"
        "else:\n"
        "    raise RuntimeError('socket guard negative probe failed')\n"
    )
    environment = os.environ | {
        "OPENAI_API_KEY": "fixture-openai",
        "OPENAI_BASE_URL": f"{dependency_url}/v1",
        "OPENAI_API_BASE": f"{dependency_url}/v1",
        "LANGSMITH_API_KEY": "platform-secret",
        "LANGSMITH_ENDPOINT": dependency_url,
        "LANGSMITH_TRACING": "false",
        "SUPABASE_URL": dependency_url,
        "SUPABASE_SERVICE_KEY": "service-secret",
        "COACH_INTERNAL_TOKEN": "internal-secret",
        "COACH_ALLOWED_ORIGINS": "https://coach.test",
        "CORS_ALLOW_ORIGINS": "https://coach.test",
        "HC_RAG_LLM_MODEL": "gpt-4o-mini",
        "HC_RAG_VALIDATOR_MODEL": "gpt-4o-mini",
        "LANGSMITH_FEEDBACK_PROJECT_ID": "00000000-0000-4000-8000-000000000fee",
        "COACH_SOCKET_GUARD_MARKER": str(marker_path),
        "PYTHONPATH": f"{log_path.parent}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
    }
    config = json.loads((ROOT / "langgraph.json").read_text())
    config["dependencies"] = [str(ROOT)]
    config["graphs"] = {
        "healthcare_rag": f"{graph_path}:graph",
        "coach": f"{graph_path}:coach",
    }
    config["auth"]["path"] = f"{ROOT}/healthcare_rag/agent/auth.py:auth"
    config["http"]["app"] = f"{ROOT}/healthcare_rag/agent/http_app.py:app"
    config["env"] = {
        key: environment[key]
        for key in (
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_API_BASE",
            "LANGSMITH_API_KEY",
            "LANGSMITH_ENDPOINT",
            "LANGSMITH_TRACING",
            "SUPABASE_URL",
            "SUPABASE_SERVICE_KEY",
            "COACH_INTERNAL_TOKEN",
            "COACH_ALLOWED_ORIGINS",
            "CORS_ALLOW_ORIGINS",
            "HC_RAG_LLM_MODEL",
            "HC_RAG_VALIDATOR_MODEL",
            "LANGSMITH_FEEDBACK_PROJECT_ID",
            "COACH_SOCKET_GUARD_MARKER",
        )
    }
    config_path = log_path.parent / "langgraph.fixture.json"
    config_path.write_text(json.dumps(config))
    with log_path.open("wb") as server_log:
        process = subprocess.Popen(
            [
                str(ROOT / ".venv/bin/langgraph"),
                "dev",
                "--no-browser",
                "--no-reload",
                "--config",
                str(config_path),
                "--port",
                str(port),
                "--server-log-level",
                "error",
            ],
            cwd=log_path.parent,
            env=environment,
            start_new_session=True,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(log_path.read_text(errors="replace"))
            try:
                if httpx.get(f"{base_url}/ok", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                continue
        else:
            process.terminate()
            pytest.fail(log_path.read_text(errors="replace"))
        try:
            assert marker_path.read_text() == "active"
            yield base_url
        finally:
            os.killpg(process.pid, 15)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, 9)
                process.wait(timeout=5)
    dependency_server.shutdown()
    dependency_server.server_close()
    dependency_thread.join(timeout=5)


@pytest.fixture
def member_headers() -> dict[str, str]:
    return {"authorization": "Bearer member-a"}


@pytest.fixture
def feedback_requests() -> Iterator[list[tuple[dict[str, str], dict[str, object]]]]:
    _FixtureHandler.feedback_requests.clear()
    yield _FixtureHandler.feedback_requests


@pytest.fixture
def extraction_call_count() -> Iterator[Callable[[], int]]:
    initial = _FixtureHandler.extraction_calls
    yield lambda: _FixtureHandler.extraction_calls - initial
