from __future__ import annotations

from .safe_message import to_safe_message
from .state import CoachState


def finalize_coach(state: CoachState) -> CoachState:
    """Apply the final whole-channel projection and clear terminal document state."""
    return {
        "messages": [to_safe_message(message) for message in state.get("messages", [])],
        "follow_ups": state.get("follow_ups", []),
        "pending_document_op_id": None,
    }


__all__ = ["finalize_coach"]
