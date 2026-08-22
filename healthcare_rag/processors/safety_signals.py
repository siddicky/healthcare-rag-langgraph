"""Deterministic safety signals derived from untrusted message text."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from .safety_patterns import injection_flags, strip_injection

SALVAGEABLE_INJECTION_FLAGS: Final = frozenset({"ignore_instructions"})

_IDENTIFIER_RECALL_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"\b(?:remind me(?: of| what)?|what (?:was|is|were)|tell me|give me(?: back)?|"
        r"repeat|read (?:it |them )?back|recite|confirm)\b[^.?!]{0,60}?"
        r"\b(?:health card|hc number|mrn|medical record|chart number|dob|date of birth|"
        r"phone number|address|email|identifiers?|my details|his details|her details|"
        r"my number|his number|her number|the number)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\blast (?:three|3|four|4|two|2) digits\b", re.IGNORECASE),
    re.compile(
        r"\b(?:you had it|you knew it|you have it|i told you (?:it|that|them)|just repeat it|"
        r"repeat it back|say it back)\b",
        re.IGNORECASE,
    ),
)

_FIRST_PERSON: Final = re.compile(
    r"\b(?:i|i'?m|im|i'?ve|ive|i'?d|my|me|myself|we|our)\b", re.IGNORECASE
)
_RED_FLAG_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "chest_pain",
        re.compile(
            r"\b(?:chest (?:pain|pains|hurts?|hurting|tightness|pressure|discomfort|heaviness)|"
            r"pain (?:in|across) (?:my |the )?chest|crushing (?:pain|chest))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "breathing_difficulty",
        re.compile(
            r"\b(?:trouble breathing|difficulty breathing|hard (?:to|time) breath\w*|"
            r"can'?t breathe|cannot breathe|short(?:ness)? of breath|"
            r"can'?t catch (?:my )?breath|struggling to breathe|gasping for)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "dark_urine",
        re.compile(
            r"(?:\b(?:brown|dark|cola|tea|red[- ]?brown)[- ]?(?:colou?red\s+)?(?:urine|pee)\b"
            r"|\b(?:urine|pee|wee)\b[^.?!]{0,30}?\b(?:gone|turned|is|has become|the colou?r of)\b"
            r"[^.?!]{0,20}?\b(?:brown|dark|cola|tea)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "confusion",
        re.compile(
            r"\b(?:feel|feeling|felt|getting|got|become|becoming|been|am|'m|is|seems?|gone)\s+"
            r"(?:very |really |quite |increasingly |more |a bit )?"
            r"(?:confused|disoriented|delirious)\b(?!\s+(?:about|by|over|regarding|as to|with))",
            re.IGNORECASE,
        ),
    ),
    (
        "allergic_swelling",
        re.compile(
            r"\b(?:swelling of (?:my |the )?(?:face|lips|tongue|throat|mouth)|"
            r"(?:my |the )?(?:face|lips|tongue|throat) (?:is |are |has |have )?(?:swollen|swelling)|"
            r"anaphyla\w*|trouble swallowing|throat closing|hives all over)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "possible_overdose",
        re.compile(
            r"\b(?:overdosed|took an overdose)\b|"
            r"\b(?:took|taken|swallowed|had)\b[^.?!]{0,40}?"
            r"\b(?:whole bottle|too many (?:pills|tablets)|all (?:my|the) (?:pills|tablets)|overdose)\b",
            re.IGNORECASE,
        ),
    ),
)
_MUSCLE_SYMPTOM: Final = re.compile(
    r"\b(?:muscle (?:weakness|pain|aches?|tenderness|soreness)|"
    r"weak(?:ness)? in (?:my )?(?:legs|arms|thighs|muscles)|thighs? feel weak|"
    r"myopathy|rhabdomyolysis|rhabdo)\b",
    re.IGNORECASE,
)
_SEVERE_ABDOMINAL: Final = re.compile(
    r"\b(?:severe|bad|terrible|intense|worst|excruciating|awful)\b[^.?!]{0,30}?"
    r"\b(?:abdominal|stomach|belly|tummy)\s+(?:pain|ache|cramps?)",
    re.IGNORECASE,
)
_VOMITING: Final = re.compile(
    r"\b(?:vomit\w*|throwing up|threw up|being sick|can'?t keep (?:anything|fluids) down|"
    r"not keeping fluids down)\b",
    re.IGNORECASE,
)

DOSING_QUESTION: Final = re.compile(
    r"\b(?:dose|doses|dosing|dosage|titrat\w*|how much|how many|mg\b|milligram|"
    r"maximum daily|max(?:imum)? dose|adjust\w* (?:the |my |his |her )?dose|double|"
    r"increase|decrease|reduce|split|half a tablet|skip|hold|stop taking|start taking)\b",
    re.IGNORECASE,
)


def scrub_phi(text: str, extra_spans: Sequence[str] = ()) -> tuple[str, list[str]]:
    """Replace personal identifiers with typed redaction tokens."""
    del extra_spans
    if not text:
        return text, []
    from healthcare_rag.graph.resources import get

    scan = get().privacy.scan(text)
    return scan.text, list(scan.kinds)


def contains_phi(text: str) -> bool:
    """Return whether the deterministic sanitizer finds an identifier."""
    return bool(scrub_phi(text)[1])


def identifier_recall_requested(text: str) -> bool:
    """Return whether the user asks the assistant to read identifiers back."""
    return any(pattern.search(text or "") for pattern in _IDENTIFIER_RECALL_PATTERNS)


def red_flag_terms(text: str) -> list[str]:
    """Return first-person emergency red flags found in message text."""
    body = text or ""
    if not _FIRST_PERSON.search(body):
        return []
    hits: list[str] = []
    for name, pattern in _RED_FLAG_PATTERNS:
        if pattern.search(body) and name not in hits:
            hits.append(name)
    if "dark_urine" in hits and _MUSCLE_SYMPTOM.search(body):
        hits.append("dark_urine_with_muscle_weakness")
    if _SEVERE_ABDOMINAL.search(body) and _VOMITING.search(body):
        hits.append("severe_abdominal_pain_with_vomiting")
    return hits


__all__ = [
    "DOSING_QUESTION",
    "SALVAGEABLE_INJECTION_FLAGS",
    "contains_phi",
    "identifier_recall_requested",
    "injection_flags",
    "red_flag_terms",
    "scrub_phi",
    "strip_injection",
]
