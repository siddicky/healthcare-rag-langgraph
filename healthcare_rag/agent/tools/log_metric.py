"""``log_metric`` — persist one metric reading, emit a trend DATA envelope.

Route-B hand-off contract: the ``coach_agent`` node sets
``configurable.coach_human_msg_id`` (the id of the latest sanitized
HumanMessage for the turn) before the agent runs; together with
``configurable.thread_id`` it scopes the envelope through
``store_data.make_envelope``. The model supplies only ``metric``, ``value``
and ``unit`` — never a date, an id, or a scope; the log date is the server's
today and the owner is the authenticated principal.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, ClassVar, Final, final, override
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedStore
from langgraph.store.base import BaseStore
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from healthcare_rag.agent import store_data
from healthcare_rag.agent.memory import principal_mapping
from healthcare_rag.agent.store_data import MetricEntry, make_envelope
from healthcare_rag.graph.resources import get as get_resources
from healthcare_rag.processors.privacy import PrivacyScanError

MAX_POINTS: Final = 8
METRIC_INVALID: Final = (
    "Metric not logged: unknown metric; expected weight, waist, or bmi."
)
PRIVACY_REFUSAL: Final = "Metric not logged: privacy checks failed."
STORE_REFUSAL: Final = "Metric not logged: storage unavailable."


class MetricName(StrEnum):
    WEIGHT = "weight"
    WAIST = "waist"
    BMI = "bmi"


@dataclass(frozen=True, slots=True)
class _MetricSpec:
    label: str
    lower_is_better: bool


METRIC_SPECS: Final[dict[str, _MetricSpec]] = {
    MetricName.WEIGHT.value: _MetricSpec("Weight", True),
    MetricName.WAIST.value: _MetricSpec("Waist", True),
    MetricName.BMI.value: _MetricSpec("BMI", True),
}


class _AuthPrincipal(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    identity: str


@final
@dataclass(frozen=True, slots=True)
class MetricIdentityError(Exception):
    code: str = "METRIC_AUTH_IDENTITY_REQUIRED"

    @override
    def __str__(self) -> str:
        return self.code


@final
@dataclass(frozen=True, slots=True)
class MetricScopeError(Exception):
    code: str = "METRIC_TURN_SCOPE_REQUIRED"

    @override
    def __str__(self) -> str:
        return self.code


def _authenticated_user_id(config: RunnableConfig) -> str:
    principal = principal_mapping(
        config.get("configurable", {}).get("langgraph_auth_user")
    )
    if principal is None:
        raise MetricIdentityError
    try:
        identity = _AuthPrincipal.model_validate(principal).identity
    except ValidationError:
        raise MetricIdentityError
    if not identity:
        raise MetricIdentityError
    return identity


def _turn_scope(config: RunnableConfig) -> tuple[str, str]:
    configurable = config.get("configurable", {})
    thread_id = configurable.get("thread_id")
    human_msg_id = configurable.get("coach_human_msg_id")
    if not isinstance(thread_id, str) or not isinstance(human_msg_id, str):
        raise MetricScopeError
    return thread_id, human_msg_id


def _format_value(value: float) -> str:
    return f"{value:g}"


def _server_today() -> date:
    return datetime.now(UTC).date()


async def log_metric_impl(
    metric: str,
    value: float,
    unit: str,
    config: RunnableConfig,
    store: Annotated[BaseStore, InjectedStore()],
    today: Callable[[], date] | None = None,
) -> str:
    """Validate, persist and envelope one metric reading for the caller."""
    spec = METRIC_SPECS.get(metric)
    if spec is None:
        return METRIC_INVALID
    user_id = _authenticated_user_id(config)
    thread_id, human_msg_id = _turn_scope(config)
    privacy = get_resources().privacy
    try:
        clean_unit = privacy.scan(unit).text
    except PrivacyScanError:
        return PRIVACY_REFUSAL
    try:
        prior = [
            entry
            for entry in await store_data.list_metrics(store, user_id)
            if entry.metric == metric
        ]
        entry = MetricEntry(
            metric_id=str(uuid4()),
            metric=metric,
            value=value,
            unit=clean_unit,
            date=(today or _server_today)(),
            created_ts=datetime.now(UTC),
        )
        await store_data.add_metric(store, user_id, entry, privacy)
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK - store boundary.
        return STORE_REFUSAL
    points: list[JsonValue] = []
    for prior_entry in prior[-(MAX_POINTS - 1) :]:
        points.append(prior_entry.value)
    points.append(value)
    data: dict[str, JsonValue] = {}
    data["label"] = spec.label
    data["value"] = _format_value(value)
    data["unit"] = clean_unit
    data["points"] = points
    text = f"{spec.label} logged: {_format_value(value)} {clean_unit}."
    if prior:
        delta = value - prior[-1].value
        data["delta"] = f"{delta:+.1f} {clean_unit}"
        data["deltaGood"] = delta <= 0 if spec.lower_is_better else delta >= 0
        text = (
            f"{spec.label} logged: {_format_value(value)} {clean_unit} "
            f"({delta:+.1f} {clean_unit} since last log)."
        )
    return make_envelope(thread_id, human_msg_id, f"trend:{metric}", data, text)


async def _log_metric(
    metric: MetricName,
    value: float,
    unit: str,
    config: RunnableConfig,
    store: Annotated[BaseStore, InjectedStore()],
) -> str:
    """LangChain-facing entry point; the clock stays server-side."""
    return await log_metric_impl(
        metric=metric, value=value, unit=unit, config=config, store=store
    )


log_metric = tool("log_metric")(_log_metric)

__all__ = [
    "METRIC_INVALID",
    "METRIC_SPECS",
    "PRIVACY_REFUSAL",
    "STORE_REFUSAL",
    "MetricIdentityError",
    "MetricName",
    "MetricScopeError",
    "log_metric",
    "log_metric_impl",
]
