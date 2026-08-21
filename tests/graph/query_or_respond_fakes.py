from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from healthcare_rag.graph.llm import LangChainLLMGateway
from healthcare_rag.graph.nodes import query_or_respond
from healthcare_rag.graph.resources import Resources, override
from healthcare_rag.graph.state import JSONValue, RAGState

from .conftest import make_settings


class SyntheticModelError(RuntimeError):
    pass


class FakeBoundModel:
    def __init__(self, response: AIMessage | SyntheticModelError) -> None:
        self.response = response
        self.messages: list[BaseMessage] = []

    async def ainvoke(self, messages: Sequence[BaseMessage]) -> AIMessage:
        self.messages = list(messages)
        if isinstance(self.response, AIMessage):
            return self.response
        raise self.response


class FakeChatModel:
    def __init__(self, response: AIMessage | SyntheticModelError) -> None:
        self.response = response
        self.bound = FakeBoundModel(response)
        self.tools: list[dict[str, JSONValue]] = []
        self.options: dict[str, str | bool] = {}
        self.bind_count = 0

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
    response: AIMessage | SyntheticModelError,
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
    response: AIMessage | SyntheticModelError,
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
