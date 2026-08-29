from __future__ import annotations

import json
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any, Self, TypedDict

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from pydantic import Field
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.store.memory import InMemoryStore

from healthcare_rag.agent import rag_relay as relay_module
from healthcare_rag.agent.build import build_coach_graph
from healthcare_rag.agent.coach_agent import (
    BASE_PROMPT,
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


class PromptCapturingFakeModel(ToolCapableFakeModel):
    """Records the system prompt seen on every model call (for dynamic-prompt checks)."""

    seen_system_prompts: list[str] = Field(default_factory=list)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        system = next(
            (
                message.content
                for message in messages
                if isinstance(message, SystemMessage)
            ),
            "",
        )
        self.seen_system_prompts.append(str(system))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


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
async def test_medical_lookup_round_trip_relays_answer_and_calls_model_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    call_count = 0

    async def fake_relay(question: str, config: RunnableConfig) -> tuple[str, list[str]]:
        nonlocal call_count
        call_count += 1
        del config
        return f"Here's what the monograph says:\n\n{question} answer.", ["Follow-up?"]

    monkeypatch.setattr(
        "healthcare_rag.agent.tools.medical_lookup.relay_question", fake_relay
    )
    model = ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "lookup-1",
                        "name": "medical_lookup",
                        "args": {"query": "metformin side effects"},
                    }
                ],
            )
        ]
    )

    # When
    result = await build_route_b_agent(model, InMemoryStore()).ainvoke(
        {
            "messages": [
                HumanMessage(id="human-1", content="what are metformin side effects")
            ]
        },
        _config(),
        context=_context(),
    )

    # Then
    assert call_count == 1
    tool_message = next(
        message for message in result["messages"] if isinstance(message, ToolMessage)
    )
    assert tool_message.name == "medical_lookup"
    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].content == (
        "Here's what the monograph says:\n\nmetformin side effects answer."
    )


@pytest.mark.asyncio
async def test_medical_lookup_call_strips_any_accompanying_assistant_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    async def fake_relay(question: str, config: RunnableConfig) -> tuple[str, list[str]]:
        del question, config
        return "Here's what the monograph says:\n\nanswer.", []

    monkeypatch.setattr(
        "healthcare_rag.agent.tools.medical_lookup.relay_question", fake_relay
    )
    model = ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="Let me check the monograph for you — metformin can cause nausea.",
                tool_calls=[
                    {
                        "id": "lookup-1",
                        "name": "medical_lookup",
                        "args": {"query": "what are metformin side effects"},
                    }
                ],
            )
        ]
    )

    # When
    result = await build_route_b_agent(model, InMemoryStore()).ainvoke(
        {
            "messages": [
                HumanMessage(id="human-1", content="what are metformin side effects")
            ]
        },
        _config(),
        context=_context(),
    )

    # Then
    ai_messages = [
        message for message in result["messages"] if isinstance(message, AIMessage)
    ]
    assert len(ai_messages) == 2
    call_message, relayed_message = ai_messages
    assert call_message.content == ""
    assert "nausea" not in call_message.content
    assert relayed_message.content == "Here's what the monograph says:\n\nanswer."


@pytest.mark.asyncio
async def test_mixed_medical_lookup_call_drops_other_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    async def fake_relay(question: str, config: RunnableConfig) -> tuple[str, list[str]]:
        del question, config
        return "Here's what the monograph says:\n\nanswer.", []

    monkeypatch.setattr(
        "healthcare_rag.agent.tools.medical_lookup.relay_question", fake_relay
    )
    model = ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "compose-1", "name": "compose_ui", "args": {"tree": []}},
                    {
                        "id": "lookup-1",
                        "name": "medical_lookup",
                        "args": {"query": "what are metformin side effects"},
                    },
                ],
            )
        ]
    )

    # When
    result = await build_route_b_agent(model, InMemoryStore()).ainvoke(
        {
            "messages": [
                HumanMessage(id="human-1", content="what are metformin side effects")
            ]
        },
        _config(),
        context=_context(),
    )

    # Then
    tool_messages = [
        message for message in result["messages"] if isinstance(message, ToolMessage)
    ]
    assert [message.name for message in tool_messages] == ["medical_lookup"]
    assert result["messages"][-1].content == "Here's what the monograph says:\n\nanswer."


