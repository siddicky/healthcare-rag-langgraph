"""Shared helpers for graph nodes."""


def render_display_answer(validated: str | None, notices: list[str]) -> str:
    """Mirror SafetyDecision.prefix_notices from safety.py:522-526."""
    if not notices or not validated:
        return validated or ""
    return "\n\n".join([*notices, validated])
