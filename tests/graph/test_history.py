from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage, HumanMessage

from healthcare_rag.graph.history import (
    build_history_views,
    render_followup_history,
    seed_messages,
)
from healthcare_rag.graph.nodes import render_display_answer


def _turns(count: int) -> list[dict]:
    start = datetime(2026, 8, 19, tzinfo=timezone.utc)
    return [
        {
            "timestamp": (start + timedelta(minutes=index)).isoformat(),
            "user_query": f"question {index}",
            "answer": f"answer {index}",
        }
        for index in range(count)
    ]


def test_build_history_views_preserves_legacy_windows_and_order() -> None:
    # Given
    messages = seed_messages(_turns(6))

    # When
    context, processed = build_history_views(messages, max_tokens=4_000, gate_on=False)

    # Then
    assert context == (
        "Previous conversation:\n"
        "User: question 3\nAssistant: answer 3\n\n"
        "User: question 4\nAssistant: answer 4\n\n"
        "User: question 5\nAssistant: answer 5\n\n"
    )
    assert [entry["user_query"] for entry in processed] == [
        "question 5",
        "question 4",
        "question 3",
        "question 2",
        "question 1",
    ]
    assert all(
        isinstance(entry["timestamp"], str)
        and datetime.fromisoformat(entry["timestamp"]) is not None
        for entry in processed
    )


def test_build_history_views_scrubs_stored_phi_when_gate_is_on() -> None:
    # Given
    messages = [
        HumanMessage(
            content="My name is John Smith and my MRN 12345",
            additional_kwargs={"ts": "2026-08-19T00:00:00+00:00"},
        ),
        AIMessage(
            content="Patient John Smith uses MRN 12345",
            additional_kwargs={"ts": "2026-08-19T00:00:00+00:00"},
        ),
    ]

    # When
    context, processed = build_history_views(messages, max_tokens=4_000, gate_on=True)

    # Then
    rendered = context + repr(processed)
    assert "John Smith" not in rendered
    assert "12345" not in rendered
    assert "[REDACTED_" in rendered


def test_build_history_views_applies_token_cap_before_windows() -> None:
    # Given
    messages = seed_messages(_turns(50))

    # When
    context, processed = build_history_views(messages, max_tokens=30, gate_on=False)

    # Then
    assert 0 < len(processed) < 5
    assert processed[0]["user_query"] == "question 49"
    assert "question 0" not in context


def test_seed_messages_scrubs_identifiers() -> None:
    # Given
    turns = [
        {
            "user_query": "My name is John Smith and my MRN 12345",
            "answer": "Patient John Smith uses MRN 12345",
        }
    ]

    # When
    messages = seed_messages(turns)

    # Then
    contents = " ".join(str(message.content) for message in messages)
    assert "John Smith" not in contents
    assert "12345" not in contents
    assert contents.count("[REDACTED_") >= 2
    assert all(isinstance(message.additional_kwargs["ts"], str) for message in messages)


def test_render_followup_history_uses_five_newest_entries() -> None:
    # Given
    _, processed = build_history_views(
        seed_messages(_turns(6)), max_tokens=4_000, gate_on=False
    )

    # When
    rendered = render_followup_history(processed)

    # Then
    assert rendered == (
        "Previous conversation:\n"
        "User: question 5\nAssistant: answer 5\n\n"
        "User: question 4\nAssistant: answer 4\n\n"
        "User: question 3\nAssistant: answer 3\n\n"
        "User: question 2\nAssistant: answer 2\n\n"
        "User: question 1\nAssistant: answer 1\n\n"
    )
    assert rendered.count("User:") == 5


def test_render_display_answer_prefixes_notices_only_when_present() -> None:
    # Given / When / Then
    assert render_display_answer("answer", ["notice one", "notice two"]) == (
        "notice one\n\nnotice two\n\nanswer"
    )
    assert render_display_answer("answer", []) == "answer"
