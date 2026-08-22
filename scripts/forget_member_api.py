from __future__ import annotations

# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false
import socket
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol, Self, TypeAlias, final, override

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

JSONValue: TypeAlias = JsonValue
ERASE_MARKER_NAME: Final = "erase_confirmation_v1"
ERASE_QUESTION: Final = "Delete all my saved data."
RUN_ENVELOPE: Final[dict[str, JSONValue]] = {
    "assistant_id": "coach",
    "stream_mode": ["updates"],
    "stream_subgraphs": False,
    "stream_resumable": False,
    "durability": "exit",
    "if_not_exists": "reject",
    "multitask_strategy": "reject",
}


@dataclass(frozen=True, slots=True)
class ConfigurationError(RuntimeError):
    variable: str

    @override
    def __str__(self) -> str:
        return f"missing required environment variable: {self.variable}"


@dataclass(frozen=True, slots=True)
class ErasureFailed(RuntimeError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class DeleteFailed(RuntimeError):
    thread_id: str
    status_code: int

    @override
    def __str__(self) -> str:
        return f"thread deletion failed for {self.thread_id} ({self.status_code})"


class MemberAPI(Protocol):
    def search_threads(self, limit: int, offset: int) -> list[str]: ...

    def thread_status(self, thread_id: str) -> str: ...

    def delete_thread(self, thread_id: str) -> None: ...


@final
class ThreadCreated(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    thread_id: str


@final
class ThreadStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    status: str


@final
class ThreadItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    thread_id: str


@final
class AuthToken(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    access_token: str


def has_erase_marker(value: JSONValue) -> bool:
    if isinstance(value, Mapping):
        if value.get("name") == ERASE_MARKER_NAME and value.get("type") in {
            "ai",
            "AIMessage",
        }:
            return True
        return any(has_erase_marker(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(has_erase_marker(item) for item in value)
    return False


@final
class HTTPMemberAPI:
    def __init__(self, url: str, token: str) -> None:
        transport = httpx.HTTPTransport(
            http2=True,
            retries=3,
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=20,
                keepalive_expiry=30.0,
            ),
            socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
        )
        self._client: httpx.Client = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"authorization": f"Bearer {token}"},
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0),
            follow_redirects=True,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self._client.close()

    @staticmethod
    def _json(response: httpx.Response) -> JSONValue:
        _ = response.raise_for_status()
        return TypeAdapter(JsonValue).validate_json(response.content)

    def create_thread(self) -> str:
        return ThreadCreated.model_validate(
            self._json(self._client.post("/threads", json={}))
        ).thread_id

    def erase_turn(self, thread_id: str) -> tuple[bool, bool]:
        payload = {**RUN_ENVELOPE, "input": {"question": ERASE_QUESTION}}
        marker = False
        try:
            with self._client.stream(
                "POST", f"/threads/{thread_id}/runs/stream", json=payload
            ) as response:
                _ = response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith("data:"):
                        parsed: JSONValue = TypeAdapter(JsonValue).validate_json(
                            line.removeprefix("data:").strip()
                        )
                        marker = marker or has_erase_marker(parsed)
            return True, marker
        except (httpx.HTTPError, ValueError):
            return False, marker

    def thread_state(self, thread_id: str) -> JSONValue:
        return self._json(self._client.get(f"/threads/{thread_id}/state"))

    def thread_status(self, thread_id: str) -> str:
        return ThreadStatus.model_validate(
            self._json(self._client.get(f"/threads/{thread_id}"))
        ).status

    def search_threads(self, limit: int, offset: int) -> list[str]:
        payload = self._json(
            self._client.post(
                "/threads/search",
                json={
                    "select": ["thread_id"],
                    "limit": limit,
                    "offset": offset,
                    "sort_by": "thread_id",
                    "sort_order": "asc",
                },
            )
        )
        raw = payload.get("items", []) if isinstance(payload, dict) else payload
        if not isinstance(raw, list):
            raise ErasureFailed("thread search returned a non-list response")
        return [
            item.thread_id
            for item in TypeAdapter(list[ThreadItem]).validate_python(raw)
        ]

    def delete_thread(self, thread_id: str) -> None:
        response = self._client.delete(f"/threads/{thread_id}")
        if response.status_code not in {204, 404}:
            raise DeleteFailed(thread_id=thread_id, status_code=response.status_code)


def _required(environment: Mapping[str, str], name: str, fallback: str = "") -> str:
    value = environment.get(name, "") or environment.get(fallback, "")
    if not value:
        raise ConfigurationError(name)
    return value


def sign_in(environment: Mapping[str, str]) -> str:
    url = _required(environment, "SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL")
    response = httpx.post(
        f"{url.rstrip('/')}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": _required(environment, "NEXT_PUBLIC_SUPABASE_ANON_KEY")},
        json={
            "email": _required(environment, "COACH_MEMBER_EMAIL"),
            "password": _required(environment, "COACH_MEMBER_PASSWORD"),
        },
        timeout=10.0,
    )
    _ = response.raise_for_status()
    return AuthToken.model_validate_json(response.content).access_token
