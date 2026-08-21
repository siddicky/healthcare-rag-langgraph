from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock

import pytest
from langchain_core.runnables import RunnableConfig
from langchain_core.utils.pydantic import model_json_schema
from langgraph.store.memory import InMemoryStore

from healthcare_rag.agent import store_data
from healthcare_rag.agent.store_data import MetricEntry
from healthcare_rag.agent.tools import log_metric as log_metric_tool
from healthcare_rag.processors.privacy import (
    PrivacySanitizer,
    PrivacyScan,
    PrivacyScanError,
)

FROZEN_THURSDAY = date(2026, 8, 20)
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FakeResources:
    privacy: FakePrivacy | PrivacySanitizer


@dataclass(frozen=True, slots=True)
class FakePrivacy:
    scan_value: Callable[[str], PrivacyScan]

    def scan(self, text: str) -> PrivacyScan:
        return self.scan_value(text)


def _clean_scan(value: str) -> PrivacyScan:
    return PrivacyScan(value, ())


def _name_scan(value: str) -> PrivacyScan:
    clean = value.replace("Alice Johnson", "[REDACTED_PERSON]")
    return PrivacyScan(clean, ("PERSON",)) if clean != value else PrivacyScan(value, ())


def _config(
    identity: str = "user-a",
    thread_id: str | None = "thread-1",
    human_msg_id: str | None = "human-1",
) -> RunnableConfig:
    configurable: dict[str, object] = {"langgraph_auth_user": {"identity": identity}}
    if thread_id is not None:
        configurable["thread_id"] = thread_id
    if human_msg_id is not None:
        configurable["coach_human_msg_id"] = human_msg_id
    return {"configurable": configurable}


def _frozen_today() -> date:
    return FROZEN_THURSDAY


async def _log(
    store: InMemoryStore,
    config: RunnableConfig,
    *,
    metric: str = "weight",
    value: float = 189.0,
    unit: str = "lb",
) -> str:
    return await log_metric_tool.log_metric_impl(
        metric=metric,
        value=value,
        unit=unit,
        config=config,
        store=store,
        today=_frozen_today,
    )


def _data(envelope: str) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(envelope)["data"])


