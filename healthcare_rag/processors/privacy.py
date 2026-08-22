from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from threading import Condition, RLock
from typing import Any, Final, Protocol, final

import httpx
from anyio import to_thread
from typing_extensions import override

from healthcare_rag.processors.privacy_patterns import (
    clinical_code_intervals,
    deterministic_hits,
)

MAX_INPUT_BYTES: Final = 16 * 1024
DEFAULT_SCORE_THRESHOLD: Final = 0.40
MODEL_NAME: Final = "en_core_web_sm"
MODEL_VERSION: Final = "3.8.0"
ANALYZER_VERSION: Final = "2.2.364"
SPACY_VERSION: Final = "3.8.15"

# The privacy engine (presidio + spaCy) runs in services/privacy and is reached
# over HTTP. These two variables are the only configuration; both are required.
SERVICE_URL_ENV: Final = "PRIVACY_SERVICE_URL"
SERVICE_TOKEN_ENV: Final = "PRIVACY_SERVICE_TOKEN"
SERVICE_TIMEOUT_ENV: Final = "PRIVACY_SERVICE_TIMEOUT_S"
DEFAULT_TIMEOUT_S: Final = 10.0

ENTITY_TYPES: Final[tuple[str, ...]] = (
    "CA_SIN", "CREDIT_CARD", "CRYPTO", "IBAN_CODE", "IP_ADDRESS",
    "MAC_ADDRESS", "MEDICAL_LICENSE", "PHONE_NUMBER", "PERSON", "URL",
    "US_BANK_NUMBER", "US_ITIN", "US_DRIVER_LICENSE", "US_MBI", "US_NPI",
    "US_PASSPORT", "US_SSN",
)
_SENTINEL_TEXT: Final = "Alice Johnson used 192.168.10.42."
_SENTINEL_ENTITIES: Final[frozenset[str]] = frozenset({"PERSON", "IP_ADDRESS"})


class Readiness(StrEnum):
    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY = "ready"
    FAILED = "failed"


@final
class PrivacyScanError(Exception):
    __slots__: tuple[str, ...] = ("code",)
    code: str

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    @override
    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class RedactSpan:
    start: int
    end: int
    kind: str
    source: str


@dataclass(frozen=True, slots=True)
class PrivacyScan:
    text: str
    kinds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalyzerResult:
    start: int
    end: int
    entity_type: str


class PrivacyScanner(Protocol):
    def scan(self, text: str) -> PrivacyScan: ...


async def ascan(scanner: PrivacyScanner, text: str) -> PrivacyScan:
    """Scan from async code. The scan is a network call, so it runs in a worker thread."""
    return await to_thread.run_sync(scanner.scan, text)


class PrivacyBackend(Protocol):
    """Where presidio actually runs. ``validate`` is the readiness gate."""

    def validate(self) -> None: ...

    def analyze(self, text: str) -> list[AnalyzerResult]: ...


def union_spans(text_length: int, spans: list[RedactSpan]) -> list[RedactSpan]:
    for span in spans:
        if not 0 <= span.start < span.end <= text_length:
            raise PrivacyScanError("PRIVACY_INVALID_SPAN")
    ordered = sorted(spans, key=lambda span: (span.start, span.end))
    merged: list[RedactSpan] = []
    component_kinds: set[str] = set()
    for span in ordered:
        if not merged or span.start > merged[-1].end:
            merged.append(span)
            component_kinds = {span.kind}
            continue
        previous = merged[-1]
        component_kinds.add(span.kind)
        kind = previous.kind if len(component_kinds) == 1 else "IDENTIFIER"
        merged[-1] = RedactSpan(
            previous.start,
            max(previous.end, span.end),
            kind,
            "union",
        )
    return merged


