"""Checkpoint-message views mirroring legacy history.py:69,92-99."""

from datetime import datetime, timezone
from typing import TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages

from healthcare_rag.processors.safety import scrub_phi


class ProcessedHistoryEntry(TypedDict):
    timestamp: str | None
    user_query: str
    answer: str


class LegacyTurn(TypedDict, total=False):
    timestamp: str | datetime
    user_query: str
    answer: str


def _scrub_message(message: BaseMessage, gate_on: bool) -> BaseMessage:
    if not gate_on:
        return message
    clean, _ = scrub_phi(str(message.content))
    return message.model_copy(update={"content": clean})


def _pairs(messages: list[BaseMessage]) -> list[ProcessedHistoryEntry]:
    pairs: list[ProcessedHistoryEntry] = []
    human: HumanMessage | None = None
    for message in messages:
        if isinstance(message, HumanMessage):
            human = message
        elif isinstance(message, AIMessage) and human is not None:
            timestamp = human.additional_kwargs.get("ts")
            pairs.append(
                {
                    "timestamp": timestamp if isinstance(timestamp, str) else None,
                    "user_query": str(human.content),
                    "answer": str(message.content),
                }
            )
            human = None
    return pairs


def build_history_views(
    messages: list[BaseMessage],
    max_tokens: int,
    gate_on: bool,
) -> tuple[str, list[ProcessedHistoryEntry]]:
    """Scrub, token-cap, then derive the two legacy history windows."""
    scrubbed = [_scrub_message(message, gate_on) for message in messages]
    trimmed = trim_messages(
        scrubbed,
        strategy="last",
        token_counter=count_tokens_approximately,
        max_tokens=max_tokens,
        start_on="human",
        include_system=True,
    )
    pairs = _pairs(list(trimmed))
    processed = list(reversed(pairs[-5:]))
    return render_history_text(processed), processed


def seed_messages(turns: list[LegacyTurn] | None) -> list[BaseMessage]:
    """Convert legacy persisted turns into scrubbed checkpoint messages."""
    messages: list[BaseMessage] = []
    for turn in turns or []:
        timestamp = turn.get("timestamp")
        if isinstance(timestamp, datetime):
            timestamp = timestamp.isoformat()
        if not isinstance(timestamp, str):
            timestamp = datetime.now(timezone.utc).isoformat()
        query, _ = scrub_phi(str(turn.get("user_query", "")))
        answer, _ = scrub_phi(str(turn.get("answer", "")))
        kwargs = {"ts": timestamp}
        messages.extend(
            [
                HumanMessage(content=query, additional_kwargs=kwargs),
                AIMessage(content=answer, additional_kwargs=kwargs),
            ]
        )
    return messages


def render_history_text(processed: list[ProcessedHistoryEntry]) -> str:
    """Render the last three exchanges oldest-first as legacy context."""
    if not processed:
        return ""
    context = "Previous conversation:\n"
    for entry in reversed(processed[:3]):
        context += f"User: {entry['user_query']}\n"
        context += f"Assistant: {entry['answer']}\n\n"
    return context


def render_followup_history(processed_history: list[ProcessedHistoryEntry]) -> str:
    """Mirror followups.py:32-38 using at most five newest-first entries."""
    if not processed_history:
        return ""
    context = "Previous conversation:\n"
    for entry in processed_history[:5]:
        context += f"User: {entry['user_query']}\n"
        context += f"Assistant: {entry['answer']}\n\n"
    return context