@pytest.fixture(autouse=True)
def clean_privacy(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = FakeResources(FakePrivacy(_clean_scan))
    monkeypatch.setattr(log_metric_tool, "get_resources", lambda: resources)


async def test_first_log_omits_delta_and_persists_server_date() -> None:
    # Given
    store = InMemoryStore()

    # When
    result = await _log(store, _config(human_msg_id="human-1"), value=189.0)

    # Then
    envelope = cast("dict[str, object]", json.loads(result))
    assert envelope["block_id"] == "trend:weight"
    assert envelope["turn_scope_id"] == hashlib.sha256(b"thread-1|human-1").hexdigest()
    assert isinstance(envelope["text"], str) and envelope["text"]
    assert _data(result) == {
        "label": "Weight",
        "value": "189",
        "unit": "lb",
        "points": [189.0],
    }
    entries = await store_data.list_metrics(store, "user-a")
    assert len(entries) == 1
    assert entries[0].metric == "weight"
    assert entries[0].value == 189.0
    assert entries[0].unit == "lb"
    assert entries[0].date == FROZEN_THURSDAY


async def test_second_log_emits_delta_points_and_new_turn_scope() -> None:
    # Given
    store = InMemoryStore()
    first = await _log(store, _config(human_msg_id="human-1"), value=189.0)

    # When
    result = await _log(store, _config(human_msg_id="human-2"), value=182.4)

    # Then
    assert _data(result) == {
        "label": "Weight",
        "value": "182.4",
        "unit": "lb",
        "delta": "-6.6 lb",
        "deltaGood": True,
        "points": [189.0, 182.4],
    }
    assert json.loads(result)["turn_scope_id"] != json.loads(first)["turn_scope_id"]
    assert len(await store_data.list_metrics(store, "user-a")) == 2


async def test_upward_delta_is_flagged_bad() -> None:
    # Given
    store = InMemoryStore()
    _ = await _log(store, _config(human_msg_id="human-1"), value=182.4)

    # When
    result = await _log(store, _config(human_msg_id="human-2"), value=189.0)

    # Then
    assert _data(result)["delta"] == "+6.6 lb"
    assert _data(result)["deltaGood"] is False


async def test_invalid_metric_returns_error_and_leaves_store_unchanged() -> None:
    # Given
    store = InMemoryStore()

    # When
    result = await _log(store, _config(), metric="steps")

    # Then
    assert result == log_metric_tool.METRIC_INVALID
    assert await store.asearch(("users", "user-a", "metrics")) == []


async def test_identifier_unit_is_scrubbed_in_store_and_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setattr(
        log_metric_tool,
        "get_resources",
        lambda: FakeResources(FakePrivacy(_name_scan)),
    )
    store = InMemoryStore()

    # When
    result = await _log(store, _config(), unit="lb Alice Johnson")

    # Then
    assert _data(result)["unit"] == "lb [REDACTED_PERSON]"
    assert "Alice Johnson" not in result
    entries = await store_data.list_metrics(store, "user-a")
    assert [entry.unit for entry in entries] == ["lb [REDACTED_PERSON]"]


async def test_real_sanitizer_scrubs_identifier_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setattr(
        log_metric_tool,
        "get_resources",
        lambda: FakeResources(PrivacySanitizer()),
    )
    store = InMemoryStore()

    # When
    result = await _log(store, _config(), unit="lb 555-867-5309")

    # Then
    assert _data(result)["unit"] == "lb [REDACTED_PHONE]"
    assert "555-867-5309" not in result
    entries = await store_data.list_metrics(store, "user-a")
    assert [entry.unit for entry in entries] == ["lb [REDACTED_PHONE]"]


async def test_points_are_capped_at_eight_and_ascending() -> None:
    # Given
    store = InMemoryStore()
    scanner = FakePrivacy(_clean_scan)
    for index in range(8):
        await store_data.add_metric(
            store,
            "user-a",
            MetricEntry(
                metric_id=f"prior-{index}",
                metric="weight",
                value=190.0 + index,
                unit="lb",
                date=FROZEN_THURSDAY - timedelta(days=9 - index),
                created_ts=NOW,
            ),
            scanner,
        )

    # When
    result = await _log(store, _config(), value=199.0)

    # Then
    assert _data(result)["points"] == [
        191.0,
        192.0,
        193.0,
        194.0,
        195.0,
        196.0,
        197.0,
        199.0,
    ]


async def test_history_is_scoped_per_user() -> None:
    # Given
    store = InMemoryStore()
    _ = await _log(store, _config(identity="user-a"), value=189.0)

    # When
    result = await _log(store, _config(identity="user-b"), value=182.4)

    # Then
    assert "delta" not in _data(result)
    assert _data(result)["points"] == [182.4]
    assert len(await store_data.list_metrics(store, "user-a")) == 1
    assert len(await store_data.list_metrics(store, "user-b")) == 1


@pytest.mark.parametrize("missing", ["coach_human_msg_id", "thread_id"])
async def test_missing_turn_scope_key_raises(missing: str) -> None:
    # Given
    store = InMemoryStore()
    config = _config(
        thread_id=None if missing == "thread_id" else "thread-1",
        human_msg_id=None if missing == "coach_human_msg_id" else "human-1",
    )

    # When / Then
    with pytest.raises(log_metric_tool.MetricScopeError):
        _ = await _log(store, config)
    assert await store.asearch(("users", "user-a", "metrics")) == []


async def test_missing_identity_raises() -> None:
    # Given
    store = InMemoryStore()
    config: RunnableConfig = {
        "configurable": {"thread_id": "thread-1", "coach_human_msg_id": "human-1"}
    }

    # When / Then
    with pytest.raises(log_metric_tool.MetricIdentityError):
        _ = await _log(store, config)
    assert await store.asearch(("users", "user-a", "metrics")) == []


async def test_store_failure_returns_error_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    store = InMemoryStore()
    monkeypatch.setattr(
        InMemoryStore,
        "aput",
        AsyncMock(side_effect=OSError("store unavailable")),
    )

    # When
    result = await _log(store, _config())

    # Then
    assert result == log_metric_tool.STORE_REFUSAL


async def test_scanner_failure_refuses_without_storing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    def fail_scan(_value: str) -> PrivacyScan:
        raise PrivacyScanError("PRIVACY_SCAN_FAILED")

    monkeypatch.setattr(
        log_metric_tool,
        "get_resources",
        lambda: FakeResources(FakePrivacy(fail_scan)),
    )
    store = InMemoryStore()

    # When
    result = await _log(store, _config())

    # Then
    assert result == log_metric_tool.PRIVACY_REFUSAL
    assert await store.asearch(("users", "user-a", "metrics")) == []


def test_tool_schema_exposes_only_model_args() -> None:
    # Given the Route-B registration contract (todo 9): injected config/store
    # stay hidden and the metric enum is model-visible.
    schema_model = log_metric_tool.log_metric.tool_call_schema
    assert not isinstance(schema_model, dict)
    schema = cast("dict[str, object]", model_json_schema(schema_model))
    properties = cast("dict[str, object]", schema["properties"])
    assert set(properties) == {"metric", "value", "unit"}
    metric_prop = cast("dict[str, object]", properties["metric"])
    metric_ref = cast("str", metric_prop["$ref"])
    defs = cast("dict[str, object]", schema["$defs"])
    enum = cast("dict[str, object]", defs[metric_ref.rsplit("/", 1)[-1]])
    assert enum["enum"] == ["weight", "waist", "bmi"]
    assert schema["required"] == ["metric", "value", "unit"]


def test_metrics_envelope_block_ids_match_metric() -> None:
    # Given the frozen metric catalog.
    assert set(log_metric_tool.METRIC_SPECS) == {"weight", "waist", "bmi"}