@final
class RemotePresidioBackend:
    """httpx client for services/privacy. Raw-free: only codes leave this class."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.Client | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not base_url or not token:
            raise PrivacyScanError("PRIVACY_SERVICE_UNCONFIGURED")
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.Client(timeout=timeout_s)

    @classmethod
    def from_env(cls, client: httpx.Client | None = None) -> RemotePresidioBackend:
        timeout = os.getenv(SERVICE_TIMEOUT_ENV)
        return cls(
            os.getenv(SERVICE_URL_ENV, ""),
            os.getenv(SERVICE_TOKEN_ENV, ""),
            client=client,
            timeout_s=float(timeout) if timeout else DEFAULT_TIMEOUT_S,
        )

    def validate(self) -> None:
        health = self._request("GET", "/health", None, "PRIVACY_INITIALIZATION_FAILED")
        if (
            health.get("analyzer_version") != ANALYZER_VERSION
            or health.get("spacy_version") != SPACY_VERSION
        ):
            raise PrivacyScanError("PRIVACY_VERSION_MISMATCH")
        if health.get("model_name") != MODEL_NAME or health.get("model_version") != MODEL_VERSION:
            raise PrivacyScanError("PRIVACY_MODEL_MISMATCH")
        inventory = health.get("entities")
        if not isinstance(inventory, list) or not set(ENTITY_TYPES) <= set(inventory):
            raise PrivacyScanError("PRIVACY_INVENTORY_MISMATCH")
        sentinels = self._analyze(_SENTINEL_TEXT, sorted(_SENTINEL_ENTITIES), "PRIVACY_SENTINEL_FAILED")
        if not {result.entity_type for result in sentinels} >= _SENTINEL_ENTITIES:
            raise PrivacyScanError("PRIVACY_SENTINEL_FAILED")

    def analyze(self, text: str) -> list[AnalyzerResult]:
        return self._analyze(text, list(ENTITY_TYPES), "PRIVACY_SCAN_FAILED")

    def _analyze(self, text: str, entities: list[str], code: str) -> list[AnalyzerResult]:
        body = {"text": text, "entities": entities, "score_threshold": DEFAULT_SCORE_THRESHOLD}
        payload = self._request("POST", "/analyze", body, code)
        results = payload.get("results")
        if not isinstance(results, list):
            raise PrivacyScanError(code)
        try:
            return [
                AnalyzerResult(int(item["start"]), int(item["end"]), str(item["entity_type"]))
                for item in results
            ]
        except (KeyError, TypeError, ValueError):
            raise PrivacyScanError(code) from None

    def _request(
        self, method: str, path: str, json: dict[str, Any] | None, code: str
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method, f"{self._base_url}{path}", json=json, headers=self._headers
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
            raise PrivacyScanError(code) from None
        if not isinstance(payload, dict):
            raise PrivacyScanError(code)
        return payload


@final
class PrivacySanitizer:
    """Process-owned, fail-closed recognized-identifier scanner."""

    def __init__(self, backend: PrivacyBackend | None = None) -> None:
        self._condition = Condition(RLock())
        self._readiness = Readiness.UNINITIALIZED
        self._backend: PrivacyBackend | None = backend
        self._ready_backend: PrivacyBackend | None = None

    @property
    def readiness(self) -> Readiness:
        with self._condition:
            return self._readiness

    def initialize(self) -> None:
        with self._condition:
            while self._readiness is Readiness.INITIALIZING:
                _ = self._condition.wait()
            if self._readiness is Readiness.READY:
                return
            if self._readiness is Readiness.FAILED:
                raise PrivacyScanError("PRIVACY_NOT_READY")
            self._readiness = Readiness.INITIALIZING
        try:
            backend = self._build_backend()
            backend.validate()
        except PrivacyScanError:
            with self._condition:
                self._readiness = Readiness.FAILED
                self._condition.notify_all()
            raise
        except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
            with self._condition:
                self._readiness = Readiness.FAILED
                self._condition.notify_all()
            raise PrivacyScanError("PRIVACY_INITIALIZATION_FAILED") from None
        with self._condition:
            self._ready_backend = backend
            self._readiness = Readiness.READY
            self._condition.notify_all()

    def scan(self, text: str) -> PrivacyScan:
        if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
            raise PrivacyScanError("PRIVACY_INPUT_TOO_LARGE")
        self.initialize()
        backend = self._ready_backend
        if backend is None:
            raise PrivacyScanError("PRIVACY_NOT_READY")
        results = backend.analyze(text)
        preserve = clinical_code_intervals(text)
        candidates = [
            RedactSpan(hit.start, hit.end, hit.kind, "deterministic")
            for hit in deterministic_hits(text)
        ]
        candidates.extend(
            RedactSpan(result.start, result.end, result.entity_type, "presidio")
            for result in results
            if not any(result.start < end and start < result.end for start, end in preserve)
            and self._presidio_result_allowed(text, result)
        )
        spans = union_spans(len(text), candidates)
        ordered_candidates = sorted(
            candidates,
            key=lambda candidate: (candidate.start, candidate.end, candidate.kind),
        )
        kinds = tuple(dict.fromkeys(candidate.kind for candidate in ordered_candidates))
        clean = text
        for span in reversed(spans):
            clean = f"{clean[:span.start]}[REDACTED_{span.kind}]{clean[span.end:]}"
        return PrivacyScan(clean, kinds)

    async def ascan(self, text: str) -> PrivacyScan:
        return await ascan(self, text)

    @staticmethod
    def _presidio_result_allowed(text: str, result: AnalyzerResult) -> bool:
        if result.entity_type != "PERSON" or " " in text[result.start:result.end].strip():
            return True
        prefix = text[max(0, result.start - 24):result.start].lower()
        cues = ("name is ", "patient ", "pt. ", "dr. ", "doctor ", "provider ")
        return any(cue in prefix for cue in cues)

    def _build_backend(self) -> PrivacyBackend:
        return self._backend if self._backend is not None else RemotePresidioBackend.from_env()
