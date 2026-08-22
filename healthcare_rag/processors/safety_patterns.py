from __future__ import annotations

import re
from typing import Final

_INJECTION_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "ignore_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass|drop)\b[^.?!]{0,40}?"
            r"\b(?:instruction|instructions|rules?|guidelines?|polic(?:y|ies)|prompt|training|"
            r"restrictions?|safety|guardrails?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "persona_override",
        re.compile(
            r"\b(?:pretend|act|behave|roleplay|role-play|imagine)\b[^.?!]{0,30}?"
            r"\b(?:you(?:'re| are)|to be|as (?:if|an?|my))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "persona_override",
        re.compile(
            r"\b(?:you are now|you'?re now|from now on,? (?:you|ignore|act)|answer as|respond as|reply as)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "unrestricted_mode",
        re.compile(
            r"\b(?:unrestricted|unfiltered|jailbreak|jailbroken|developer mode|dev mode|test mode|"
            r"god mode|dan mode|no (?:safety|restrictions|filters|polic(?:y|ies))|"
            r"without (?:any )?(?:safety|restrictions|filters))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_exfil",
        re.compile(
            r"\b(?:system prompt|prompt verbatim|"
            r"(?:print|repeat|reveal|show|output|tell me|give me) your (?:system )?(?:prompt|instructions))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "fiction_harm",
        re.compile(
            r"\b(?:novel|story|fiction|fictional|screenplay|script|character)\b[^.?!]{0,90}?"
            r"\b(?:lethal|fatal|deadly|overdose|toxic dose|how much .{0,20}kill)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "fiction_harm",
        re.compile(
            r"\b(?:lethal|fatal|deadly|toxic) (?:dose|amount|quantity)\b[^.?!]{0,90}?"
            r"\b(?:novel|story|fiction|fictional|screenplay|script|character|writing)\b",
            re.IGNORECASE,
        ),
    ),
)

NUMERIC_DOSE: Final = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|µg|g|ml|mL|mmol/?L?|[uµ]mol/?L?|%|tablets?|"
    r"times? (?:a|per) day|hours?|hrs?|days?|weeks?)\b",
    re.IGNORECASE,
)


def injection_flags(text: str) -> list[str]:
    hits: list[str] = []
    for name, pattern in _INJECTION_PATTERNS:
        if pattern.search(text or "") and name not in hits:
            hits.append(name)
    return hits


def strip_injection(text: str) -> str:
    out = text or ""
    for _name, pattern in _INJECTION_PATTERNS:
        out = pattern.sub(" ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return re.sub(
        r"^(?:and|then|also|but|so|,|\.|;|:)\s+", "", out, flags=re.IGNORECASE
    ).strip()
