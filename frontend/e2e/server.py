#!/usr/bin/env python3
"""Hermetic E2E orchestrator for the coach frontend (todo 15).

Boots the FULL local stack with ZERO external network:

  1. a scripted dependency server (fake OpenAI gateway + Supabase stub +
     LangSmith feedback mirror) on an ephemeral port;
  2. a real ``langgraph dev`` Agent Server on a second ephemeral port running
     the REAL coach graph, with two offline seams applied at import time in a
     generated graph module (the todo-10 / coach_smoke.py pattern):
       - ``gate.GATEWAY``  -> deterministic offline safety classifier;
       - ``rag_relay.child`` -> offline Route-A monograph answer graph;
     Route-B turns and document extraction run through the scripted gateway
     via ``OPENAI_BASE_URL``; reminder cron registration loops back to this
     same server via ``LANGGRAPH_API_URL``;
  3. a production ``next start`` frontend built against the server URL.

Everything is torn down on SIGTERM/SIGINT/exit: process groups are killed and
ports released. A JSON runfile is written for the Playwright suite.

Run: .venv/bin/python frontend/e2e/server.py --runfile <path>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, Final, override
from urllib.parse import urlparse

import httpx

ROOT: Final = Path(__file__).resolve().parents[2]
FRONTEND: Final = ROOT / "frontend"

U1_EMAIL: Final = "u1@nymble.test"
U1_PASSWORD: Final = "u1-password-4e2e"
U2_EMAIL: Final = "u2@nymble.test"
U2_PASSWORD: Final = "u2-password-4e2e"
IDENTITIES: Final = {
    "u1-access-token-4e2e": {"id": "member-u1", "email": U1_EMAIL},
    "u2-access-token-4e2e": {"id": "member-u2", "email": U2_EMAIL},
}
ACCOUNTS: Final = {
    U1_EMAIL: {"password": U1_PASSWORD, "token": "u1-access-token-4e2e"},
    U2_EMAIL: {"password": U2_PASSWORD, "token": "u2-access-token-4e2e"},
}
PLATFORM_KEY: Final = "platform-secret"
INTERNAL_TOKEN: Final = "internal-secret"
SERVICE_KEY: Final = "service-secret"
ANON_KEY: Final = "test-anon-key"

EXTRACT_FIELDS: Final = [
    {"key": "medication", "label": "Medication", "value": "Lipitor",
     "needsReview": False},
    {"key": "dose_time", "label": "Dose time", "value": "Evening",
     "needsReview": True},
]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _next_weekday(target: int) -> date:
    """Next strictly-future date whose weekday() == target (Mon=0)."""
    today = datetime.now(UTC).date()
    return today + timedelta(days=(target - today.weekday()) % 7 or 7)


def next_friday() -> date:
    return _next_weekday(4)


def next_monday_after(day: date) -> date:
    return day + timedelta(days=(0 - day.weekday()) % 7 or 7)


def friday_after(day: date) -> date:
    return day + timedelta(days=(4 - day.weekday()) % 7 or 7)


class FixtureHandler(BaseHTTPRequestHandler):
    """The scripted dependency server: OpenAI gateway + Supabase + LangSmith."""

    tool_call_seq: ClassVar[int] = 0
    feedback_posts: ClassVar[list[dict[str, object]]] = []

    @override
    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _cors(self) -> None:
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-headers", "*")
        self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("access-control-max-age", "86400")
        self.end_headers()

    def _json(self, status: int, payload: object) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("content-length", "0"))
        return self.rfile.read(length) if length else b""

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/auth/v1/user":
            token = self.headers.get("authorization", "").removeprefix("Bearer ")
            identity = IDENTITIES.get(token)
            if identity is not None:
                self._json(200, {"id": identity["id"], "email": identity["email"]})
                return
            self._json(401, {"message": "Invalid API key"})
            return
        if parsed.path == "/feedback":
            # LangSmith list_feedback probe at server startup.
            self._json(200, [])
            return
        if parsed.path == "/e2e/feedback":
            # Hermetic introspection for the Playwright suite: every feedback
            # POST the coach feedback proxy mirrored into this server.
            self._json(200, {"posts": type(self).feedback_posts})
            return
        self._json(404, {"detail": "not found"})

    # ----------------------------------------------------------------- POST
    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        raw = self._read_body()
        if parsed.path == "/auth/v1/token" and "grant_type=password" in (
            parsed.query or ""
        ):
            payload = json.loads(raw or b"{}")
            account = ACCOUNTS.get(str(payload.get("email", "")).lower())
            if account is None or payload.get("password") != account["password"]:
                self._json(
                    400,
                    {"error": "invalid_grant",
                     "error_description": "Invalid login credentials",
                     "msg": "Invalid login credentials"},
                )
                return
            token = account["token"]
            identity = IDENTITIES[token]
            self._json(
                200,
                {
                    "access_token": token,
                    "token_type": "bearer",
                    "expires_in": 3600,
                    "expires_at": int(time.time()) + 3600,
                    "refresh_token": f"refresh-{token}",
                    "user": {"id": identity["id"], "email": identity["email"]},
                },
            )
            return
        if parsed.path == "/auth/v1/logout":
            self.send_response(204)
            self._cors()
            self.end_headers()
            return
        if parsed.path.endswith("/chat/completions"):
            self._json(200, self._completion(json.loads(raw or b"{}")))
            return
        if parsed.path == "/feedback":
            type(self).feedback_posts.append(json.loads(raw or b"{}"))
            self._json(
                200,
                {
                    "id": "00000000-0000-4000-8000-000000000fed",
                    "created_at": "2026-01-01T00:00:00Z",
                    "modified_at": "2026-01-01T00:00:00Z",
                    "key": "member_feedback",
                    "score": (json.loads(raw or b"{}") or {}).get("score"),
                },
            )
            return
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ---------------------------------------------------- scripted gateway
    @staticmethod
    def _envelope_blocks(messages: list[dict[str, object]]) -> list[dict]:
        blocks: list[dict] = []
        for message in messages:
            content = message.get("content")
            if message.get("role") != "tool" or not isinstance(content, str):
                continue
            try:
                candidate = json.loads(content)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(candidate, dict)
                and isinstance(candidate.get("block_id"), str)
                and isinstance(candidate.get("turn_scope_id"), str)
            ):
                blocks.append(candidate)
        return blocks

    @classmethod
    def _tool_call_id(cls) -> str:
        cls.tool_call_seq += 1
        return f"call_{cls.tool_call_seq}"

    @classmethod
    def _call(cls, name: str, args: dict[str, object]) -> dict[str, object]:
        return {
            "id": cls._tool_call_id(),
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }

    @classmethod
    def _completion(cls, body: dict[str, object]) -> dict[str, object]:
        messages = [
            message
            for message in body.get("messages", [])
            if isinstance(message, dict)
        ]
        system_texts = [
            str(message.get("content"))
            for message in messages
            if message.get("role") == "system"
        ]
        if any("Extract candidate health-document fields" in text for text in system_texts):
            return cls._content_completion(
                json.dumps(
                    {"candidateFields": EXTRACT_FIELDS, "sourceLabel": "ignored"}
                )
            )
        tools = body.get("tools")
        if not isinstance(tools, list) or not tools:
            return cls._content_completion("OK.")
        return cls._agent_turn(messages)

    @classmethod
    def _agent_turn(cls, messages: list[dict[str, object]]) -> dict[str, object]:
        user_index = -1
        question = ""
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.get("role") == "user" and isinstance(
                message.get("content"), str
            ):
                user_index = index
                question = str(message["content"]).strip().casefold()
                break
        results_after = [
            message
            for message in messages[user_index + 1 :]
            if message.get("role") == "tool"
        ]
        turn_blocks = cls._envelope_blocks(
            [m for m in messages[user_index + 1 :]]
        )
        all_blocks = cls._envelope_blocks(messages)
        composed_in_turn = any(
            "Composition accepted." in str(message.get("content", ""))
            for message in results_after
        )
        prior_trend_logged = any(
            block.get("block_id") == "trend:weight" for block in all_blocks
        )

        def turn_block(prefix: str) -> dict[str, object] | None:
            return next(
                (
                    block
                    for block in reversed(turn_blocks)
                    if isinstance(block.get("block_id"), str)
                    and str(block["block_id"]).startswith(prefix)
                ),
                None,
            )

        if question == "log my weight":
            if turn_block("trend:weight") is None:
                value = 80 if prior_trend_logged else 82
                return cls._tool_calls_completion(
                    [cls._call("log_metric", {
                        "metric": "weight", "value": value, "unit": "kg",
                    })]
                )
            if not composed_in_turn:
                return cls._tool_calls_completion([cls._call("compose_ui", {
                    "tree": cls._trend_tree(turn_block("trend:weight")),
                })])
            return cls._content_completion("Logged your weight.")

        if question.startswith("schedule my weekly friday check-in"):
            if not results_after:
                return cls._tool_calls_completion([cls._call("change_schedule", {
                    "request": {
                        "action": "add",
                        "date": next_friday().isoformat(),
                        "time": "09:00",
                        "kind": "check-in",
                        "description": "Friday check-in",
                    },
                })])
            return cls._content_completion("Your Friday check-in is on the calendar.")

        if question == "move my friday check-in to monday":
            if not results_after:
                return cls._tool_calls_completion([cls._call("change_schedule", {
                    "request": {
                        "action": "reschedule",
                        "target": "Friday check-in",
                        "destination": {
                            "date": next_monday_after(next_friday()).isoformat(),
                            "time": "09:00",
                        },
                    },
                })])
            return cls._content_completion("Moved your check-in to Monday.")

        if question == "move my check-in back to friday":
            if not results_after:
                return cls._tool_calls_completion([cls._call("change_schedule", {
                    "request": {
                        "action": "reschedule",
                        "target": "Friday check-in",
                        "destination": {
                            "date": friday_after(
                                next_monday_after(next_friday())
                            ).isoformat(),
                            "time": "09:00",
                        },
                    },
                })])
            return cls._content_completion("Moved your check-in back to Friday.")

        if question == "remind me to log my weight every monday":
            if not results_after:
                return cls._tool_calls_completion([cls._call("create_reminder", {
                    "title": "Log my weight",
                    "weekday": "Mon",
                    "time": "08:00",
                })])
            return cls._content_completion("Reminder set for Mondays at 8:00 AM.")

        if question == "pause my log my weight reminder":
            if not results_after:
                return cls._tool_calls_completion([cls._call("edit_reminder", {
                    "target": "Log my weight", "active": False,
                })])
            return cls._content_completion("Paused your reminder.")

        if question == "what's on my calendar this month?":
            if not results_after:
                return cls._tool_calls_completion([cls._call("view_schedule", {
                    "month": datetime.now(UTC).date().strftime("%Y-%m"),
                })])
            calendar = turn_block("calendar:")
            if calendar is None or not composed_in_turn:
                return cls._tool_calls_completion([cls._call("compose_ui", {
                    "tree": cls._calendar_tree(calendar),
                })])
            return cls._content_completion("Here is your month.")

        month_view = re.fullmatch(
            r"what's on my calendar in (\d{4}-\d{2})\?", question
        )
        if month_view is not None:
            month = month_view.group(1)
            if not results_after:
                return cls._tool_calls_completion([cls._call("view_schedule", {
                    "month": month,
                })])
            calendar = turn_block(f"calendar:{month}")
            if calendar is None or not composed_in_turn:
                return cls._tool_calls_completion([cls._call("compose_ui", {
                    "tree": cls._calendar_tree(calendar),
                })])
            return cls._content_completion("Here is your month.")

        return cls._content_completion("Hello from your coach.")

    @staticmethod
    def _ref(envelope: dict[str, object] | None, pointer: str) -> dict[str, object]:
        assert envelope is not None
        return {
            "__ref": {
                "turn_scope_id": envelope["turn_scope_id"],
                "block_id": envelope["block_id"],
                "pointer": pointer,
            }
        }

    @classmethod
    def _trend_tree(cls, envelope: dict[str, object] | None) -> list[dict]:
        props: dict[str, object] = {
            "label": cls._ref(envelope, "/label"),
            "value": cls._ref(envelope, "/value"),
            "unit": cls._ref(envelope, "/unit"),
            "points": cls._ref(envelope, "/points"),
        }
        assert isinstance(envelope, dict)
        if "delta" in envelope["data"]:
            props["delta"] = cls._ref(envelope, "/delta")
            props["deltaGood"] = cls._ref(envelope, "/deltaGood")
        return [{"component": "TrendCard", "props": props}]

    @classmethod
    def _calendar_tree(cls, envelope: dict[str, object] | None) -> list[dict]:
        return [{
            "component": "MiniCalendar",
            "props": {
                "monthLabel": cls._ref(envelope, "/monthLabel"),
                "firstWeekday": cls._ref(envelope, "/firstWeekday"),
                "daysInMonth": cls._ref(envelope, "/daysInMonth"),
                "highlights": cls._ref(envelope, "/highlights"),
            },
        }]

    @staticmethod
    def _content_completion(content: str) -> dict[str, object]:
        return {
            "id": "fixture-completion",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o-mini",
            "choices": [{
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    @classmethod
    def _tool_calls_completion(cls, calls: list[dict[str, object]]) -> dict[str, object]:
        return {
            "id": "fixture-completion",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o-mini",
            "choices": [{
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": calls,
                },
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


E2E_GRAPHS: Final = '''\
"""E2E graph module: the REAL coach graph with two offline seams applied.

