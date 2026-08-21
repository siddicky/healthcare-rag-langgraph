from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from healthcare_rag.processors.safety import (
    identifier_recall_requested,
    injection_flags,
    red_flag_terms,
)
from healthcare_rag.processors.safety_responses import (
    emergency_response,
    identifier_recall_response,
    injection_response,
    out_of_scope_response,
)

from .state import CoachState


def short_circuit(state: CoachState) -> CoachState:
    """Render the already-classified safety outcome without a model call."""
    if any(isinstance(message, AIMessage) for message in state.get("messages", [])):
        return {"follow_ups": []}
    human = next(
        (
            message
            for message in reversed(state.get("messages", []))
            if isinstance(message, HumanMessage)
        ),
        None,
    )
    question = str(human.content) if human is not None else ""
    if red_flag_terms(question):
        body = emergency_response(overdose="overdose" in question.casefold())
    elif injection_flags(question):
        body = injection_response()
    elif identifier_recall_requested(question):
        body = identifier_recall_response()
    else:
        body = out_of_scope_response()
    return {"messages": [AIMessage(content=body)], "follow_ups": []}


__all__ = ["short_circuit"]