class _MemoryChildState(TypedDict, total=False):
    question: str
    seen: list[str]
    answer: str | None
    follow_ups: list[str]
    safety: dict[str, bool] | None
    error: str | None


def _memory_child():
    async def remember(state: _MemoryChildState) -> _MemoryChildState:
        previous = state.get("seen", [])
        question = state.get("question", "")
        return {
            "seen": [*previous, question],
            "answer": f"history={','.join(previous) or '<empty>'}; current={question}",
            "follow_ups": [],
            "safety": {"contains_phi": False},
            "error": None,
        }

    builder = StateGraph(
        _MemoryChildState, input_schema=_MemoryChildState, output_schema=_MemoryChildState
    )
    _ = builder.add_node("remember", remember)
    _ = builder.add_edge(START, "remember")
    return builder.compile(checkpointer=True, name="test_healthcare_child")


@pytest.mark.asyncio
async def test_nested_medical_lookup_child_carries_history_per_parent_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setattr(relay_module, "child", _memory_child())
    turns = iter(["first", "second", "other"])

    class _StubGateway:
        def chat_model(self, *_args: object, **_kwargs: object) -> ToolCapableFakeModel:
            question = next(turns)
            return ToolCapableFakeModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": f"lookup-{question}",
                                "name": "medical_lookup",
                                "args": {"query": question},
                            }
                        ],
                    )
                ]
            )

    class _StubResources:
        gateway = _StubGateway()

    monkeypatch.setattr(
        "healthcare_rag.agent.coach_agent.get_resources", lambda: _StubResources()
    )
    graph = build_coach_graph().compile(checkpointer=InMemorySaver())
    config_a: RunnableConfig = {
        "configurable": {
            "thread_id": "thread-a",
            "langgraph_auth_user": {"identity": "user-1"},
        }
    }
    config_b: RunnableConfig = {
        "configurable": {
            "thread_id": "thread-b",
            "langgraph_auth_user": {"identity": "user-1"},
        }
    }

    # When
    _ = await graph.ainvoke({"question": "first"}, config_a)
    second_a = await graph.ainvoke({"question": "second"}, config_a)
    first_b = await graph.ainvoke({"question": "other"}, config_b)

    # Then
    assert second_a["messages"][-1].text.endswith("history=first; current=second")
    assert first_b["messages"][-1].text.endswith("history=<empty>; current=other")


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


# ---------------------------------------------------------------------------
# Middleware-stack behaviour-neutrality characterizations.
#
# These five tests pin the observable behaviour of every downstream middleware
# projection. They were written against the stack WITHOUT CopilotKitMiddleware
# and must keep passing with it inserted outermost — proving that adding it
# changes nothing in state (behaviour-neutral), not merely that it imports.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_medical_lookup_round_trip_still_relays_verbatim_and_fires_relay_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    async def fake_relay(question: str, config: RunnableConfig) -> tuple[str, list[str]]:
        del config
        return f"Here's what the monograph says:\n\n{question} answer.", ["Follow-up?"]

    monkeypatch.setattr(
        "healthcare_rag.agent.tools.medical_lookup.relay_question", fake_relay
    )
    model = ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "lookup-1",
                        "name": "medical_lookup",
                        "args": {"query": "lipitor interactions"},
                    }
                ],
            )
        ]
    )

    # When
    result = await build_route_b_agent(model, InMemoryStore()).ainvoke(
        {
            "messages": [
                HumanMessage(id="human-1", content="what are lipitor interactions")
            ]
        },
        _config(),
        context=_context(),
    )

    # Then: relay_medical_answer fired — terminal ToolMessage became an AIMessage
    # carrying the tool output verbatim (no paraphrase).
    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].content == (
        "Here's what the monograph says:\n\nlipitor interactions answer."
    )
    tool_message = next(
        message for message in result["messages"] if isinstance(message, ToolMessage)
    )
    assert tool_message.name == "medical_lookup"
    assert tool_message.tool_call_id == "lookup-1"


