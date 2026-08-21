from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from healthcare_rag.graph.llm import LangChainLLMGateway
from healthcare_rag.graph.nodes import query_or_respond
from healthcare_rag.graph.resources import Resources, override
from healthcare_rag.graph.state import JSONValue, RAGState
from healthcare_rag.processors.social_responses import social_response

from .conftest import make_settings


@dataclass(slots=True)
class FakeBoundModel:
    response: AIMessage | RuntimeError
    messages: list[BaseMessage] = field(default_factory=list)

    async def ainvoke(self, messages: Sequence[BaseMessage]) -> AIMessage:
        self.messages = list(messages)
        match self.response:
            case RuntimeError() as error:
                raise error
            case AIMessage() as response:
                return response


@dataclass(slots=True)
class FakeChatModel:
    response: AIMessage | RuntimeError
    bound: FakeBoundModel = field(init=False)
    tools: list[dict[str, JSONValue]] = field(default_factory=list)
    options: dict[str, str | bool] = field(default_factory=dict)
    bind_count: int = 0

    def __post_init__(self) -> None:
        self.bound = FakeBoundModel(self.response)

    def bind_tools(
        self,
        tools: Sequence[dict[str, JSONValue]],
        *,
        tool_choice: str,
        parallel_tool_calls: bool,
    ) -> FakeBoundModel:
        self.bind_count += 1
        self.tools = list(tools)
        self.options = {
            "tool_choice": tool_choice,
            "parallel_tool_calls": parallel_tool_calls,
        }
        return self.bound


def _gateway(
    monkeypatch: pytest.MonkeyPatch,
    response: AIMessage | RuntimeError,
    *,
    arm: str = "tool",
    history_max_tokens: int = 4_000,
) -> tuple[LangChainLLMGateway, FakeChatModel]:
    settings = replace(
        make_settings(),
        query_response_arm=arm,
        history_max_tokens=history_max_tokens,
    )
    gateway = LangChainLLMGateway(settings=settings)
    model = FakeChatModel(response)
    monkeypatch.setattr(gateway, "chat_model", lambda _tier: model)
    return gateway, model


def _state(*, benign_social: bool, query: str = "Hello") -> RAGState:
    return {
        "scrubbed_question": query,
        "working_query": query,
        "messages": [HumanMessage(content="prior"), AIMessage(content="final")],
        "safety": {
            "category": "out_of_scope" if benign_social else "in_scope_informational",
            "benign_social": benign_social,
            "social_intent": "greeting" if benign_social else None,
        },
        "direct_response": "stale direct",
        "response_action": "stale",
        "query_router": {"backend": "stale"},
    }


def _install(
    monkeypatch: pytest.MonkeyPatch,
    response: AIMessage | RuntimeError,
    *,
    arm: str = "tool",
) -> tuple[LangChainLLMGateway, FakeChatModel]:
    gateway, model = _gateway(monkeypatch, response, arm=arm)
    override(Resources(gateway.settings))
    monkeypatch.setattr(query_or_respond, "GATEWAY", gateway)
    return gateway, model


def _tool_call(
    name: str = "retrieve_monographs",
    args: dict[str, str] | None = None,
    *,
    call_id: str = "call-1",
) -> dict[str, str | dict[str, str]]:
    return {
        "name": name,
        "args": {"query": "Lipitor effects"} if args is None else args,
        "id": call_id,
        "type": "tool_call",
    }


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


async def test_social_no_tool_content_becomes_sanitized_direct_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    canary = "person@example.com"
    _, model = _install(monkeypatch, AIMessage(content=f"Hello {canary}"))
    state = _state(benign_social=True)

    # When
    update = await query_or_respond.generate_query_or_respond(state)

    # Then
    assert update.get("response_action") == "direct"
    assert canary not in str(update.get("direct_response"))
    assert update.get("query_router") == {
        "backend": "tool",
        "model_action": "direct",
        "effective_action": "direct",
        "fallback": False,
        "error": False,
        "fallback_reason": None,
        "tool_call_count": 0,
    }
    assert "messages" not in update
    assert model.bound.messages[-1].content == "Hello"


@pytest.mark.parametrize(
    "content",
    [
        "Take 10 mg now.",
        "Take 2 tablets today.",
        "Please take 5 mL.",
        "You should stop taking your medicine.",
        "Use 1 percent solution.",
        "Use 1% solution.",
        "Please avoid grapefruit while taking Lipitor.",
        "You must swallow the tablet whole.",
        "I recommend calling your doctor.",
        "Take 200 mcg now.",
        "Take Lipitor now.",
        "Stop Lipitor now.",
        "Double your dose now.",
        "Increase Lipitor now.",
        "Decrease Lipitor now.",
        "Skip tonight's dose.",
    ],
)
async def test_gateway_rejects_clinical_direct_content(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    gateway, _ = _gateway(monkeypatch, AIMessage(content=content))

    decision = await gateway.aquery_or_respond([], "Hello")

    assert decision.action == "direct"
    assert decision.direct_content == ""
    assert decision.fallback_reason == "clinical_direct_content"


@pytest.mark.parametrize(
    "content",
    [
        "Take 10 mg now.",
        "You should increase your dose.",
        "Use 1% solution.",
        "I recommend calling your doctor.",
        "Take 200 mcg now.",
        "Take Lipitor now.",
        "Stop Lipitor now.",
        "Double your dose now.",
        "Decrease Lipitor now.",
        "Skip tonight's dose.",
    ],
)
async def test_social_clinical_direct_content_uses_deterministic_fallback(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    _, model = _install(monkeypatch, AIMessage(content=content))

    update = await query_or_respond.generate_query_or_respond(
        _state(benign_social=True)
    )

    assert update.get("direct_response") == social_response("greeting")
    assert update.get("response_action") == "direct"
    assert update.get("query_router") == {
        "backend": "tool",
        "model_action": "direct",
        "effective_action": "direct",
        "fallback": True,
        "error": True,
        "fallback_reason": "clinical_direct_content",
        "tool_call_count": 0,
    }
    assert content not in repr(update)
    assert "messages" not in update
    assert model.bound.messages[-1].content == "Hello"


@pytest.mark.parametrize(
    ("intent", "content"),
    [
        ("greeting", "Hello there."),
        ("thanks", "Happy to help."),
        ("goodbye", "Goodbye."),
        ("capability", "I can help with dosing in general from the monographs."),
    ],
)
async def test_social_direct_content_preserves_allowed_intents(
    monkeypatch: pytest.MonkeyPatch,
    intent: str,
    content: str,
) -> None:
    _install(monkeypatch, AIMessage(content=content))
    state = _state(benign_social=True)
    state["safety"] = {
        "category": "out_of_scope",
        "benign_social": True,
        "social_intent": intent,
    }

    update = await query_or_respond.generate_query_or_respond(state)

    assert update.get("direct_response") == content
    telemetry = update.get("query_router")
    assert isinstance(telemetry, dict)
    assert telemetry.get("fallback") is False
    assert telemetry.get("error") is False


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
        (RuntimeError("synthetic model failure"), "model_error", None),
    ],
)
async def test_social_invalid_decisions_use_gate_invalid_deterministic_fallback(
    monkeypatch: pytest.MonkeyPatch,
    response: AIMessage | RuntimeError,
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
        (RuntimeError("synthetic model failure"), "model_error", None),
    ],
)
async def test_medical_invalid_decisions_discard_output_and_use_original_query(
    monkeypatch: pytest.MonkeyPatch,
    response: AIMessage | RuntimeError,
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
