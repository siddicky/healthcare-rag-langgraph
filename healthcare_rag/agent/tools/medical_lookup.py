from __future__ import annotations

from langchain.tools import ToolRuntime, tool

from healthcare_rag.agent.rag_relay import relay_question
from healthcare_rag.agent.state import CoachState


@tool("medical_lookup", return_direct=True, response_format="content_and_artifact")
async def medical_lookup(
    query: str, runtime: ToolRuntime[None, CoachState]
) -> tuple[str, dict[str, list[str]]]:
    """Answer a question about a medication (dose, side effects, interactions,
    warnings, what the monograph says). Result is shown to the member exactly
    as returned — never paraphrase or repeat it yourself."""
    message, follow_ups = await relay_question(query, runtime.config)
    return message, {"follow_ups": follow_ups}


__all__ = ["medical_lookup"]