@pytest.mark.asyncio
async def test_mixed_call_guard_still_drops_sibling_calls_alongside_medical_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    async def fake_relay(question: str, config: RunnableConfig) -> tuple[str, list[str]]:
        del question, config
        return "Here's what the monograph says:\n\nanswer.", []

    monkeypatch.setattr(
        "healthcare_rag.agent.tools.medical_lookup.relay_question", fake_relay
    )
    model = ToolCapableFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "metric-1", "name": "log_metric", "args": {}},
                    {
                        "id": "lookup-1",
                        "name": "medical_lookup",
                        "args": {"query": "metformin dosing"},
                    },
                ],
            )
        ]
    )

    # When
    result = await build_route_b_agent(model, InMemoryStore()).ainvoke(
        {
            "messages": [
                HumanMessage(id="human-1", content="log my metric and metformin dosing")
            ]
        },
        _config(),
        context=_context(),
    )

    # Then: only medical_lookup executed; the sibling call never ran.
    tool_messages = [
        message for message in result["messages"] if isinstance(message, ToolMessage)
    ]
    assert [message.name for message in tool_messages] == ["medical_lookup"]
    assert all(message.tool_call_id != "metric-1" for message in tool_messages)
    assert result["messages"][-1].content == "Here's what the monograph says:\n\nanswer."


@pytest.mark.asyncio
async def test_invalid_composition_rewrites_then_second_offense_hits_safe_fallback() -> (
    None
):
    # Given: the same invalid compose_ui call twice — first offense rewritten,
    # second offense terminal.
    invalid_args = {
        "tree": [{"component": "TrendCard", "props": {"value": "literal"}}]
    }
    invalid_call = AIMessage(
        content="",
        tool_calls=[
            {"id": "compose-bad", "name": "compose_ui", "args": invalid_args}
        ],
    )
    model = ToolCapableFakeModel(responses=[invalid_call, invalid_call])

    # When
    result = await build_route_b_agent(model, InMemoryStore()).ainvoke(
        {"messages": [HumanMessage(id="human-1", content="show progress")]},
        _config(),
        context=_context(),
    )

    # Then: first offense was rewritten to an empty tree with an error ToolMessage…
    ai_messages = [
        message for message in result["messages"] if isinstance(message, AIMessage)
    ]
    rewritten = [
        call
        for message in ai_messages
        for call in message.tool_calls
        if call["id"] == "compose-bad"
    ]
    assert rewritten == [
        {
            "name": "compose_ui",
            "id": "compose-bad",
            "args": {"tree": []},
            "type": "tool_call",
        }
    ]
    assert any(
        isinstance(message, ToolMessage)
        and message.name == "compose_ui"
        and message.status == "error"
        and message.tool_call_id == "compose-bad"
        for message in result["messages"]
    )
    # …and the second offense terminated with SAFE_FALLBACK, no tool calls.
    last = result["messages"][-1]
    assert isinstance(last, AIMessage)
    assert last.content == SAFE_FALLBACK
    assert last.tool_calls == []


@pytest.mark.asyncio
async def test_change_schedule_run_limit_blocks_second_parallel_call_with_error_tool_message() -> (
    None
):
    # Given: two change_schedule calls in one step; run_limit=1 blocks the second.
    calls = [
        {
            "id": f"change-{index}",
            "name": "change_schedule",
            "args": {
                "request": {
                    "action": "add",
                    "date": f"2027-02-0{index}",
                    "kind": "check-in",
                }
            },
        }
        for index in (1, 2)
    ]
    model = ToolCapableFakeModel(responses=[AIMessage(content="", tool_calls=calls)])

    # When
    result = await build_route_b_agent(model, InMemoryStore()).ainvoke(
        {"messages": [HumanMessage(id="human-1", content="add two check-ins")]},
        _config(),
        context=_context(),
    )

    # Then: only the first call reached the interrupt; the second was blocked by
    # the limiter with an error ToolMessage and never scheduled.
    blocked = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage)
        and message.name == "change_schedule"
        and message.status == "error"
    ]
    assert [message.tool_call_id for message in blocked] == ["change-2"]
    assert len(result["__interrupt__"]) == 1
    assert result["__interrupt__"][0].value["status"] == "pending"


@pytest.mark.asyncio
async def test_memory_segment_still_appended_after_base_prompt() -> None:
    # Given: a stored profile fact the dynamic prompt should surface.
    store = InMemoryStore()
    await store.aput(
        ("users", "user-1", "profile"),
        "pref-1",
        {"fact": "prefers morning workouts"},
    )
    model = PromptCapturingFakeModel(responses=[AIMessage(content="Noted.")])

    # When
    result = await build_route_b_agent(model, store).ainvoke(
        {"messages": [HumanMessage(id="human-1", content="hello")]},
        _config(),
        context=_context(),
    )

    # Then: the system prompt is BASE_PROMPT plus the memory segment, unchanged.
    assert result["messages"][-1].content == "Noted."
    assert model.seen_system_prompts
    for prompt in model.seen_system_prompts:
        assert prompt.startswith(BASE_PROMPT)
        assert "## Saved user memories" in prompt
        assert "- prefers morning workouts" in prompt


