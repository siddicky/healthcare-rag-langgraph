from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

from healthcare_rag.processors.privacy import MAX_INPUT_BYTES
from healthcare_rag.processors.safety import NUMERIC_DOSE, injection_flags, scrub_phi

GeneratedOutputDenial = Literal[
    "clinical_direct_content",
    "privacy_error",
    "unsafe_direct_content",
]

_TOKEN: Final = re.compile(r"[^\W_]+(?:['’][^\W_]+)?|%", re.UNICODE)
_CLINICAL_UNITS: Final = frozenset(
    {
        "%",
        "day",
        "days",
        "g",
        "hour",
        "hours",
        "hr",
        "hrs",
        "mcg",
        "mg",
        "ml",
        "mmol",
        "percent",
        "tablet",
        "tablets",
        "ug",
        "umol",
        "week",
        "weeks",
    }
)
_CLINICAL_ACTIONS: Final = frozenset(
    {
        "advise",
        "advised",
        "advises",
        "advising",
        "avoid",
        "avoided",
        "avoiding",
        "call",
        "called",
        "calling",
        "consider",
        "considered",
        "considering",
        "decrease",
        "decreased",
        "decreases",
        "decreasing",
        "double",
        "doubled",
        "doubles",
        "doubling",
        "hold",
        "holding",
        "increase",
        "increased",
        "increases",
        "increasing",
        "recommend",
        "recommended",
        "recommending",
        "reduce",
        "reduced",
        "reducing",
        "skip",
        "skipped",
        "skipping",
        "start",
        "started",
        "starting",
        "stop",
        "stopped",
        "stopping",
        "swallow",
        "swallowed",
        "swallowing",
        "take",
        "taken",
        "takes",
        "taking",
        "use",
        "used",
        "using",
    }
)
_CLINICAL_TARGETS: Final = frozenset(
    {
        "atorvastatin",
        "clinician",
        "dose",
        "doses",
        "dosage",
        "dosing",
        "doctor",
        "drug",
        "drugs",
        "grapefruit",
        "lipitor",
        "medication",
        "medications",
        "medicine",
        "medicines",
        "metformin",
        "pharmacist",
        "pill",
        "pills",
        "tablet",
        "tablets",
        "treatment",
        "treatments",
    }
)


@dataclass(frozen=True, slots=True)
class GeneratedOutputPolicyDecision:
    content: str
    denial_reason: GeneratedOutputDenial | None


def _normalized_tokens(text: str) -> tuple[str, tuple[str, ...]]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = normalized.replace("µ", "u").replace("μ", "u")
    return normalized, tuple(_TOKEN.findall(normalized))


def _has_clinical_instruction(tokens: tuple[str, ...]) -> bool:
    token_set = frozenset(tokens)
    return bool(
        _CLINICAL_ACTIONS.intersection(token_set)
        and _CLINICAL_TARGETS.intersection(token_set)
    )


def evaluate_generated_output(text: str) -> GeneratedOutputPolicyDecision:
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        return GeneratedOutputPolicyDecision("", "privacy_error")
    normalized, tokens = _normalized_tokens(text)
    if injection_flags(normalized):
        return GeneratedOutputPolicyDecision("", "unsafe_direct_content")
    has_unit = NUMERIC_DOSE.search(normalized) or _CLINICAL_UNITS.intersection(tokens)
    if has_unit or _has_clinical_instruction(tokens):
        return GeneratedOutputPolicyDecision("", "clinical_direct_content")
    scrubbed, privacy_hits = scrub_phi(text)
    if privacy_hits:
        return GeneratedOutputPolicyDecision("", "privacy_error")
    return GeneratedOutputPolicyDecision(scrubbed.strip(), None)
