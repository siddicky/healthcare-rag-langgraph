from __future__ import annotations

import re
import unicodedata
from typing import Final, assert_never

from healthcare_rag.processors.refusal_boundary import (
    _LIPITOR,
    _METFORMIN,
    _OUT_OF_SCOPE_DRUGS,
)

from .state import CoachingParse, PreviousContext, TurnFeatures

MEDICAL_CUES: Final = frozenset(
    {
        "medication", "medicine", "dose", "dosing", "interaction", "interactions",
        "contraindication", "contraindications", "safety", "symptom", "symptoms",
    }
)
SYMPTOMS: Final = frozenset(
    {"dizzy", "dizziness", "nausea", "headache", "chest pain", "blood sugar"}
)
KNOWLEDGE_CUES: Final = (
    "side effect", "side effects", "interaction", "interactions", "contraindication",
    "contraindications", "safety", "safe to", "dosing guidance",
)
REMINDER_WORDS: Final = frozenset({"remind", "reminder", "reminders", "nudge", "nudges"})
CONTENT_OPENERS: Final = frozenset(
    {"what", "why", "how", "is", "are", "can", "could", "should", "does", "do"}
)
SMALLTALK: Final = frozenset(
    {"hello", "hi", "hey", "thanks", "thank you", "how are you", "good morning", "good evening"}
)
ERASE_ACTION: Final = re.compile(
    r"\b(?:delete(?:d|ing)?|erase(?:d|ing)?|remove(?:d|ing)?|forget|wipe(?:d|ing)?|"
    + r"clear(?:ed|ing)?|purge(?:d|ing)?|get rid of)\b"
)
ERASE_OBJECT: Final = re.compile(
    r"\b(?:my\s+)?(?:data|account|history|records|everything|medication history)\b"
)
NUMBER_UNIT: Final = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|ml|iu|lb|lbs|pounds?|kg|kilograms?|cm|in)\b",
    re.IGNORECASE,
)
DOSAGE_UNIT: Final = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|ml|iu)\b", re.IGNORECASE)
METRIC_UNIT: Final = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:lb|lbs|pounds?|kg|kilograms?|cm|in)\b",
    re.IGNORECASE,
)
ANAPHORIC: Final = re.compile(r"\b(?:it|that|this|them|those|do that|change it)\b")


def _normalize(text: str) -> tuple[str, tuple[str, ...]]:
    source = unicodedata.normalize("NFC", text).lower()
    return source, tuple(token for token in re.split(r"[\W_]+", source) if token)


def _coaching_parse(source: str, tokens: tuple[str, ...]) -> CoachingParse:
    token_set = set(tokens)
    if token_set & REMINDER_WORDS:
        return "reminder_manage"
    if ({"schedule", "calendar", "appointment", "checkin"} & token_set) and (
        {"move", "change", "reschedule", "cancel", "shift"} & token_set
    ):
        return "schedule_change"
    if ("move" in token_set) and ({"friday", "monday", "dose", "checkin"} & token_set):
        return "schedule_change"
    if {"schedule", "calendar", "appointments"} & token_set:
        return "schedule_view"
    metric_read = "weight" in token_set and ({"trend", "trending", "changed", "history"} & token_set)
    metric_write = ({"log", "record", "track", "weigh"} & token_set) and (
        {"weight", "waist", "bmi"} & token_set or METRIC_UNIT.search(source) is not None
    )
    if metric_read or metric_write:
        return "metric_log"
    drug_hit = _LIPITOR.search(source) is not None or _METFORMIN.search(source) is not None
    injection_object = {"injection", "injections", "shot", "shots", "dose", "doses"} & token_set
    if ({"log", "record", "took", "taken", "inject", "injected"} & token_set) and (
        drug_hit or bool(injection_object) or DOSAGE_UNIT.search(source) is not None
    ):
        return "injection_log"
    if ({"remember", "save", "note"} & token_set) and ({"fact", "preference", "goal"} & token_set):
        return "memory_write"
    return "none"


def compute_features(
    question: str,
    attachment_id: str | None = None,
    prev_context: PreviousContext = "none",
) -> TurnFeatures:
    source, tokens = _normalize(question)
    token_set = set(tokens)
    phrases = {" ".join(tokens[index:index + 2]) for index in range(len(tokens) - 1)}
    first = tokens[0] if tokens else ""
    return TurnFeatures(
        has_in_scope_drug=_LIPITOR.search(source) is not None or _METFORMIN.search(source) is not None,
        has_oos_drug=bool(token_set & _OUT_OF_SCOPE_DRUGS),
        has_medical_cue=bool(
            token_set & MEDICAL_CUES
            or phrases & SYMPTOMS
            or token_set & SYMPTOMS
            or any(cue in source for cue in KNOWLEDGE_CUES)
        ),
        has_number_unit=NUMBER_UNIT.search(source) is not None,
        is_content_request="?" in question or first in CONTENT_OPENERS,
        coaching_parse=_coaching_parse(source, tokens),
        is_erase_request=ERASE_ACTION.search(source) is not None
        and ERASE_OBJECT.search(source) is not None,
        is_smalltalk=source.strip(" .!?") in SMALLTALK,
        has_attachment=bool(attachment_id),
        prev_context=prev_context,
        classifier_category="ambiguous",
        classifier_failed=False,
    )


def is_anaphoric_followup(question: str) -> bool:
    source, tokens = _normalize(question)
    return len(tokens) <= 15 and ANAPHORIC.search(source) is not None


def _dosage_is_associated(source: str) -> bool:
    drugs = [*_OUT_OF_SCOPE_DRUGS, "lipitor", "atorvastatin", "metformin", "glucophage"]
    names = "|".join(sorted((re.escape(drug) for drug in drugs), key=len, reverse=True))
    pattern = re.compile(
        rf"\b(?:my\s+)?(?:{names})[ \t]+\d+(?:\.\d+)?[ \t]+(?:mg|mcg|ml|iu)\b"
        + r"(?:[ \t]+(?:dose|doses|injection|shot))?",
        re.IGNORECASE,
    )
    return pattern.search(source) is not None


def has_unexplained_medical_token(question: str, features: TurnFeatures) -> bool:
    source, _ = _normalize(question)
    knowledge = any(cue in source for cue in KNOWLEDGE_CUES)
    dosage = DOSAGE_UNIT.search(source) is not None
    metric = METRIC_UNIT.search(source) is not None
    drug = features["has_in_scope_drug"] or features["has_oos_drug"]
    if dosage and features["is_content_request"]:
        return True
    match features["coaching_parse"]:
        case "metric_log":
            return dosage or drug or knowledge
        case "injection_log":
            return metric or knowledge or (dosage and not _dosage_is_associated(source))
        case "schedule_view" | "schedule_change" | "reminder_manage":
            return metric or dosage or knowledge
        case "memory_write":
            return metric or dosage or drug or knowledge
        case "none":
            return False
        case unreachable:
            assert_never(unreachable)
