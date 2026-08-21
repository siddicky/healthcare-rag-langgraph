from __future__ import annotations

from typing import assert_never, cast

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.tool import ToolCall
from pydantic import JsonValue

from healthcare_rag.processors.safety import scrub_phi


def _scrub(value: JsonValue) -> JsonValue:
    match value:
        case str() as text:
            return scrub_phi(text)[0]
        case bool() | int() | float() | None:
            return value
        case list() as values:
            return [_scrub(item) for item in values]
        case dict() as values:
            return {key: _scrub(item) for key, item in values.items()}
        case unreachable:
            assert_never(unreachable)


def _content(message: BaseMessage) -> str | list[str | dict[str, str]]:
    content = message.content
    match content:
        case str() as text:
            return scrub_phi(text)[0]
        case list() as blocks:
            safe: list[str | dict[str, str]] = []
            for block in blocks:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                ):
                    safe.append({"type": "text", "text": scrub_phi(block["text"])[0]})
            return safe
        case unreachable:
            assert_never(unreachable)


def to_safe_message(message: BaseMessage) -> AnyMessage:
    """Project a LangChain message onto the checkpoint-safe field allow-list."""
    content = _content(message)
    match message:
        case AIMessage():
            calls: list[ToolCall] = []
            for call in message.tool_calls:
                args = _scrub(call["args"])
                if isinstance(args, dict):
                    calls.append({"id": call["id"], "name": call["name"], "args": args})
            return AIMessage(
                id=message.id, name=message.name, content=content, tool_calls=calls
            )
        case ToolMessage():
            status = "error" if message.status == "error" else "success"
            return ToolMessage(
                id=message.id,
                name=message.name,
                content=content,
                tool_call_id=message.tool_call_id,
                status=status,
            )
        case HumanMessage():
            return HumanMessage(id=message.id, name=message.name, content=content)
        case SystemMessage():
            return SystemMessage(id=message.id, name=message.name, content=content)
        case ChatMessage(role=role):
            return ChatMessage(
                id=message.id, name=message.name, content=content, role=role
            )
        case BaseMessage(type=message_type):
            return cast(
                AnyMessage,
                BaseMessage(
                    id=message.id,
                    name=message.name,
                    content=content,
                    type=message_type,
                ),
            )
        case unreachable:
            assert_never(unreachable)


__all__ = ["to_safe_message"]
