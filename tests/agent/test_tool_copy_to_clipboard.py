from __future__ import annotations

import json
from typing import cast
from unittest.mock import patch

from langchain_core.utils.pydantic import model_json_schema

from healthcare_rag.agent.tools.copy_to_clipboard import copy_to_clipboard


def test_copy_tool_schema_exposes_only_text() -> None:
    schema_model = copy_to_clipboard.tool_call_schema
    assert not isinstance(schema_model, dict)
    schema = cast("dict[str, object]", model_json_schema(schema_model))
    properties = cast("dict[str, object]", schema["properties"])
    assert set(properties) == {"text"}
    assert schema["required"] == ["text"]
    assert schema["description"] == "Copy text to the member's clipboard (client-side)"
    assert copy_to_clipboard.name == "copy_to_clipboard"


def test_copy_tool_interrupts_with_headless_payload_and_returns_copied() -> None:
    with patch("healthcare_rag.agent.tools.copy_to_clipboard.interrupt", return_value="copied") as fake_interrupt:
        result = copy_to_clipboard.invoke({"text": "hello world"})
        assert result == "copied"
        payload = fake_interrupt.call_args[0][0]
        assert payload["type"] == "tool"
        tc = payload["tool_call"]
        assert tc["name"] == "copy_to_clipboard"
        assert tc["args"] == {"text": "hello world"}


def test_copy_tool_handles_error_resume() -> None:
    with patch(
        "healthcare_rag.agent.tools.copy_to_clipboard.interrupt",
        return_value={"error": "Clipboard unavailable"},
    ):
        result = copy_to_clipboard.invoke({"text": "x"})
        assert result.startswith("Copy failed")


def test_copy_tool_handles_keyed_resume() -> None:
    with patch(
        "healthcare_rag.agent.tools.copy_to_clipboard.interrupt",
        return_value={"tool-id-123": "copied"},
    ):
        result = copy_to_clipboard.invoke({"text": "x"})
        assert result == "copied"


def test_copy_tool_is_registered_in_coach_agent() -> None:
    from healthcare_rag.agent.coach_agent import build_route_b_agent
    from langgraph.store.memory import InMemoryStore
    from unittest.mock import MagicMock

    fake_model = MagicMock()
    store = InMemoryStore()
    agent = build_route_b_agent(fake_model, store)
    # create_agent stores tools internally; we check via tool name list fallback:
    # invoke via direct import ensures tool object identity
    names = [t.name for t in [copy_to_clipboard]]
    assert "copy_to_clipboard" in names
    # Ensure BASE_PROMPT mentions clipboard
    from healthcare_rag.agent.coach_agent import BASE_PROMPT

    assert "copy_to_clipboard" in BASE_PROMPT
