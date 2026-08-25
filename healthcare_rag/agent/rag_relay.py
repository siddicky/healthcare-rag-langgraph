"""Bridge from the coach agent's ``medical_lookup`` tool into the healthcare graph.

The default child inherits the parent checkpointer through ``checkpointer=True``,
so healthcare history and refusal boundaries persist inside each coach thread
(under the calling tool's checkpoint namespace). ``HC_RAG_RELAY_MODE=pipeline``
is a degraded fallback: it compiles the complete healthcare graph with a fresh
in-memory saver and UUID thread for every call, so all safety and validation
stages remain active but inner multi-turn memory is lost.
"""

from __future__ import annotations

import logging
import os
from typing import Final
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from healthcare_rag.graph.build import build_graph
from healthcare_rag.graph.state import GraphOutput
from healthcare_rag.processors.safety_responses import PHI_NOTICE

logger = logging.getLogger("MedicalRAG")

RELAY_MODE_ENV: Final = "HC_RAG_RELAY_MODE"
PIPELINE_MODE: Final = "pipeline"
MONOGRAPH_INTRO: Final = "Here's what the monograph says:"
RELAY_ERROR_MESSAGE: Final = (
    "I couldn't retrieve monograph information right now. Please try again."
)


child = build_graph().compile(checkpointer=True, name="healthcare_rag_child")


def _assemble(result: GraphOutput) -> tuple[str, list[str]]:
    error = result.get("error")
    answer = result.get("answer")
    if error is not None or answer is None:
        return RELAY_ERROR_MESSAGE, []

    safety = result.get("safety") or {}
    if safety.get("short_circuited") is True:
        return answer, []

    contains_phi = safety.get("contains_phi") is True
    answer_prefix = f"{PHI_NOTICE}\n\n"
    validated = answer.removeprefix(answer_prefix) if contains_phi else answer
    sections = [MONOGRAPH_INTRO, validated]
    if contains_phi:
        sections.insert(0, PHI_NOTICE)

    follow_ups = result.get("follow_ups") or []
    message = "\n\n".join(sections)
    if follow_ups:
        message = f"{message}\n\n" + "\n".join(
            f"- {question}" for question in follow_ups
        )
    return message, follow_ups


async def relay_question(question: str, config: RunnableConfig) -> tuple[str, list[str]]:
    """Run a scrubbed question through the healthcare graph without another model call."""
    try:
        if os.getenv(RELAY_MODE_ENV, "").strip().lower() == PIPELINE_MODE:
            active_child = build_graph().compile(
                checkpointer=InMemorySaver(),
                name="healthcare_rag_pipeline_turn",
            )
            child_config: RunnableConfig = {
                "configurable": {"thread_id": str(uuid4())}
            }
        else:
            active_child = child
            child_config = config
        raw_result = await active_child.ainvoke(
            {"question": question},
            child_config,
        )
        result = GraphOutput(
            answer=raw_result.get("answer"),
            follow_ups=raw_result.get("follow_ups", []),
            safety=raw_result.get("safety"),
            error=raw_result.get("error"),
        )
    except Exception:  # noqa: BROAD_EXCEPT_OK - raw-free child boundary.
        logger.warning("RAG_RELAY_CHILD_FAILED", exc_info=True)
        result = GraphOutput(error="RAG_RELAY_CHILD_FAILED")

    return _assemble(result)


__all__ = [
    "MONOGRAPH_INTRO",
    "RELAY_ERROR_MESSAGE",
    "RELAY_MODE_ENV",
    "child",
    "relay_question",
]
