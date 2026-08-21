from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Final, Literal, TypeAlias, assert_never

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .static_copy_allowlist import DISPATCH_ALLOWLIST, STATIC_COPY_ALLOWLIST

JsonObject: TypeAlias = dict[str, JsonValue]
ExpectedType: TypeAlias = Literal["string", "number", "boolean", "array", "object"]
_FACT_PROPS: Final[dict[str, dict[str, ExpectedType]]] = {
    "InjectionTracker": {
        "medicationName": "string",
        "doseLabel": "string",
        "days": "array",
        "nextDoseLabel": "string",
    },
    "MiniCalendar": {
        "monthLabel": "string",
        "firstWeekday": "number",
        "daysInMonth": "number",
        "highlights": "array",
    },
    "TrendCard": {
        "label": "string",
        "value": "string",
        "unit": "string",
        "delta": "string",
        "deltaGood": "boolean",
        "points": "array",
    },
    "ActionCard": {"title": "string", "body": "string"},
    "StatRow": {"stats": "array"},
    "ScoreRing": {"score": "number"},
    "Timeline": {"items": "array"},
    "Card": {},
    "Tag": {},
    "Label": {},
    "Button": {},
}
_STATIC_PROPS: Final[dict[str, frozenset[str]]] = {
    "InjectionTracker": frozenset(),
    "MiniCalendar": frozenset({"onDateSelectAction"}),
    "TrendCard": frozenset(),
    "ActionCard": frozenset({"primaryAction", "secondaryAction"}),
    "StatRow": frozenset(),
    "ScoreRing": frozenset({"label"}),
    "Timeline": frozenset(),
    "Card": frozenset({"variant", "bordered", "large", "text"}),
    "Tag": frozenset({"text"}),
    "Label": frozenset({"gold", "text"}),
    "Button": frozenset({"variant", "size", "full", "disabled", "label", "action"}),
}
_ENUMS: Final = frozenset(
    {"elevated", "birch", "primary", "secondary", "gold", "default", "sm"}
)
_CLAIM_PATTERN: Final = re.compile(
    r"\d|\b(?:mon|tue|wed|thu|fri|sat|sun|daily|weekly|monthly|mg|kg|lb|lbs|bmi|am|pm)\b",
    re.IGNORECASE,
)


class RefTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    turn_scope_id: str
    block_id: str
    pointer: str


class DataRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ref: RefTarget = Field(alias="__ref")

    @property
    def target(self) -> RefTarget:
        return self.ref


class ComposedNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    component: str
    props: JsonObject
    children: list[ComposedNode] | None = None


class ComposeUIArgs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tree: list[ComposedNode]


@dataclass(frozen=True, slots=True)
class CompositionValidation:
    valid: bool
    reason: str = ""


def _pointer(value: JsonValue, pointer: str) -> JsonValue | None:
    if not pointer.startswith("/"):
        return None
    current = value
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        match current:
            case dict() as mapping:
                if token not in mapping:
                    return None
                current = mapping[token]
            case list() as sequence:
                if not token.isdigit() or int(token) >= len(sequence):
                    return None
                current = sequence[int(token)]
            case str() | bool() | int() | float() | None:
                return None
            case unreachable:
                assert_never(unreachable)
    return current


def _matches(value: JsonValue, expected: ExpectedType) -> bool:
    match expected:
        case "string":
            return isinstance(value, str)
        case "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
        case "boolean":
            return isinstance(value, bool)
        case "array":
            return isinstance(value, list)
        case "object":
            return isinstance(value, dict)
        case unreachable:
            assert_never(unreachable)


def _envelopes(contents: list[str], scope: str) -> dict[str, JsonValue]:
    values: dict[str, JsonValue] = {}
    for content in contents:
        try:
            candidate = json.loads(content)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(candidate, dict)
            and candidate.get("turn_scope_id") == scope
            and isinstance(candidate.get("block_id"), str)
            and "data" in candidate
        ):
            values[candidate["block_id"]] = candidate["data"]
    return values


def _static(value: JsonValue) -> bool:
    match value:
        case str() as text:
            if text in DISPATCH_ALLOWLIST or text in _ENUMS:
                return True
            return text in STATIC_COPY_ALLOWLIST and _CLAIM_PATTERN.search(text) is None
        case bool():
            return True
        case dict() as mapping:
            return all(_static(item) for item in mapping.values())
        case list() as values:
            return all(_static(item) for item in values)
        case int() | float() | None:
            return False
        case unreachable:
            assert_never(unreachable)


def _node_valid(node: ComposedNode, blocks: dict[str, JsonValue], scope: str) -> bool:
    component = node.component
    if component not in _FACT_PROPS:
        return False
    allowed = frozenset(_FACT_PROPS[component]) | _STATIC_PROPS[component]
    if frozenset(node.props) - allowed:
        return False
    for name, value in node.props.items():
        expected = _FACT_PROPS[component].get(name)
        if expected is None:
            if not _static(value):
                return False
            continue
        try:
            target = DataRef.model_validate(value).target
        except (TypeError, ValueError):
            return False
        if target.turn_scope_id != scope or target.block_id not in blocks:
            return False
        resolved = _pointer(blocks[target.block_id], target.pointer)
        if resolved is None or not _matches(resolved, expected):
            return False
    return all(_node_valid(child, blocks, scope) for child in node.children or [])


def validate_composition(
    args: JsonObject, envelope_contents: list[str], turn_scope_id: str
) -> CompositionValidation:
    """Validate one compose_ui call against catalog, current-turn envelopes and copy."""
    try:
        parsed = ComposeUIArgs.model_validate(args)
    except (TypeError, ValueError) as error:
        return CompositionValidation(False, type(error).__name__)
    blocks = _envelopes(envelope_contents, turn_scope_id)
    valid = all(_node_valid(node, blocks, turn_scope_id) for node in parsed.tree)
    return CompositionValidation(valid, "" if valid else "CATALOG_COMPOSITION_INVALID")


@tool(args_schema=ComposeUIArgs)
def compose_ui(tree: list[ComposedNode]) -> str:
    """Render a backend-validated catalog composition in the member client."""
    del tree
    return "Composition accepted."


__all__ = [
    "ComposeUIArgs",
    "ComposedNode",
    "CompositionValidation",
    "compose_ui",
    "validate_composition",
]
