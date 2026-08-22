from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph

from healthcare_rag.agent import gate
from healthcare_rag.agent import rag_relay as relay_module
from healthcare_rag.agent.build import build_coach_graph
from healthcare_rag.agent.rag_relay import rag_relay
from healthcare_rag.agent.state import CoachState
from healthcare_rag.models.safety import SafetyAssessment
from healthcare_rag.processors.safety_responses import (
    PHI_NOTICE,
    personal_advice_response,
)


class ChildResult(TypedDict):
    answer: str | None
    follow_ups: list[str]
    safety: dict[str, bool] | None
    error: str | None


@dataclass(slots=True)  # noqa: MUTABLE_OK - scripted child records relay calls.
class ScriptedChild:
    results: list[ChildResult | RuntimeError]
    calls: list[tuple[dict[str, str], RunnableConfig]] = field(default_factory=list)

    async def ainvoke(
        self,
        state: dict[str, str],
        config: RunnableConfig,
    ) -> ChildResult:
        self.calls.append((state, config))
        result = self.results.pop(0)
        if isinstance(result, RuntimeError):
            raise result
        return result


def _result(
    answer: str | None,
    *,
    follow_ups: list[str] | None = None,
    contains_phi: bool = False,
    short_circuited: bool = False,
    error: str | None = None,
) -> ChildResult:
    return {
        "answer": answer,
        "follow_ups": follow_ups or [],
        "safety": {
            "contains_phi": contains_phi,
            "short_circuited": short_circuited,
        },
        "error": error,
    }


async def test_relay_assembles_exact_informational_message_and_propagates_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    validated = "Lipitor information. [doc_1]\nSecond validated line."
    scripted = ScriptedChild(
        [_result(validated, follow_ups=["Ask one?", "Ask two?"], contains_phi=True)]
    )
    monkeypatch.setattr(relay_module, "child", scripted)
    config: RunnableConfig = {"configurable": {"thread_id": "parent-thread"}}
    state: CoachState = {
        "messages": [HumanMessage(content="scrubbed question")]
    }

    # When
    update = await rag_relay(state, config)

    # Then
    expected = (
        f"{PHI_NOTICE}\n\nHere's what the monograph says:\n\n{validated}"
        "\n\n- Ask one?\n- Ask two?"
    )
    assert update.get("messages") == [AIMessage(content=expected)]
    assert update.get("follow_ups") == ["Ask one?", "Ask two?"]
    assert scripted.calls == [(({"question": "scrubbed question"}), config)]


async def test_relay_preserves_refusal_bytes_without_framing_or_follow_ups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    refusal = personal_advice_response()
    scripted = ScriptedChild([_result(refusal, short_circuited=True)])
    monkeypatch.setattr(relay_module, "child", scripted)
    state: CoachState = {
        "messages": [HumanMessage(content="Should I change my dose?")]
    }
    config: RunnableConfig = {"configurable": {"thread_id": "refusal"}}

    # When
    update = await rag_relay(state, config)

    # Then
    assert update.get("messages") == [AIMessage(content=refusal)]
    assert update.get("follow_ups") == []


async def test_relay_returns_safe_error_and_remains_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    scripted = ScriptedChild(
        [
            RuntimeError("raw internal failure"),
            _result("Recovered validated answer."),
        ]
    )
    monkeypatch.setattr(relay_module, "child", scripted)
    config: RunnableConfig = {"configurable": {"thread_id": "recoverable"}}
    state: CoachState = {"messages": [HumanMessage(content="question")]}

    # When
    failed = await rag_relay(state, config)
    recovered = await rag_relay(state, config)

    # Then
    assert failed.get("messages") == [
        AIMessage(content="I couldn't retrieve monograph information right now. Please try again.")
    ]
    assert "raw internal failure" not in str(failed)
    assert recovered.get("messages") == [
        AIMessage(content="Here's what the monograph says:\n\nRecovered validated answer.")
    ]


