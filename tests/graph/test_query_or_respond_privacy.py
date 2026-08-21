from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from healthcare_rag.graph.nodes import query_or_respond
from healthcare_rag.processors.social_responses import social_response

from .query_or_respond_fakes import _gateway, _install, _state, _tool_call


async def test_gateway_binds_exact_tool_options_and_scrubbed_capped_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    canary = "person@example.com"
    response = AIMessage(content="safe social response")
    gateway, model = _gateway(monkeypatch, response, history_max_tokens=30)
    history: list[BaseMessage] = [
        HumanMessage(content=f"old-marker {canary} " + "old " * 80),
        AIMessage(content="old answer " * 80),
        HumanMessage(content="recent-marker"),
        AIMessage(content=f"recent answer {canary}"),
    ]

    # When
    result = await gateway.aquery_or_respond(history, f"Hello {canary}")

    # Then
    assert result.action == "direct"
    assert result.direct_content == "safe social response"
    assert result.fallback_reason is None
    assert model.tools == [
        {
            "type": "function",
            "function": {
                "name": "retrieve_monographs",
                "description": "Retrieve Lipitor and metformin product-monograph information.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "The monograph question to retrieve evidence for."
                            ),
                        }
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    assert model.options == {"tool_choice": "auto", "parallel_tool_calls": False}
    assert isinstance(model.bound.messages[0], SystemMessage)
    assert isinstance(model.bound.messages[-1], HumanMessage)
    assert "recent-marker" in repr(model.bound.messages)
    assert "old-marker" not in repr(model.bound.messages)
    assert canary not in repr(model.bound.messages)


async def test_inconsistent_social_state_cannot_enable_direct_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    original = "Original scrubbed question"
    _install(monkeypatch, AIMessage(content="untrusted direct output"))
    state = _state(benign_social=True, query=original)
    state["safety"] = {
        "category": "out_of_scope",
        "benign_social": True,
        "social_intent": None,
    }

    # When
    update = await query_or_respond.generate_query_or_respond(state)

    # Then
    assert update.get("working_query") == original
    assert update.get("direct_response") is None
    assert update.get("response_action") == "retrieve"
    assert update.get("query_router") == {
        "backend": "tool",
        "model_action": "direct",
        "effective_action": "retrieve",
        "fallback": True,
        "error": True,
        "fallback_reason": "invalid_social_authority",
        "tool_call_count": 0,
    }


async def test_non_out_of_scope_social_proposal_cannot_enable_direct_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    original = "Original scrubbed question"
    _install(monkeypatch, AIMessage(content="untrusted direct output"))
    state = _state(benign_social=True, query=original)
    state["safety"] = {
        "category": "personal_medical_advice",
        "benign_social": True,
        "social_intent": "greeting",
    }

    # When
    update = await query_or_respond.generate_query_or_respond(state)

    # Then
    assert update.get("working_query") == original
    assert update.get("direct_response") is None
    assert update.get("response_action") == "retrieve"
    assert update.get("query_router") == {
        "backend": "tool",
        "model_action": "direct",
        "effective_action": "retrieve",
        "fallback": True,
        "error": True,
        "fallback_reason": "invalid_social_authority",
        "tool_call_count": 0,
    }


async def test_gateway_projects_history_as_sanitized_content_only_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    canary = "history-person@example.com"
    gateway, model = _gateway(monkeypatch, AIMessage(content="safe response"))
    history: list[BaseMessage] = [
        HumanMessage(content="first human"),
        AIMessage(
            content="retained assistant content",
            tool_calls=[_tool_call(args={"query": canary})],
            additional_kwargs={"private": canary},
            response_metadata={"private": canary},
            id=canary,
            name=canary,
        ),
        HumanMessage(content="second human"),
    ]

    # When
    await gateway.aquery_or_respond(history, "Hello")

    # Then
    assert canary not in repr(model.bound.messages)
    retained = next(
        message
        for message in model.bound.messages
        if message.content == "retained assistant content"
    )
    assert isinstance(retained, AIMessage)
    assert retained.tool_calls == []
    assert retained.invalid_tool_calls == []
    assert retained.additional_kwargs == {}
    assert retained.response_metadata == {}
    assert retained.id is None
    assert retained.name is None


async def test_oversized_social_direct_content_uses_deterministic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _install(monkeypatch, AIMessage(content="x" * 20_000))

    # When
    update = await query_or_respond.generate_query_or_respond(
        _state(benign_social=True)
    )

    # Then
    assert update.get("direct_response") == social_response("greeting")
    assert update.get("response_action") == "direct"
    assert update.get("query_router") == {
        "backend": "tool",
        "model_action": "direct",
        "effective_action": "direct",
        "fallback": True,
        "error": True,
        "fallback_reason": "privacy_error",
        "tool_call_count": 0,
    }


async def test_oversized_medical_tool_query_uses_original_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    original = "Original scrubbed metformin question"
    response = AIMessage(
        content="discarded",
        tool_calls=[_tool_call(args={"query": "x" * 20_000})],
    )
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
        "model_action": "retrieve",
        "effective_action": "retrieve",
        "fallback": True,
        "error": True,
        "fallback_reason": "privacy_error",
        "tool_call_count": 1,
    }


@pytest.mark.parametrize("oversized_channel", ["history", "current"])
async def test_oversized_gateway_input_uses_original_query_without_model_call(
    monkeypatch: pytest.MonkeyPatch,
    oversized_channel: str,
) -> None:
    # Given
    original = "x" * 20_000 if oversized_channel == "current" else "Original query"
    _, model = _install(monkeypatch, AIMessage(content="must not run"))
    state = _state(benign_social=False, query=original)
    if oversized_channel == "history":
        state["messages"] = [HumanMessage(content="x" * 20_000)]

    # When
    update = await query_or_respond.generate_query_or_respond(state)

    # Then
    assert update.get("working_query") == original
    assert update.get("direct_response") is None
    assert update.get("response_action") == "retrieve"
    assert update.get("query_router") == {
        "backend": "tool",
        "model_action": None,
        "effective_action": "retrieve",
        "fallback": True,
        "error": True,
        "fallback_reason": "privacy_error",
        "tool_call_count": 0,
    }
    assert model.bind_count == 0