def _named_context(name: str | None) -> AgentContext:
    return AgentContext(
        user_id="user-1",
        thread_id="thread-1",
        human_msg_id="human-1",
        display_name=name,
    )


@pytest.mark.asyncio
async def test_memory_segment_renders_member_name_without_saved_memories() -> None:
    # Given: a display name on the context and an empty memory store.
    store = InMemoryStore()
    model = PromptCapturingFakeModel(responses=[AIMessage(content="Hi.")])

    # When
    result = await build_route_b_agent(model, store).ainvoke(
        {"messages": [HumanMessage(id="human-1", content="hello")]},
        _config(),
        context=_named_context("Dana"),
    )

    # Then: the name renders as its own section; no memory section appears.
    assert result["messages"][-1].content == "Hi."
    assert model.seen_system_prompts
    for prompt in model.seen_system_prompts:
        assert prompt.startswith(BASE_PROMPT)
        assert "## Member context" in prompt
        assert "Dana" in prompt
        assert "## Saved user memories" not in prompt


@pytest.mark.asyncio
async def test_memory_segment_renders_name_and_facts_as_separate_sections() -> None:
    # Given: a stored profile fact and a display name on the context.
    store = InMemoryStore()
    await store.aput(
        ("users", "user-1", "profile"),
        "pref-1",
        {"fact": "prefers morning workouts"},
    )
    model = PromptCapturingFakeModel(responses=[AIMessage(content="Noted.")])

    # When
    result = await build_route_b_agent(model, store).ainvoke(
        {"messages": [HumanMessage(id="human-1", content="hello")]},
        _config(),
        context=_named_context("Dana"),
    )

    # Then: both sections render, member context first, facts unchanged.
    assert result["messages"][-1].content == "Noted."
    assert model.seen_system_prompts
    for prompt in model.seen_system_prompts:
        assert prompt.startswith(BASE_PROMPT)
        assert prompt.index("## Member context") < prompt.index(
            "## Saved user memories"
        )
        assert "Dana" in prompt
        assert "- prefers morning workouts" in prompt


@pytest.mark.asyncio
async def test_memory_segment_without_name_or_facts_is_exactly_base_prompt() -> None:
    # Given: no display name and an empty store (the pre-change context shape).
    store = InMemoryStore()
    model = PromptCapturingFakeModel(responses=[AIMessage(content="Hello!")])

    # When
    result = await build_route_b_agent(model, store).ainvoke(
        {"messages": [HumanMessage(id="human-1", content="hello")]},
        _config(),
        context=_context(),
    )

    # Then: every model call sees exactly BASE_PROMPT -- byte-identical to before.
    assert result["messages"][-1].content == "Hello!"
    assert model.seen_system_prompts
    for prompt in model.seen_system_prompts:
        assert prompt == BASE_PROMPT


@pytest.mark.asyncio
async def test_coach_agent_threads_display_name_from_principal_into_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the full coach graph with a stub gateway capturing prompts.
    model = PromptCapturingFakeModel(responses=[AIMessage(content="Hi.")])

    class _StubGateway:
        def chat_model(self, *_args: object, **_kwargs: object) -> PromptCapturingFakeModel:
            return model

    class _StubResources:
        gateway = _StubGateway()

    monkeypatch.setattr(
        "healthcare_rag.agent.coach_agent.get_resources", lambda: _StubResources()
    )
    graph = build_coach_graph().compile(checkpointer=InMemorySaver())
    config: RunnableConfig = {
        "configurable": {
            "thread_id": "thread-a",
            "langgraph_auth_user": {
                "identity": "user-1",
                "member_display_name": "Dana",
            },
        }
    }

    # When
    _ = await graph.ainvoke({"question": "hello"}, config)

    # Then: the principal's display name reached the model's system prompt.
    assert model.seen_system_prompts
    for prompt in model.seen_system_prompts:
        assert "## Member context" in prompt
        assert "Dana" in prompt
