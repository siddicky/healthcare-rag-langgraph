"""Manual, opt-in network probe for the ChatOpenAI (langchain-openai) integration.

This is NOT part of ``make test`` and is never collected by pytest: it is a
manual network probe that spends real OpenAI money (~$0.01 across both model
tiers). Run it by hand after dependency or model changes:

    .venv/bin/python scripts/probe_chat_openai.py

Loads ``OPENAI_API_KEY`` from ``.env`` (the value is never printed) and verifies,
for BOTH model tiers (``default_llm_model()`` / ``default_validator_model()``):

  (a) ChatOpenAI(use_responses_api=False, max_retries=3, reasoning_effort="none",
      temperature=0.1) round-trips a trivial completion
  (b) reasoning_effort="low" WITHOUT temperature is accepted
  (c) with_structured_output(SafetyAssessment, method="json_schema") returns an
      isinstance-validated SafetyAssessment
  (d) the same with strict=True
  (e) bind_tools with the two routing tool schemas yields a tool_calls AIMessage
  (f) usage_metadata is populated on the raw response (include_raw=True)
  (g) a callback handler passed via config={"callbacks": [...]} receives on_llm_end

Prints one OK/FAIL line per check; exits 0 only when every check passes.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any
from uuid import UUID

from dotenv import load_dotenv
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult
from langchain_openai import ChatOpenAI

from healthcare_rag.models.safety import SafetyAssessment
from healthcare_rag.services.models import default_llm_model, default_validator_model

ROUTING_TOOLS = [
    # INLINE copies of the two routing tool schemas built in
    # healthcare_rag/processors/retrieval.py:43-64 — keep in sync; production
    # extraction lands in todo 5.
    {
        "type": "function",
        "function": {
            "name": "query_lipitor",
            "description": "Get information about Lipitor from the Weaviate database",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The query from the user (verbatim) that should pertain to Lipitor",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_metformin",
            "description": "Get information about Metformin from the Weaviate database",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The query from the user (verbatim) that should pertain to Metformin",
                    }
                },
                "required": ["query"],
            },
        },
    },
]

PING = "Reply with exactly one word: pong"
ROUTING_QUERY = "What are Lipitor side effects?"
GATE_PROMPT = (
    "Classify this user message for the medicine-information safety gate: "
    "'What are the side effects of Lipitor?'"
)


class _LLMEndRecorder(BaseCallbackHandler):
    """Counts on_llm_end callbacks (check g)."""

    llm_end_count: int

    def __init__(self) -> None:
        self.llm_end_count = 0

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self.llm_end_count += 1


def _chat(model: str, **overrides: Any) -> ChatOpenAI:
    kwargs: dict[str, Any] = {
        "use_responses_api": False,
        "max_retries": 3,
        "reasoning_effort": "none",
        "temperature": 0.1,
    }
    kwargs.update(overrides)
    return ChatOpenAI(model=model, **kwargs)


def _tier_checks(model: str) -> list[tuple[str, Callable[[], None]]]:
    def plain_roundtrip() -> None:
        reply = _chat(model).invoke(PING)
        assert isinstance(reply, AIMessage) and reply.content, "empty completion"

    def low_effort_without_temperature() -> None:
        # temperature=None omits the parameter: GPT-5.x rejects it unless
        # reasoning_effort == "none" (mirrors services/models.py sampling_params).
        _ = _chat(model, reasoning_effort="low", temperature=None).invoke(PING)

    def structured() -> None:
        runnable = _chat(model).with_structured_output(SafetyAssessment, method="json_schema")
        result = runnable.invoke(GATE_PROMPT)
        assert isinstance(result, SafetyAssessment), f"got {type(result)!r}"

    def structured_strict() -> None:
        runnable = _chat(model).with_structured_output(
            SafetyAssessment, method="json_schema", strict=True
        )
        result = runnable.invoke(GATE_PROMPT)
        assert isinstance(result, SafetyAssessment), f"got {type(result)!r}"

    def routing_tools() -> None:
        msg = _chat(model).bind_tools(ROUTING_TOOLS).invoke(ROUTING_QUERY)
        assert isinstance(msg, AIMessage) and msg.tool_calls, "model returned no tool_calls"

    def usage_metadata() -> None:
        runnable = _chat(model).with_structured_output(
            SafetyAssessment, method="json_schema", include_raw=True
        )
        result = runnable.invoke(GATE_PROMPT)
        usage: Any = result["raw"].usage_metadata
        assert usage and usage.get("input_tokens"), "usage_metadata not populated on raw response"

    def callbacks() -> None:
        recorder = _LLMEndRecorder()
        _ = _chat(model).invoke(PING, config={"callbacks": [recorder]})
        assert recorder.llm_end_count >= 1, "on_llm_end never fired"

    return [
        ("(a) plain round-trip, effort=none + temperature", plain_roundtrip),
        ("(b) effort=low without temperature", low_effort_without_temperature),
        ("(c) structured output, method=json_schema", structured),
        ("(d) structured output, method=json_schema + strict=True", structured_strict),
        ("(e) bind_tools -> tool_calls AIMessage", routing_tools),
        ("(f) usage_metadata on raw response (include_raw=True)", usage_metadata),
        ("(g) config callbacks receive on_llm_end", callbacks),
    ]


def main() -> int:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        print("FAIL: OPENAI_API_KEY is not set — add it to .env or the environment (value never printed)")
        return 2

    failures: list[str] = []
    for tier, model in (("llm", default_llm_model()), ("validator", default_validator_model())):
        for label, fn in _tier_checks(model):
            full_label = f"[{tier}:{model}] {label}"
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 — report the failing check, keep probing
                failures.append(full_label)
                print(f"FAIL {full_label}: {type(exc).__name__}: {exc}")
            else:
                print(f"OK   {full_label}")

    if failures:
        print(f"\n{len(failures)} check(s) failed")
        return 1
    print("\nAll checks passed for both model tiers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
