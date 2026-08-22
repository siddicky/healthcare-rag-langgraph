from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Final, final, override

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)

from .store_data import Weekday

_WEEKDAY_NUMBER: Final[dict[Weekday, int]] = {
    Weekday.MON: 1,
    Weekday.TUE: 2,
    Weekday.WED: 3,
    Weekday.THU: 4,
    Weekday.FRI: 5,
    Weekday.SAT: 6,
    Weekday.SUN: 0,
}


class Cron(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    cron_id: str
    thread_id: str
    schedule: str
    timezone: str | None = None
    enabled: bool
    next_run_date: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class CronCreate(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    reminder_id: str
    user_id: str
    thread_id: str
    wake_token: str
    weekday: Weekday
    time: str
    timezone: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class CronAPIError(Exception):
    operation: str
    status_code: int | None = None

    @override
    def __str__(self) -> str:
        return f"cron API {self.operation} failed"


@dataclass(frozen=True, slots=True)
class CronAmbiguousError(Exception):
    operation: str

    @override
    def __str__(self) -> str:
        return f"cron API {self.operation} outcome is ambiguous"


def cron_expression(weekday: Weekday, time: str) -> str:
    """Map ``Mon 09:00`` to standard five-field cron ``0 9 * * 1``."""
    hour, minute = time.split(":", maxsplit=1)
    return f"{int(minute)} {int(hour)} * * {_WEEKDAY_NUMBER[weekday]}"


@final
class CronClient:
    """Call Agent Server with both platform and internal-principal credentials."""

    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        api_key: str,
        internal_token: str,
        page_size: int = 100,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._internal_token = internal_token
        self._page_size = page_size

    def _headers(self, owner: str | None = None) -> dict[str, str]:
        headers = {
            "x-api-key": self._api_key,
            "x-internal-token": self._internal_token,
        }
        if owner is not None:
            headers["x-internal-owner"] = owner
        return headers

    @staticmethod
    def _payload(spec: CronCreate) -> dict[str, JsonValue]:
        wake: dict[str, JsonValue] = {
            "reminder_id": spec.reminder_id,
            "user_id": spec.user_id,
            "thread_id": spec.thread_id,
            "wake_token": spec.wake_token,
        }
        return {
            "schedule": cron_expression(spec.weekday, spec.time),
            "timezone": spec.timezone,
            "assistant_id": "coach",
            "input": {"cron_wake": wake},
            "metadata": {
                "reminder_id": spec.reminder_id,
                "user_id": spec.user_id,
            },
            "enabled": spec.enabled,
            "multitask_strategy": "enqueue",
        }

    async def create(self, spec: CronCreate) -> Cron:
        try:
            response = await self._http.post(
                f"/threads/{spec.thread_id}/runs/crons",
                headers=self._headers(spec.user_id),
                json=self._payload(spec),
            )
        except httpx.RequestError as error:
            raise CronAmbiguousError("create") from error
        return self._cron_response(response, "create")

    async def update(self, cron_id: str, spec: CronCreate) -> Cron:
        payload = self._payload(spec)
        _ = payload.pop("assistant_id")
        _ = payload.pop("multitask_strategy")
        try:
            response = await self._http.patch(
                f"/runs/crons/{cron_id}",
                headers=self._headers(spec.user_id),
                json=payload,
            )
        except httpx.RequestError as error:
            raise CronAmbiguousError("update") from error
        return self._cron_response(response, "update")

    async def delete(self, cron_id: str, owner: str) -> None:
        try:
            response = await self._http.delete(
                f"/runs/crons/{cron_id}", headers=self._headers(owner)
            )
        except httpx.RequestError as error:
            raise CronAmbiguousError("delete") from error
        if response.status_code not in {200, 204, 404}:
            raise CronAPIError("delete", response.status_code)

    async def search(self, *, metadata: Mapping[str, str], owner: str) -> list[Cron]:
        crons: list[Cron] = []
        offset = 0
        while True:
            try:
                response = await self._http.post(
                    "/runs/crons/search",
                    headers=self._headers(owner),
                    json={
                        "metadata": dict(metadata),
                        "limit": self._page_size,
                        "offset": offset,
                        "sort_by": "cron_id",
                        "sort_order": "asc",
                    },
                )
            except httpx.RequestError as error:
                raise CronAPIError("search") from error
            page = self._cron_page(response)
            crons.extend(page)
            if len(page) < self._page_size:
                return crons
            offset += self._page_size

    async def search_reservations(self, owner: str) -> list[str]:
        thread_ids: list[str] = []
        offset = 0
        while True:
            try:
                response = await self._http.post(
                    "/threads/search",
                    headers=self._headers(),
                    json={
                        "metadata": {
                            "resource_kind": "upload_reservation",
                            "owner": owner,
                        },
                        "limit": self._page_size,
                        "offset": offset,
                        "sort_by": "thread_id",
                        "sort_order": "asc",
                    },
                )
            except httpx.RequestError as error:
                raise CronAPIError("reservation search") from error
            payload = self._json(response, "reservation search")
            raw_items = (
                payload.get("items", []) if isinstance(payload, dict) else payload
            )
            if not isinstance(raw_items, list):
                raise CronAPIError("reservation search", response.status_code)
            page: list[str] = []
            for item in raw_items:
                if isinstance(item, dict):
                    thread_id = item.get("thread_id")
                    if isinstance(thread_id, str):
                        page.append(thread_id)
            thread_ids.extend(page)
            if len(raw_items) < self._page_size:
                return thread_ids
            offset += self._page_size

    async def delete_reservation(self, thread_id: str) -> None:
        try:
            response = await self._http.delete(
                f"/threads/{thread_id}", headers=self._headers()
            )
        except httpx.RequestError as error:
            raise CronAPIError("reservation delete") from error
        if response.status_code not in {200, 204, 404}:
            raise CronAPIError("reservation delete", response.status_code)

    @staticmethod
    def _json(response: httpx.Response, operation: str) -> JsonValue:
        if response.status_code != 200:
            raise CronAPIError(operation, response.status_code)
        try:
            payload: JsonValue = TypeAdapter(JsonValue).validate_json(response.content)
            return payload
        except (ValueError, ValidationError) as error:
            raise CronAPIError(operation, response.status_code) from error

    @classmethod
    def _cron_response(cls, response: httpx.Response, operation: str) -> Cron:
        payload = cls._json(response, operation)
        try:
            return Cron.model_validate(payload)
        except ValidationError as error:
            raise CronAPIError(operation, response.status_code) from error

    @classmethod
    def _cron_page(cls, response: httpx.Response) -> list[Cron]:
        payload = cls._json(response, "search")
        raw_items = payload.get("items", []) if isinstance(payload, dict) else payload
        if not isinstance(raw_items, list):
            raise CronAPIError("search", response.status_code)
        try:
            return TypeAdapter(list[Cron]).validate_python(raw_items)
        except ValidationError as error:
            raise CronAPIError("search", response.status_code) from error


__all__ = [
    "Cron",
    "CronAPIError",
    "CronAmbiguousError",
    "CronClient",
    "CronCreate",
    "cron_expression",
]
