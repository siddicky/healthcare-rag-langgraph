from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from healthcare_rag.agent import rag_relay as relay_module
from healthcare_rag.agent.rag_relay import relay_question
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


@pytest.mark.asyncio
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

    # When
    message, follow_ups = await relay_question("scrubbed question", config)

    # Then
    expected = (
        f"{PHI_NOTICE}\n\nHere's what the monograph says:\n\n{validated}"
        "\n\n- Ask one?\n- Ask two?"
    )
    assert message == expected
    assert follow_ups == ["Ask one?", "Ask two?"]
    assert scripted.calls == [(({"question": "scrubbed question"}), config)]


@pytest.mark.asyncio
async def test_relay_preserves_refusal_bytes_without_framing_or_follow_ups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    refusal = personal_advice_response()
    scripted = ScriptedChild([_result(refusal, short_circuited=True)])
    monkeypatch.setattr(relay_module, "child", scripted)
    config: RunnableConfig = {"configurable": {"thread_id": "refusal"}}

    # When
    message, follow_ups = await relay_question("Should I change my dose?", config)

    # Then
    assert message == refusal
    assert follow_ups == []


@pytest.mark.asyncio
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

    # When
    failed, _ = await relay_question("question", config)
    recovered, _ = await relay_question("question", config)

    # Then
    assert failed == "I couldn't retrieve monograph information right now. Please try again."
    assert "raw internal failure" not in failed
    assert recovered == "Here's what the monograph says:\n\nRecovered validated answer."


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


@pytest.mark.asyncio
async def test_pipeline_mode_compiles_fresh_saver_and_thread_each_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    recorder = CompileRecorder()
    monkeypatch.setenv("HC_RAG_RELAY_MODE", "pipeline")
    monkeypatch.setattr(relay_module, "build_graph", lambda: recorder)
    config: RunnableConfig = {"configurable": {"thread_id": "parent"}}

    # When
    first = await relay_question("question", config)
    second = await relay_question("question", config)

    # Then
    assert len(recorder.savers) == 2
    assert recorder.savers[0] is not recorder.savers[1]
    assert len(set(recorder.thread_ids)) == 2
    assert "parent" not in recorder.thread_ids
    expected = ("Here's what the monograph says:\n\nhistory=<empty>", [])
    assert first == expected
    assert second == expected
