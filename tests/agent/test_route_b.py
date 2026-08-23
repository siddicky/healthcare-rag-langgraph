from __future__ import annotations

import json
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any, Self

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from healthcare_rag.agent.coach_agent import (
    SAFE_FALLBACK,
    AgentContext,
    build_route_b_agent,
)
from healthcare_rag.agent.compose_ui import validate_composition
from healthcare_rag.agent.erase import ERASE_MARKER_NAME, erase_my_data
from healthcare_rag.agent.finalize import finalize_coach
from healthcare_rag.agent.ns_sweep import (
    checkpoint_records,
    diff_records,
    lineage_leaves,
)
from healthcare_rag.agent.safe_message import to_safe_message


class ToolCapableFakeModel(FakeMessagesListChatModel):
    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Self:
        return self


def _context() -> AgentContext:
    return AgentContext(user_id="user-1", thread_id="thread-1", human_msg_id="human-1")


def _config() -> RunnableConfig:
    return {
        "recursion_limit": 20,
        "configurable": {
            "thread_id": "thread-1",
            "coach_human_msg_id": "human-1",
            "langgraph_auth_user": {"identity": "user-1"},
        },
    }


@pytest.mark.asyncio
async def test_route_b_tool_round_trip_preserves_call_correlation() -> None:
    model = ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "compose-1", "name": "compose_ui", "args": {"tree": []}}
                ],
            ),
            AIMessage(content="Done."),
        ]
    )
    agent = build_route_b_agent(model, InMemoryStore())

    result = await agent.ainvoke(
        {"messages": [HumanMessage(id="human-1", content="show progress")]},
        _config(),
        context=_context(),
    )

    tool_message = next(
        message for message in result["messages"] if isinstance(message, ToolMessage)
    )
    assert tool_message.tool_call_id == "compose-1"
    assert tool_message.status == "success"
    assert result["messages"][-1].content == "Done."


@pytest.mark.asyncio
async def test_invalid_composition_is_rewritten_and_model_is_reprompted() -> None:
    invalid = {"tree": [{"component": "TrendCard", "props": {"value": "literal"}}]}
    model = ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"id": "compose-1", "name": "compose_ui", "args": invalid}],
            ),
            AIMessage(content="Corrected in plain text."),
        ]
    )

    result = await build_route_b_agent(model, InMemoryStore()).ainvoke(
        {"messages": [HumanMessage(id="human-1", content="show progress")]},
        _config(),
        context=_context(),
    )

    calls = [
        call
        for message in result["messages"]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]
    assert calls[0]["args"] == {"tree": []}
    assert invalid not in [call["args"] for call in calls]
    assert any(
        isinstance(message, ToolMessage)
        and message.tool_call_id == "compose-1"
        and message.status == "error"
        for message in result["messages"]
    )
    assert result["messages"][-1].content == "Corrected in plain text."


@pytest.mark.asyncio
async def test_always_invalid_composition_stops_at_retry_cap() -> None:
    invalid_call = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "compose-invalid",
                "name": "compose_ui",
                "args": {
                    "tree": [{"component": "TrendCard", "props": {"value": "literal"}}]
                },
            }
        ],
    )
    model = ToolCapableFakeModel(responses=[invalid_call, invalid_call])

    result = await build_route_b_agent(model, InMemoryStore()).ainvoke(
        {"messages": [HumanMessage(id="human-1", content="show progress")]},
        _config(),
        context=_context(),
    )

    assert result["messages"][-1].content == SAFE_FALLBACK
    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].tool_calls == []


@pytest.mark.asyncio
async def test_change_schedule_parallel_batch_allows_only_first_interrupt() -> None:
    calls = [
        {
            "id": f"change-{index}",
            "name": "change_schedule",
            "args": {
                "request": {
                    "action": "add",
                    "date": f"2027-01-0{index}",
                    "kind": "check-in",
                }
            },
        }
        for index in (1, 2)
    ]
    model = ToolCapableFakeModel(responses=[AIMessage(content="", tool_calls=calls)])

    result = await build_route_b_agent(model, InMemoryStore()).ainvoke(
        {"messages": [HumanMessage(id="human-1", content="add two check-ins")]},
        _config(),
        context=_context(),
    )

    assert len(result["__interrupt__"]) == 1
    assert result["__interrupt__"][0].value["status"] == "pending"


