from __future__ import annotations

from langchain.tools import ToolRuntime, tool
from langsmith.run_helpers import get_current_run_tree

from healthcare_rag.agent.rag_relay import relay_question
from healthcare_rag.agent.state import CoachState


@tool("medical_lookup", return_direct=True, response_format="content_and_artifact")
async def medical_lookup(
    query: str, runtime: ToolRuntime[None, CoachState]
) -> tuple[str, dict[str, object]]:
    """Answer a question about a medication (dose, side effects, interactions,
    warnings, what the monograph says). Result is shown to the member exactly
    as returned — never paraphrase or repeat it yourself."""
    message, follow_ups = await relay_question(query, runtime.config)
    artifact: dict[str, object] = {"follow_ups": follow_ups}
    run_tree = get_current_run_tree()
    if run_tree is not None:
        # Conventional feedback linkage: `relay_medical_answer` stamps this
        # onto the relayed AIMessage id so `post_feedback` can resolve the
        # coach trace (see `healthcare_rag.agent.feedback._model_run_id`).
        artifact["source_run_id"] = str(run_tree.id)
    return message, artifact


__all__ = ["medical_lookup"]
