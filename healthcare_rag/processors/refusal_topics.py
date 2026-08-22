"""Drug-topic classification for persisted refusal boundaries."""

from __future__ import annotations

import re
from typing import Final, Literal, TypeAlias, assert_never

BoundaryTopic: TypeAlias = Literal["lipitor", "metformin", "both", "none", "other"]

_LIPITOR: Final = re.compile(r"\b(?:lipitor|atorvastatin)\b", re.IGNORECASE)
_METFORMIN: Final = re.compile(r"\b(?:metformin|glucophage)\b", re.IGNORECASE)
_OUT_OF_SCOPE_DRUGS: Final = frozenset(
    {
        "insulin",
        "warfarin",
        "aspirin",
        "ibuprofen",
        "acetaminophen",
        "tylenol",
        "advil",
        "lisinopril",
        "amlodipine",
        "levothyroxine",
        "omeprazole",
        "gabapentin",
        "hydrochlorothiazide",
        "ozempic",
        "semaglutide",
        "jardiance",
        "sitagliptin",
        "januvia",
    }
)
_OTHER_DRUG: Final = re.compile(
    rf"\b(?:{'|'.join(sorted(_OUT_OF_SCOPE_DRUGS))})\b", re.IGNORECASE
)


def query_topic(text: str) -> BoundaryTopic:
    """Classify explicit drug words using the pinned deterministic lexicons."""
    lipitor = bool(_LIPITOR.search(text))
    metformin = bool(_METFORMIN.search(text))
    if lipitor and metformin:
        return "both"
    if lipitor:
        return "lipitor"
    if metformin:
        return "metformin"
    return "other" if _OTHER_DRUG.search(text) else "none"


def derive_boundary_topic(
    scrubbed_query: str, assessment_drug: str
) -> BoundaryTopic:
    """Prefer an explicit query drug, then a supported assessment drug."""
    topic = query_topic(scrubbed_query)
    match topic:
        case "lipitor" | "metformin" | "both" | "other":
            return topic
        case "none":
            pass
        case unreachable:
            assert_never(unreachable)
    match assessment_drug:
        case "lipitor" | "metformin" | "both":
            return assessment_drug
        case _:
            return "none"


__all__ = ["BoundaryTopic", "derive_boundary_topic", "query_topic"]