async def test_relay_makes_no_gateway_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    calls = 0

    async def gateway_spy(**_kwargs: str) -> SafetyAssessment:
        nonlocal calls
        calls += 1
        raise AssertionError("relay must not call a model gateway")

    monkeypatch.setattr(gate, "GATEWAY", gateway_spy)
    monkeypatch.setattr(relay_module, "child", ScriptedChild([_result("Validated.")]))
    state: CoachState = {"messages": [HumanMessage(content="question")]}
    config: RunnableConfig = {"configurable": {"thread_id": "no-model"}}

    # When
    _ = await rag_relay(state, config)

    # Then
    assert calls == 0


class MemoryState(TypedDict, total=False):
    question: str
    seen: list[str]
    answer: str | None
    follow_ups: list[str]
    safety: dict[str, bool] | None
    error: str | None


def _memory_child():
    async def remember(state: MemoryState) -> MemoryState:
        previous = state.get("seen", [])
        question = state.get("question", "")
        return {
            "seen": [*previous, question],
            "answer": f"history={','.join(previous) or '<empty>'}; current={question}",
            "follow_ups": [],
            "safety": {"contains_phi": False},
            "error": None,
        }

    builder = StateGraph(MemoryState, input_schema=MemoryState, output_schema=MemoryState)
    _ = builder.add_node("remember", remember)
    _ = builder.add_edge(START, "remember")
    return builder.compile(checkpointer=True, name="test_healthcare_child")


async def test_nested_child_carries_history_per_parent_thread_without_bleed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    async def classify(**_kwargs: str) -> SafetyAssessment:
        return SafetyAssessment(
            category="in_scope_informational",
            contains_phi=False,
            phi_spans=[],
            drug_mentioned="lipitor",
            rationale="scripted",
        )

    monkeypatch.setattr(gate, "GATEWAY", classify)
    def no_scrub(text: str) -> tuple[str, list[str]]:
        return text, []

    monkeypatch.setattr(gate, "scrub_phi", no_scrub)
    monkeypatch.setattr(relay_module, "child", _memory_child())
    graph = build_coach_graph().compile(checkpointer=InMemorySaver())
    config_a: RunnableConfig = {"configurable": {"thread_id": "thread-a"}}
    config_b: RunnableConfig = {"configurable": {"thread_id": "thread-b"}}

    # When
    _ = await graph.ainvoke({"question": "first"}, config_a)
    second_a = await graph.ainvoke({"question": "second"}, config_a)
    first_b = await graph.ainvoke({"question": "other"}, config_b)

    # Then
    assert second_a["messages"][-1].text.endswith("history=first; current=second")
    assert first_b["messages"][-1].text.endswith("history=<empty>; current=other")


@dataclass(slots=True)  # noqa: MUTABLE_OK - fallback fake records saver and thread ids.
class CompileRecorder:
    savers: list[InMemorySaver] = field(default_factory=list)
    thread_ids: list[str] = field(default_factory=list)

    def compile(self, *, checkpointer: InMemorySaver, name: str):
        del name
        self.savers.append(checkpointer)
        recorder = self

        class Compiled:
            def __init__(self) -> None:
                self.seen: list[str] = []

            async def ainvoke(
                self,
                state: dict[str, str],
                config: RunnableConfig,
            ) -> ChildResult:
                configurable = config.get("configurable", {})
                recorder.thread_ids.append(str(configurable.get("thread_id", "")))
                answer = f"history={','.join(self.seen) or '<empty>'}"
                self.seen.append(state["question"])
                return _result(answer)

        return Compiled()


async def test_pipeline_mode_compiles_fresh_saver_and_thread_each_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    recorder = CompileRecorder()
    monkeypatch.setenv("HC_RAG_RELAY_MODE", "pipeline")
    monkeypatch.setattr(relay_module, "build_graph", lambda: recorder)
    state: CoachState = {"messages": [HumanMessage(content="question")]}
    config: RunnableConfig = {"configurable": {"thread_id": "parent"}}

    # When
    first = await rag_relay(state, config)
    second = await rag_relay(state, config)

    # Then
    assert len(recorder.savers) == 2
    assert recorder.savers[0] is not recorder.savers[1]
    assert len(set(recorder.thread_ids)) == 2
    assert "parent" not in recorder.thread_ids
    expected = AIMessage(content="Here's what the monograph says:\n\nhistory=<empty>")
    assert first.get("messages") == [expected]
    assert second.get("messages") == [expected]
