"""Presidio analyzer construction and fail-closed self-validation.

This is the only place in the repository that imports presidio or spaCy. The
graph-side client reproduces the same contract (entity inventory, pinned
versions, sentinel scan) against this service's ``/health`` and ``/analyze``
routes, so the two validations cannot silently drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from typing import Final, final

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

MAX_INPUT_BYTES: Final = 16 * 1024
DEFAULT_SCORE_THRESHOLD: Final = 0.40
MODEL_NAME: Final = "en_core_web_sm"
MODEL_VERSION: Final = "3.8.0"
ANALYZER_VERSION: Final = "2.2.364"
SPACY_VERSION: Final = "3.8.15"
LANGUAGE: Final = "en"

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
SENTINEL_TEXT: Final = "Alice Johnson used 192.168.10.42."
SENTINEL_ENTITIES: Final[frozenset[str]] = frozenset({"PERSON", "IP_ADDRESS"})


@final
class EngineError(Exception):
    """Raw-free engine failure; ``code`` is the only thing that leaves this module."""

    __slots__: tuple[str, ...] = ("code",)
    code: str

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    entity_type: str
    score: float


@dataclass(frozen=True, slots=True)
class EngineInfo:
    analyzer_version: str
    spacy_version: str
    model_name: str
    model_version: str
    entities: tuple[str, ...]


def build_analyzer() -> AnalyzerEngine:
    nlp = SpacyNlpEngine(
        models=[{"lang_code": LANGUAGE, "model_name": MODEL_NAME}],
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
    registry = RecognizerRegistry(recognizers=recognizers, supported_languages=[LANGUAGE])
    return AnalyzerEngine(
        registry=registry,
        nlp_engine=nlp,
        log_decision_process=False,
        default_score_threshold=DEFAULT_SCORE_THRESHOLD,
        supported_languages=[LANGUAGE],
        context_aware_enhancer=LemmaContextAwareEnhancer(
            context_prefix_count=5,
            context_suffix_count=5,
            context_matching_mode="whole_word",
            min_score_with_context_similarity=DEFAULT_SCORE_THRESHOLD,
        ),
    )


def validate(analyzer: AnalyzerEngine) -> EngineInfo:
    info = EngineInfo(
        analyzer_version=metadata.version("presidio-analyzer"),
        spacy_version=metadata.version("spacy"),
        model_name=MODEL_NAME,
        model_version=metadata.version("en-core-web-sm"),
        entities=tuple(sorted(analyzer.get_supported_entities(language=LANGUAGE))),
    )
    if info.analyzer_version != ANALYZER_VERSION or info.spacy_version != SPACY_VERSION:
        raise EngineError("PRIVACY_VERSION_MISMATCH")
    if info.model_version != MODEL_VERSION:
        raise EngineError("PRIVACY_MODEL_MISMATCH")
    if not set(ENTITY_TYPES) <= set(info.entities):
        raise EngineError("PRIVACY_INVENTORY_MISMATCH")
    sentinels = analyze(analyzer, SENTINEL_TEXT, list(SENTINEL_ENTITIES), DEFAULT_SCORE_THRESHOLD)
    if not {span.entity_type for span in sentinels} >= SENTINEL_ENTITIES:
        raise EngineError("PRIVACY_SENTINEL_FAILED")
    return info


def analyze(
    analyzer: AnalyzerEngine,
    text: str,
    entities: list[str],
    score_threshold: float,
) -> list[Span]:
    try:
        results = analyzer.analyze(
            text=text,
            language=LANGUAGE,
            entities=entities,
            score_threshold=score_threshold,
            return_decision_process=False,
        )
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        raise EngineError("PRIVACY_SCAN_FAILED") from None
    return [
        Span(result.start, result.end, result.entity_type, float(result.score))
        for result in results
    ]


@final
class Engine:
    """Process-owned analyzer; construction is the readiness gate."""

    __slots__: tuple[str, ...] = ("_analyzer", "info")

    def __init__(self) -> None:
        try:
            analyzer = build_analyzer()
        except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
            raise EngineError("PRIVACY_INITIALIZATION_FAILED") from None
        self.info: EngineInfo = validate(analyzer)
        self._analyzer: AnalyzerEngine = analyzer

    def analyze(
        self,
        text: str,
        entities: list[str] | None = None,
        score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    ) -> list[Span]:
        return analyze(self._analyzer, text, entities or list(ENTITY_TYPES), score_threshold)
