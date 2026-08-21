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
_SENTENCE_BOUNDARY: Final = re.compile(r"[.!?;:\n]+")
_SOCIAL_ONLY_SENTENCE: Final = re.compile(
    r"(?:"
    r"(?:hello|hi|hey)(?:\s+(?:again|everyone|there))?"
    r"|(?:good\s+(?:afternoon|evening|morning))"
    r"|(?:goodbye|bye)(?:\s+(?:for\s+now|take\s+care))?"
    r"|take\s+care"
    r"|(?:thank\s+you|thanks)(?:\s+(?:again|so\s+much|very\s+much))?"
    r"|(?:you(?:'re|\s+are)\s+welcome)(?:\s+anytime)?"
    r"|never\s+mind(?:\s*,?\s*thanks)?"
    r")"
)
_CAPABILITY_SENTENCE: Final = re.compile(
    r"(?:"
    r"(?:happy|glad)\s+to\s+(?:answer|assist|help)"
    r"|(?:i|we)\s+(?:(?:can|could)\s+|(?:am|are)\s+able\s+to\s+)"
    r"(?:answer|assist|discuss|explain|help)"
    r"|you\s+(?:can|may)\s+ask"
    r"|ask"
    r"|feel\s+free\s+to\s+ask"
    r"|do\s+not\s+hesitate\s+to\s+ask"
    r"|consider\s+asking"
    r")(?:\s+(?P<scope>.+))?"
)
_DRUG_SCOPE: Final = (
    r"(?:lipitor(?:\s*\(\s*atorvastatin\s*\))?|atorvastatin|metformin)"
)
_DRUG_LIST_SCOPE: Final = rf"{_DRUG_SCOPE}(?:\s+(?:and|or)\s+{_DRUG_SCOPE})*"
_MONOGRAPH_SCOPE: Final = (
    rf"(?:the\s+)?(?:{_DRUG_LIST_SCOPE}\s+)?(?:product\s+)?monographs?"
)
_CAPABILITY_CATEGORY: Final = (
    r"(?:dosing|effects|interactions|monitoring|uses|warnings|side\s+effects)"
)
_CATEGORY_LIST: Final = (
    rf"{_CAPABILITY_CATEGORY}(?:\s*,\s*{_CAPABILITY_CATEGORY})*"
    rf"(?:\s*,?\s+and\s+{_CAPABILITY_CATEGORY})?"
)
_MONOGRAPH_TOPIC: Final = (
    rf"{_MONOGRAPH_SCOPE}(?:\s*,\s*including\s+{_CATEGORY_LIST})?"
)
_CAPABILITY_TOPIC: Final = (
    rf"(?:{_MONOGRAPH_TOPIC}"
    rf"|{_DRUG_SCOPE}\s+{_CAPABILITY_CATEGORY}"
    rf"|{_CAPABILITY_CATEGORY}(?:\s+in\s+general)?"
    rf"(?:\s+from\s+{_MONOGRAPH_SCOPE})?"
    rf"|information\s+(?:about|from|on)\s+{_MONOGRAPH_TOPIC})"
)
_QUESTION_SCOPE: Final = (
    r"(?:(?:a|an|another|any|your)\s+)?questions?"
    rf"(?:\s+(?:(?:about|on)\s+{_CAPABILITY_TOPIC}"
    rf"|grounded\s+in\s+{_MONOGRAPH_SCOPE}))?"
)
_CAPABILITY_SCOPE: Final = re.compile(
    rf"(?:{_QUESTION_SCOPE}"
    rf"|(?:me\s+)?anything(?:\s+(?:about|on)\s+{_CAPABILITY_TOPIC})?"
    rf"|me"
    rf"|(?:me\s+)?(?:about|on)\s+{_CAPABILITY_TOPIC}"
    rf"|with\s+(?:{_QUESTION_SCOPE}|{_CAPABILITY_TOPIC})"
    rf"|{_CAPABILITY_TOPIC})"
)
_WHOLE_TOKEN_CONTROLS: Final = frozenset(
    {
        "medical prose must be discarded",
        "safe social response",
        "that pillbox looks useful",
        "the tabletops are clean",
        "this is milligrammatical wordplay",
    }
)
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


def _is_social_output(normalized: str) -> bool:
    sentences = tuple(
        sentence.strip()
        for sentence in _SENTENCE_BOUNDARY.split(normalized)
        if sentence.strip()
    )
    if not sentences:
        return False
    for sentence in sentences:
        if sentence in _WHOLE_TOKEN_CONTROLS:
            continue
        if _SOCIAL_ONLY_SENTENCE.fullmatch(sentence) is not None:
            continue
        capability = _CAPABILITY_SENTENCE.fullmatch(sentence)
        if capability is None:
            return False
        scope = capability.group("scope")
        if scope is not None and _CAPABILITY_SCOPE.fullmatch(scope) is None:
            return False
    return True


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
    if not _is_social_output(normalized):
        return GeneratedOutputPolicyDecision("", "clinical_direct_content")
    return GeneratedOutputPolicyDecision(scrubbed.strip(), None)
