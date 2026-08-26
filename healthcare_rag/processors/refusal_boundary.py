"""Pure persisted-refusal matching and state helpers.

Same-thread concurrent turns are unsupported and must be serialized by the caller;
``GraphEngine._run`` has no per-thread lock, while the CLI and eval harness are
sequential today. Cue-less re-asks are fresh classifier trials by design. Short or
anaphoric unknown-drug phrasings can inherit and replay (known limit L4); only long,
referent-free unknown-drug queries fall through.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import ClassVar, Final, Literal, TypeAlias, assert_never

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from pydantic_core import PydanticCustomError
from typing_extensions import TypeAliasType

from .refusal_topics import (
    BoundaryTopic,
    derive_boundary_topic,
    query_topic,
)
from .safety import (
    DOSING_QUESTION,
    SALVAGEABLE_INJECTION_FLAGS,
    injection_flags,
    red_flag_terms,
)
from .safety_responses import (
    emergency_response,
    injection_response,
    personal_advice_response,
)
from .safety_signals import _FIRST_PERSON

JSONValue = TypeAliasType(
    "JSONValue",
    "str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]",
)
BoundaryKind: TypeAlias = Literal["personal_advice", "emergency", "injection"]
BoundaryKey: TypeAlias = tuple[str, str] | tuple[str, str, str]

TEMPLATE_VERSION: Final = 1

_ALLOWED_RESPONSES: Final[Mapping[BoundaryKind, frozenset[str]]] = MappingProxyType(
    {
        "personal_advice": frozenset({personal_advice_response()}),
        "emergency": frozenset({emergency_response(), emergency_response(overdose=True)}),
        "injection": frozenset({injection_response()}),
    }
)

_REFERENT = re.compile(
    r"\b(it|that|this|them|those|the (dose|pill|tablet|amount|max|maximum|limit))\b",
    re.IGNORECASE,
)
_CONTINUATION = re.compile(
    r"\b(?:not|just) (?:asking|told|said|asked)\b|\b(?:like|as) i said\b|\bi already\b|" +
    r"\byou already\b|\bstill\b|\bagain\b|\bis back\b|\bafter all\b|\bthough\b|\banyway\b",
    re.IGNORECASE,
)
_INFORMATIONAL = re.compile(
    r"the monograph|product monograph|prescribing information|patient information|" +
    r"product information|general information|non-personal information|in general\b|the label|" +
    r"per the (monograph|label|document)|" +
    r"(inside|within|under|over|above|below|exceed\w*) (the |a |that )?" +
    r"(limit|maximum|max(imum)? (daily )?dose|recommended|range|stated)|" +
    r"what(?:['’]?s| is| are) the (usual|maximum|max|recommended|typical|normal|listed|documented)|" +
    r"does the (monograph|document)|is [^.?!]{0,30} a (listed|reported|known|documented)|" +
    r"how is [^.?!]{0,30} (dosed|given|titrated)|" +
    r"(how|what) (are|is|do|does) [^.?!]{0,40}? " +
    r"(described|listed|documented|explained|covered|written|reported)|" +
    r"what should i (ask|tell)\b|" +
    r"(can|could) i ask (how|what|whether|about)",
    re.IGNORECASE,
)
_DECISION_REQUEST = re.compile(
    r"(safe|ok|okay|fine|sensible|wise|right) for me to\b|" +
    r"should i\b(?![^.?!]{0,40}?\b(?:ask|tell)\b)[^.?!]{0,40}?\b(take|taking|dose|dosing|dosage|double|doubling|split|stop|stopping|start|skip|increase|decrease|go up|go down|crush|chew|mix|drink|inject|keep taking|keep on)\b|" +
    r"what should i\b(?![^.?!]{0,40}?\b(?:ask|tell)\b)[^.?!]{0,40}?\b(take|taking|dose|dosing|dosage|double|doubling|split|stop|stopping|start|skip|increase|decrease|go up|go down|crush|chew|mix|drink|inject|keep taking|keep on)\b|" +
    r"what would you do\b|in my position\b|give me the (green light|go ahead)|" +
    r"confirm i can\b|just confirm i can\b",
    re.IGNORECASE,
)
_INTENT_CUE = re.compile(
    r"can i|could i|may i|is it (ok|okay|safe|fine|alright) (if|to) i|" +
    r"what if i (took|take|missed|skip(?:ped)?|double(?:d)?|stop(?:ped)?)|" +
    r"i('ll| will| am|'m) (start|take|double|stop|skip|split|increase|decrease|begin)|" +
    r"i('ll| will) start that",
    re.IGNORECASE,
)


class RefusalBoundary(BaseModel):
    """Versioned, template-constrained refusal persisted in checkpoint state."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True, extra="forbid")

    kind: BoundaryKind
    topic: BoundaryTopic
    response: str
    created_ts: str
    template_version: int

    @field_validator("created_ts")
    @classmethod
    def _created_ts_is_utc(cls, value: str) -> str:
        del cls
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise PydanticCustomError("utc_datetime", "created_ts must be ISO-8601 UTC") from error
        if parsed.utcoffset() != timedelta(0):
            raise PydanticCustomError("utc_datetime", "created_ts must be ISO-8601 UTC")
        return value

    def to_state(self) -> dict[str, JSONValue]:
        """Serialize without changing template bytes."""
        return {
            "kind": self.kind,
            "topic": self.topic,
            "response": self.response,
            "created_ts": self.created_ts,
            "template_version": self.template_version,
        }

    @classmethod
    def from_state(cls, data: dict[str, JSONValue]) -> RefusalBoundary | None:
        """Parse a valid current-version boundary, otherwise leave it inert."""
        try:
            boundary = cls.model_validate(data)
        except ValidationError:
            return None
        if boundary.template_version != TEMPLATE_VERSION:
            return None
        if boundary.response not in allowed_responses(boundary.kind):
            return None
        return boundary


