from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from importlib import metadata
from threading import Condition, RLock
from typing import Final, Protocol, final, override

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
from presidio_analyzer.context_aware_enhancers import LemmaContextAwareEnhancer
from presidio_analyzer.nlp_engine import NerModelConfiguration, SpacyNlpEngine
from presidio_analyzer.predefined_recognizers import (
    CaSinRecognizer,
    CreditCardRecognizer,
    CryptoRecognizer,
    IbanRecognizer,
    IpRecognizer,
    MacAddressRecognizer,
    MedicalLicenseRecognizer,
    PhoneRecognizer,
    SpacyRecognizer,
    UrlRecognizer,
    UsBankRecognizer,
    UsItinRecognizer,
    UsLicenseRecognizer,
    UsMbiRecognizer,
    UsNpiRecognizer,
    UsPassportRecognizer,
    UsSsnRecognizer,
)

from healthcare_rag.processors.privacy_patterns import clinical_code_intervals, deterministic_hits

MAX_INPUT_BYTES: Final = 16 * 1024
DEFAULT_SCORE_THRESHOLD: Final = 0.40
MODEL_NAME: Final = "en_core_web_sm"
MODEL_VERSION: Final = "3.8.0"
ANALYZER_VERSION: Final = "2.2.364"

ENTITY_TYPES: Final[tuple[str, ...]] = (
    "CA_SIN", "CREDIT_CARD", "CRYPTO", "IBAN_CODE", "IP_ADDRESS",
    "MAC_ADDRESS", "MEDICAL_LICENSE", "PHONE_NUMBER", "PERSON", "URL",
    "US_BANK_NUMBER", "US_ITIN", "US_DRIVER_LICENSE", "US_MBI", "US_NPI",
    "US_PASSPORT", "US_SSN",
)
_SPACY_IGNORED_LABELS: Final[tuple[str, ...]] = (
    "CARDINAL", "DATE", "EVENT", "FAC", "GPE", "LANGUAGE", "LAW", "LOC",
    "MONEY", "NORP", "ORDINAL", "ORG", "PERCENT", "PRODUCT", "QUANTITY",
    "TIME", "WORK_OF_ART",
)


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


class AnalyzerResult(Protocol):
    start: int
    end: int
    entity_type: str


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
class PrivacySanitizer:
    """Process-owned, fail-closed recognized-identifier scanner."""

    def __init__(self) -> None:
        self._condition = Condition(RLock())
        self._readiness = Readiness.UNINITIALIZED
        self._analyzer: AnalyzerEngine | None = None

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
            analyzer = self._build_analyzer()
            self._validate(analyzer)
        except PrivacyScanError:
            with self._condition:
                self._readiness = Readiness.FAILED
                self._condition.notify_all()
            raise
        except Exception:  # noqa: BROAD_EXCEPT_OK - raw-free third-party boundary.
            with self._condition:
                self._readiness = Readiness.FAILED
                self._condition.notify_all()
            raise PrivacyScanError("PRIVACY_INITIALIZATION_FAILED") from None
        with self._condition:
            self._analyzer = analyzer
            self._readiness = Readiness.READY
            self._condition.notify_all()

    def scan(self, text: str) -> PrivacyScan:
        if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
            raise PrivacyScanError("PRIVACY_INPUT_TOO_LARGE")
        self.initialize()
        analyzer = self._analyzer
        if analyzer is None:
            raise PrivacyScanError("PRIVACY_NOT_READY")
        try:
            results = analyzer.analyze(
                text=text,
                language="en",
                entities=list(ENTITY_TYPES),
                score_threshold=DEFAULT_SCORE_THRESHOLD,
                return_decision_process=False,
            )
        except Exception:  # noqa: BROAD_EXCEPT_OK - raw-free third-party boundary.
            raise PrivacyScanError("PRIVACY_SCAN_FAILED") from None
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

    @staticmethod
    def _presidio_result_allowed(text: str, result: AnalyzerResult) -> bool:
        if result.entity_type != "PERSON" or " " in text[result.start:result.end].strip():
            return True
        prefix = text[max(0, result.start - 24):result.start].lower()
        cues = ("name is ", "patient ", "pt. ", "dr. ", "doctor ", "provider ")
        return any(cue in prefix for cue in cues)

    def _build_analyzer(self) -> AnalyzerEngine:
        nlp = SpacyNlpEngine(
            models=[{"lang_code": "en", "model_name": MODEL_NAME}],
            ner_model_configuration=NerModelConfiguration(
                model_to_presidio_entity_mapping={"PERSON": "PERSON"},
                labels_to_ignore=_SPACY_IGNORED_LABELS,
                default_score=0.85,
            ),
        )
        nlp.load()
        recognizers = [
            CaSinRecognizer(), CreditCardRecognizer(), CryptoRecognizer(),
            IbanRecognizer(), IpRecognizer(), MacAddressRecognizer(),
            MedicalLicenseRecognizer(), PhoneRecognizer(supported_regions=("CA", "US")),
            SpacyRecognizer(),
            UrlRecognizer(), UsBankRecognizer(), UsItinRecognizer(), UsLicenseRecognizer(),
            UsMbiRecognizer(), UsNpiRecognizer(), UsPassportRecognizer(), UsSsnRecognizer(),
        ]
        registry = RecognizerRegistry(recognizers=recognizers, supported_languages=["en"])
        return AnalyzerEngine(
            registry=registry,
            nlp_engine=nlp,
            log_decision_process=False,
            default_score_threshold=DEFAULT_SCORE_THRESHOLD,
            supported_languages=["en"],
            context_aware_enhancer=LemmaContextAwareEnhancer(
                context_prefix_count=5,
                context_suffix_count=5,
                context_matching_mode="whole_word",
                min_score_with_context_similarity=DEFAULT_SCORE_THRESHOLD,
            ),
        )

    def _validate(self, analyzer: AnalyzerEngine) -> None:
        if metadata.version("presidio-analyzer") != ANALYZER_VERSION:
            raise PrivacyScanError("PRIVACY_VERSION_MISMATCH")
        if metadata.version("spacy") != "3.8.15":
            raise PrivacyScanError("PRIVACY_VERSION_MISMATCH")
        if metadata.version("en-core-web-sm") != MODEL_VERSION:
            raise PrivacyScanError("PRIVACY_MODEL_MISMATCH")
        inventory = set(analyzer.get_supported_entities(language="en"))
        if not set(ENTITY_TYPES) <= inventory:
            raise PrivacyScanError("PRIVACY_INVENTORY_MISMATCH")
        sentinels = analyzer.analyze(
            text="Alice Johnson used 192.168.10.42.",
            language="en",
            entities=["PERSON", "IP_ADDRESS"],
            score_threshold=DEFAULT_SCORE_THRESHOLD,
            return_decision_process=False,
        )
        if not {result.entity_type for result in sentinels} >= {"PERSON", "IP_ADDRESS"}:
            raise PrivacyScanError("PRIVACY_SENTINEL_FAILED")