Mirrors scripts/coach_smoke.py: the safety classifier is a deterministic
in-process gateway and Route A relays to an offline answer graph, while every
Route-B agent turn and the document extraction still run through the scripted
OpenAI gateway via OPENAI_BASE_URL.
"""

from __future__ import annotations

from healthcare_rag.agent import gate as gate_module
from healthcare_rag.agent import rag_relay as relay_module
from healthcare_rag.agent.build import build_coach_graph
from healthcare_rag.graph.state import GraphInput, GraphOutput, RAGState
from healthcare_rag.models.safety import SafetyAssessment
from langgraph.graph import END, START, StateGraph


async def _offline_answer(state: RAGState) -> RAGState:
    return {
        "answer": f"Offline monograph answer for: {state.get('question', '')}",
        "follow_ups": ["What else does the monograph cover?"],
        "safety": {"contains_phi": False, "short_circuited": False},
        "error": None,
    }


async def _classify(**_kwargs: str) -> SafetyAssessment:
    return SafetyAssessment(
        category="in_scope_informational",
        contains_phi=False,
        phi_spans=[],
        drug_mentioned="lipitor",
        rationale="offline e2e classifier",
    )


_child_builder = StateGraph(
    RAGState, input_schema=GraphInput, output_schema=GraphOutput
)
_child_builder.add_node("offline_answer", _offline_answer)
_child_builder.add_edge(START, "offline_answer")
_child_builder.add_edge("offline_answer", END)
relay_module.child = _child_builder.compile(checkpointer=True, name="offline_rag")
gate_module.GATEWAY = _classify

import os as _os
if _os.getenv("E2E_DEBUG_LOG"):
    import traceback as _tb
    from collections.abc import Mapping as _Mapping

    from healthcare_rag.agent import build as _build_module
    from healthcare_rag.agent import coach_agent as _coach_agent_module

    _orig = _coach_agent_module.coach_agent

    async def _debug_coach_agent(state, config, *, store):
        try:
            return await _orig(state, config, store=store)
        except BaseException as exc:
            with open(_os.environ["E2E_DEBUG_LOG"], "a") as fh:
                fh.write("coach_agent raised %r" % (exc,))
                _tb.print_exc(file=fh)
                cfg = config.get("configurable", {})
                fh.write("configurable keys: %r" % sorted(cfg.keys()))
                auth = cfg.get("langgraph_auth_user")
                fh.write("auth user: %r mapping=%s" % (
                    auth, isinstance(auth, _Mapping) if auth is not None else "n/a",
                ))
            raise

    _build_module.coach_agent = _debug_coach_agent

coach = build_coach_graph().compile(name="coach")
'''

SOCKET_GUARD: Final = '''\
import os, socket
_original_connect = socket.socket.connect
def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else ''
    if host not in {'127.0.0.1', '::1', 'localhost'}:
        raise PermissionError(f'external socket blocked: {host}')
    return _original_connect(self, address)
socket.socket.connect = _guarded_connect
try:
    socket.create_connection(('8.8.8.8', 443), timeout=0.01)
except PermissionError:
    open(os.environ['COACH_SOCKET_GUARD_MARKER'], 'w').write('active')
else:
    raise RuntimeError('socket guard negative probe failed')
'''


def _wait_http(url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1.0, follow_redirects=True).status_code < 500:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runfile", required=True)
    args = parser.parse_args()
    runfile = Path(args.runfile)

    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix="coach-e2e-", dir=runfile.parent))
    logs = workdir / "logs"
    logs.mkdir(exist_ok=True)

    dep_port = _free_port()
    server_port = _free_port()
    frontend_port = _free_port()
    dep_url = f"http://127.0.0.1:{dep_port}"
    server_url = f"http://127.0.0.1:{server_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"

    dependency_server = ThreadingHTTPServer(("127.0.0.1", dep_port), FixtureHandler)
    dependency_thread = threading.Thread(
        target=dependency_server.serve_forever, daemon=True
    )
    dependency_thread.start()

    graph_path = workdir / "e2e_graphs.py"
    graph_path.write_text(E2E_GRAPHS)
    guard_path = workdir / "sitecustomize.py"
    marker_path = workdir / "socket-guard.active"
    guard_path.write_text(SOCKET_GUARD)

    environment = os.environ | {
        "OPENAI_API_KEY": "fixture-openai",
        "OPENAI_BASE_URL": f"{dep_url}/v1",
        "OPENAI_API_BASE": f"{dep_url}/v1",
        "LANGSMITH_API_KEY": PLATFORM_KEY,
        "LANGSMITH_ENDPOINT": dep_url,
        "LANGSMITH_TRACING": "false",
        "SUPABASE_URL": dep_url,
        "SUPABASE_SERVICE_KEY": SERVICE_KEY,
        "COACH_INTERNAL_TOKEN": INTERNAL_TOKEN,
        "COACH_ALLOWED_ORIGINS": frontend_url,
        "CORS_ALLOW_ORIGINS": frontend_url,
        "HC_RAG_LLM_MODEL": "gpt-4o-mini",
        "HC_RAG_VALIDATOR_MODEL": "gpt-4o-mini",
        "LANGSMITH_FEEDBACK_PROJECT_ID": (
            "00000000-0000-4000-8000-000000000fee"
        ),
        "COACH_SOCKET_GUARD_MARKER": str(marker_path),
        "LANGGRAPH_API_URL": server_url,
        "PYTHONPATH": f"{workdir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
    }

    config = json.loads((ROOT / "langgraph.json").read_text())
    config["dependencies"] = [str(ROOT)]
    config["graphs"] = {"coach": f"{graph_path}:coach"}
    config["auth"]["path"] = f"{ROOT}/healthcare_rag/agent/auth.py:auth"
    config["http"]["app"] = f"{ROOT}/healthcare_rag/agent/http_app.py:app"
    config.pop("store", None)  # offline: no embedding-backed store index
    config.pop("env", None)
    config_path = workdir / "langgraph.e2e.json"
    config_path.write_text(json.dumps(config))

    children: list[subprocess.Popen] = []

    def teardown(*_args: object) -> None:
        for process in children:
            if process.poll() is not None:
                continue
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
        for process in children:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        try:
            runfile.unlink(missing_ok=True)
        finally:
            dependency_server.shutdown()
            dependency_server.server_close()
            dependency_thread.join(timeout=5)

    server_log = (logs / "langgraph-dev.log").open("wb")
    server_proc = subprocess.Popen(
        [
            str(ROOT / ".venv/bin/langgraph"),
            "dev",
            "--no-browser",
            "--no-reload",
            "--config",
            str(config_path),
            "--port",
            str(server_port),
            "--server-log-level",
            os.getenv("COACH_E2E_LOG_LEVEL", "error"),
        ],
        cwd=workdir,
        env=environment,
        start_new_session=True,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    children.append(server_proc)
    if server_proc.poll() is not None or not _wait_http(f"{server_url}/ok", 60):
        teardown()
        print((logs / "langgraph-dev.log").read_text(errors="replace"), file=sys.stderr)
        return 1
    if marker_path.read_text() != "active":
        teardown()
        print("socket guard did not activate", file=sys.stderr)
        return 1

    bun = "bun"
    build_env = os.environ | {
        "NEXT_PUBLIC_LANGGRAPH_URL": server_url,
        "NEXT_PUBLIC_SUPABASE_URL": dep_url,
        "NEXT_PUBLIC_SUPABASE_ANON_KEY": ANON_KEY,
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    build_log = (logs / "next-build.log").open("wb")
    build_proc = subprocess.Popen(
        [bun, "run", "build"],
        cwd=FRONTEND,
        env=build_env,
        start_new_session=True,
        stdout=build_log,
        stderr=subprocess.STDOUT,
    )
    if build_proc.wait(timeout=420) != 0:
        teardown()
        print((logs / "next-build.log").read_text(errors="replace"), file=sys.stderr)
        return 1

    frontend_log = (logs / "next-start.log").open("wb")
    frontend_proc = subprocess.Popen(
        [bun, "run", "start"],
        cwd=FRONTEND,
        env=build_env | {"PORT": str(frontend_port)},
        start_new_session=True,
        stdout=frontend_log,
        stderr=subprocess.STDOUT,
    )
    children.append(frontend_proc)
    if frontend_proc.poll() is not None or not _wait_http(frontend_url + "/login", 90):
        teardown()
        print((logs / "next-start.log").read_text(errors="replace"), file=sys.stderr)
        return 1

    runfile.write_text(
        json.dumps(
            {
                "ready": True,
                "dep_url": dep_url,
                "server_url": server_url,
                "frontend_url": frontend_url,
                "u1": {
                    "email": U1_EMAIL,
                    "password": U1_PASSWORD,
                    "token": "u1-access-token-4e2e",
                    "user_id": "member-u1",
                },
                "u2": {
                    "email": U2_EMAIL,
                    "password": U2_PASSWORD,
                    "token": "u2-access-token-4e2e",
                    "user_id": "member-u2",
                },
                "internal": {"api_key": PLATFORM_KEY, "token": INTERNAL_TOKEN},
                "anon_key": ANON_KEY,
            },
            indent=2,
        )
    )
    print(f"e2e stack ready: frontend={frontend_url} server={server_url}")

    stop = threading.Event()

    def _signal(*_args: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)
    try:
        stop.wait()
    finally:
        teardown()
        for handle in (server_log, frontend_log):
            handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