@pytest.mark.asyncio
async def test_erase_coordinator_orders_remote_sweeps_before_store_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    store = InMemoryStore()

    @asynccontextmanager
    async def client_context():
        events.append("client")
        yield object()

    async def clean_crons(*_args: object) -> bool:
        events.append("crons")
        return True

    async def clean_reservations(*_args: object) -> bool:
        events.append("reservations")
        return True

    async def delete_store(*_args: object) -> None:
        events.append("store")

    monkeypatch.setattr("healthcare_rag.agent.erase.deployment_client", client_context)
    monkeypatch.setattr("healthcare_rag.agent.erase.cleanup_user_crons", clean_crons)
    monkeypatch.setattr(
        "healthcare_rag.agent.erase.sweep_upload_reservations", clean_reservations
    )
    monkeypatch.setattr("healthcare_rag.agent.erase.delete_all_for_user", delete_store)

    result = await erase_my_data({}, _config(), store=store)

    assert events == ["client", "crons", "reservations", "store"]
    assert result["messages"][0].name == ERASE_MARKER_NAME
    assert await store.aget(("users", "user-1", "gate"), "erasing") is None


def test_to_safe_message_preserves_correlation_and_drops_provider_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setattr(
        "healthcare_rag.agent.safe_message.scrub_phi",
        lambda text: (text.replace("SENTINEL", "[REDACTED_PERSON]"), []),
    )
    message = AIMessage(
        id="ai-1",
        content=[
            {"type": "text", "text": "hello SENTINEL"},
            {"type": "image", "url": "SENTINEL"},
        ],
        tool_calls=[
            {"id": "call-1", "name": "compose_ui", "args": {"note": "SENTINEL"}}
        ],
        additional_kwargs={"secret": "SENTINEL"},
        response_metadata={"provider": "secret"},
    )

    # When
    safe = to_safe_message(message)

    # Then
    assert safe.id == "ai-1"
    assert safe.content == [{"type": "text", "text": "hello [REDACTED_PERSON]"}]
    assert isinstance(safe, AIMessage)
    assert [
        {key: call[key] for key in ("id", "name", "args")} for call in safe.tool_calls
    ] == [{"id": "call-1", "name": "compose_ui", "args": {"note": "[REDACTED_PERSON]"}}]
    assert safe.additional_kwargs == {}
    assert safe.response_metadata == {}


def test_to_safe_message_preserves_tool_error_status() -> None:
    # Given
    message = ToolMessage(
        id="tool-1",
        name="compose_ui",
        content="invalid",
        tool_call_id="call-1",
        status="error",
    )

    # When
    safe = to_safe_message(message)

    # Then
    assert isinstance(safe, ToolMessage)
    assert safe.status == "error"
    assert safe.tool_call_id == "call-1"


def test_composition_requires_same_turn_resolved_fact_refs() -> None:
    # Given
    envelope = json.dumps(
        {
            "turn_scope_id": "scope-1",
            "block_id": "trend:weight",
            "data": {"label": "Weight", "value": "190", "points": [190.0]},
            "text": "Weight logged.",
        }
    )
    tree = [
        {
            "component": "TrendCard",
            "props": {
                key: {
                    "__ref": {
                        "turn_scope_id": "scope-1",
                        "block_id": "trend:weight",
                        "pointer": pointer,
                    }
                }
                for key, pointer in {
                    "label": "/label",
                    "value": "/value",
                    "points": "/points",
                }.items()
            },
        }
    ]

    # When/Then
    assert validate_composition({"tree": tree}, [envelope], "scope-1").valid
    assert not validate_composition({"tree": tree}, [envelope], "scope-2").valid
    tree[0]["props"]["value"] = "190 lb"
    assert not validate_composition({"tree": tree}, [envelope], "scope-1").valid


@pytest.mark.asyncio
async def test_namespace_sweep_enumerates_root_and_finished_child_records() -> None:
    # Given
    saver = InMemorySaver()
    root = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}
    child = {
        "configurable": {
            "thread_id": "thread-1",
            "checkpoint_ns": "coach_agent:child",
        }
    }
    await saver.aput(
        root,
        {
            "v": 4,
            "id": "root-1",
            "ts": "",
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
            "updated_channels": None,
        },
        {"parents": {}},
        {},
    )
    await saver.aput(
        child,
        {
            "v": 4,
            "id": "child-1",
            "ts": "",
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
            "updated_channels": None,
        },
        {"parents": {"": "root-1"}},
        {},
    )

    # When
    records = await checkpoint_records(saver, "thread-1")
    added = diff_records((), records)
    leaves = lineage_leaves(added)

    # Then
    assert {record.checkpoint_ns for record in records} == {"", "coach_agent:child"}
    assert [record.checkpoint_id for record in leaves["root-1"]] == ["child-1"]


def test_finalize_projects_whole_channel_without_changing_ids() -> None:
    # Given
    state = {
        "messages": [
            HumanMessage(id="human-1", content="hello"),
            AIMessage(id="ai-1", content="hi"),
        ],
        "pending_document_op_id": "terminal-private",
    }

    # When
    result = finalize_coach(state)

    # Then
    assert [message.id for message in result["messages"]] == ["human-1", "ai-1"]
    assert result["follow_ups"] == []
    assert result["pending_document_op_id"] is None
