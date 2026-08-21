from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from healthcare_rag.graph.nodes import query_or_respond
from healthcare_rag.processors.social_responses import social_response

from .query_or_respond_fakes import SyntheticModelError, _install, _state, _tool_call


@pytest.mark.parametrize(
    ("response", "reason", "model_action"),
    [
        (
            AIMessage(content="", tool_calls=[_tool_call()]),
            "social_tool_call",
            "retrieve",
        ),
        (AIMessage(content=""), "empty_response", None),
        (
            AIMessage(
                content="",
                invalid_tool_calls=[
                    {
                        "name": "retrieve_monographs",
                        "args": "{bad-json",
                        "id": "bad",
                        "type": "invalid_tool_call",
                        "error": "bad arguments",
                    }
                ],
            ),
            "malformed_tool",
            "retrieve",
        ),
        (
            AIMessage(content=[{"type": "text", "text": "malformed social"}]),
            "malformed_content",
            None,
        ),
        (SyntheticModelError("synthetic model failure"), "model_error", None),
    ],
)
async def test_social_invalid_decisions_use_gate_invalid_deterministic_fallback(
    monkeypatch: pytest.MonkeyPatch,
    response: AIMessage | SyntheticModelError,
    reason: str,
    model_action: str | None,
) -> None:
    # Given
    _install(monkeypatch, response)

    # When
    update = await query_or_respond.generate_query_or_respond(
        _state(benign_social=True)
    )

    # Then
    assert update.get("direct_response") == social_response("greeting")
    assert update.get("response_action") == "direct"
    assert update.get("query_router") == {
        "backend": "tool",
        "model_action": model_action,
        "effective_action": "direct",
        "fallback": True,
        "error": True,
        "fallback_reason": reason,
        "tool_call_count": (
            len(response.tool_calls) + len(response.invalid_tool_calls)
            if isinstance(response, AIMessage)
            else 0
        ),
    }
    assert "messages" not in update


async def test_medical_valid_tool_uses_sanitized_tool_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    canary = "person@example.com"
    response = AIMessage(
        content="untrusted prose",
        tool_calls=[_tool_call(args={"query": f"Lipitor effects for {canary}"})],
    )
    _install(monkeypatch, response)

    # When
    update = await query_or_respond.generate_query_or_respond(
        _state(benign_social=False, query="Original Lipitor question")
    )

    # Then
    assert update.get("response_action") == "retrieve"
    assert update.get("direct_response") is None
    assert canary not in str(update.get("working_query"))
    assert "untrusted prose" not in repr(update)
    assert update.get("query_router") == {
        "backend": "tool",
        "model_action": "retrieve",
        "effective_action": "retrieve",
        "fallback": False,
        "error": False,
        "fallback_reason": None,
        "tool_call_count": 1,
    }
    assert "messages" not in update


@pytest.mark.parametrize(
    ("response", "reason", "model_action"),
    [
        (
            AIMessage(content="medical prose must be discarded"),
            "medical_free_text",
            "direct",
        ),
        (
            AIMessage(content="unsafe", tool_calls=[_tool_call("unknown_tool")]),
            "unknown_tool",
            "retrieve",
        ),
        (
            AIMessage(
                content="unsafe",
                tool_calls=[_tool_call(), _tool_call(call_id="call-2")],
            ),
            "multiple_tools",
            "retrieve",
        ),
        (
            AIMessage(content="unsafe", tool_calls=[_tool_call(args={})]),
            "malformed_tool",
            "retrieve",
        ),
        (
            AIMessage(content=[{"type": "text", "text": "medical prose"}]),
            "malformed_content",
            None,
        ),
        (AIMessage(content=""), "empty_response", None),
        (SyntheticModelError("synthetic model failure"), "model_error", None),
    ],
)
async def test_medical_invalid_decisions_discard_output_and_use_original_query(
    monkeypatch: pytest.MonkeyPatch,
    response: AIMessage | SyntheticModelError,
    reason: str,
    model_action: str | None,
) -> None:
    # Given
    original = "Original scrubbed metformin question"
    _install(monkeypatch, response)

    # When
    update = await query_or_respond.generate_query_or_respond(
        _state(benign_social=False, query=original)
    )

    # Then
    assert update.get("working_query") == original
    assert update.get("direct_response") is None
    assert update.get("response_action") == "retrieve"
    assert update.get("query_router") == {
        "backend": "tool",
        "model_action": model_action,
        "effective_action": "retrieve",
        "fallback": True,
        "error": True,
        "fallback_reason": reason,
        "tool_call_count": (
            len(response.tool_calls) + len(response.invalid_tool_calls)
            if isinstance(response, AIMessage)
            else 0
        ),
    }
    assert "unsafe" not in repr(update)
    assert "medical prose" not in repr(update)
    assert "messages" not in update


@pytest.mark.parametrize("arm", ["current", "deterministic"])
async def test_non_tool_arms_preserve_state_without_model_calls(
    monkeypatch: pytest.MonkeyPatch,
    arm: str,
) -> None:
    # Given
    _, model = _install(monkeypatch, AIMessage(content="must not run"), arm=arm)

    # When
    update = await query_or_respond.generate_query_or_respond(
        _state(benign_social=True)
    )

    # Then
    assert update == {}
    assert model.bind_count == 0