def allowed_responses(kind: BoundaryKind) -> frozenset[str]:
    """Return byte-exact templates permitted for a boundary kind."""
    return _ALLOWED_RESPONSES[kind]


def _ANAPHORIC(text: str) -> bool:
    return len(text.split()) <= 15 or bool(_REFERENT.search(text) or _CONTINUATION.search(text))


def _topic_matches(topic: BoundaryTopic, anaphoric: bool, stored: BoundaryTopic) -> bool:
    return (
        (stored == topic and topic != "none")
        or (topic == "none" and anaphoric and stored in {"lipitor", "metformin", "both", "none"})
        or (stored == "both" and topic in {"lipitor", "metformin"})
        or (topic == "both" and stored in {"lipitor", "metformin"})
    )


def boundary_hit(query: str, boundaries: Sequence[RefusalBoundary]) -> RefusalBoundary | None:
    """Return the most recent compatible refusal under exclusive cue precedence."""
    red_flags = red_flag_terms(query)
    emergency_cue = bool(red_flags)
    inj_flags = injection_flags(query)
    unsalvageable = set(inj_flags) - SALVAGEABLE_INJECTION_FLAGS
    decision_request = bool(_DECISION_REQUEST.search(query))
    personal_cue = bool(
        _FIRST_PERSON.search(query)
        and (DOSING_QUESTION.search(query) or _INTENT_CUE.search(query) or decision_request)
    )
    if not decision_request and _INFORMATIONAL.search(query):
        return None

    topic = query_topic(query)
    anaphoric = _ANAPHORIC(query)
    if emergency_cue:
        overdose = emergency_response(overdose=True)
        return next(
            (
                boundary
                for boundary in reversed(boundaries)
                if boundary.kind == "emergency"
                and _topic_matches(topic, anaphoric, boundary.topic)
                and (("possible_overdose" in red_flags) == (boundary.response == overdose))
            ),
            None,
        )
    if inj_flags:
        if not unsalvageable:
            return None
        return next(
            (
                boundary
                for boundary in reversed(boundaries)
                if boundary.kind == "injection" and _topic_matches(topic, anaphoric, boundary.topic)
            ),
            None,
        )
    if personal_cue:
        return next(
            (
                boundary
                for boundary in reversed(boundaries)
                if boundary.kind == "personal_advice"
                and _topic_matches(topic, anaphoric, boundary.topic)
            ),
            None,
        )
    return None


def _boundary_key(boundary: RefusalBoundary) -> BoundaryKey:
    match boundary.kind:
        case "personal_advice" | "injection":
            return boundary.kind, boundary.topic
        case "emergency":
            variant = "overdose" if boundary.response == emergency_response(overdose=True) else "standard"
            return boundary.kind, boundary.topic, variant
        case unreachable:
            assert_never(unreachable)


def _raw_key(raw: dict[str, JSONValue]) -> BoundaryKey | None:
    kind = raw.get("kind")
    topic = raw.get("topic")
    response = raw.get("response")
    if raw.get("template_version") != TEMPLATE_VERSION or not isinstance(response, str):
        return None
    if kind not in {"personal_advice", "emergency", "injection"}:
        return None
    if topic not in {"lipitor", "metformin", "both", "none", "other"}:
        return None
    if kind == "personal_advice" and response == personal_advice_response():
        return kind, topic
    if kind == "injection" and response == injection_response():
        return kind, topic
    if kind == "emergency" and response in allowed_responses("emergency"):
        variant = "overdose" if response == emergency_response(overdose=True) else "standard"
        return kind, topic, variant
    return None


def upsert_boundary(raw: list[dict[str, JSONValue]], new: RefusalBoundary) -> list[dict[str, JSONValue]]:
    """Replace matching valid/raw-best-effort keys and append the new state."""
    new_key = _boundary_key(new)
    retained: list[dict[str, JSONValue]] = []
    for entry in raw:
        parsed = RefusalBoundary.from_state(entry)
        entry_key = _boundary_key(parsed) if parsed is not None else _raw_key(entry)
        if entry_key != new_key:
            retained.append(entry)
    return [*retained, new.to_state()]


def load_boundaries(raw: list[dict[str, JSONValue]]) -> list[RefusalBoundary]:
    """Load only valid boundaries without mutating raw checkpoint state."""
    return [boundary for entry in raw if (boundary := RefusalBoundary.from_state(entry)) is not None]


__all__ = [
    "TEMPLATE_VERSION",
    "BoundaryKind",
    "BoundaryTopic",
    "JSONValue",
    "RefusalBoundary",
    "allowed_responses",
    "boundary_hit",
    "derive_boundary_topic",
    "load_boundaries",
    "query_topic",
    "upsert_boundary",
]
