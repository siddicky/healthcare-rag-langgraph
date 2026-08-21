from __future__ import annotations

from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage

from healthcare_rag.graph.nodes import render_display_answer
from healthcare_rag.graph.state import RAGState
from healthcare_rag.processors.safety import scrub_phi


async def finalize(state: RAGState) -> RAGState:
    safety_response = state.get("safety_response", "")
    if safety_response:
        answer = "\n\n".join(
            part for part in [*state.get("safety_notices", []), safety_response] if part
        )
        follow_ups: list[str] = []
    else:
        answer = render_display_answer(
            state.get("validated"),
            state.get("safety_notices", []),
        )
        follow_ups = state.get("follow_ups", [])

    answer = scrub_phi(answer)[0]
    follow_ups = [scrub_phi(question)[0] for question in follow_ups]
    selected_branch_query = state.get("selected_branch_query")
    if selected_branch_query is None:
        selected_branch_query = state.get("working_query")
    selected_branch_query = scrub_phi(selected_branch_query or "")[0] or None
    update: RAGState = {
        "answer": answer or None,
        "follow_ups": follow_ups,
        "selected_branch_query": selected_branch_query,
    }
    if answer:
        timestamp = datetime.now(timezone.utc).isoformat()
        update["messages"] = [
            HumanMessage(
                content=state.get("scrubbed_question", ""),
                additional_kwargs={"ts": timestamp},
            ),
            AIMessage(content=answer, additional_kwargs={"ts": timestamp}),
        ]
    return update
