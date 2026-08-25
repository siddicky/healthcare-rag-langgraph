from __future__ import annotations

import pytest

from healthcare_rag.agent.features import is_erase_request


@pytest.mark.parametrize(
    "text",
    [
        "Delete my data",
        "Erase my account",
        "Remove my history",
        "Forget my records",
        "Wipe my medication history",
        "Clear everything",
        "Purge my data",
        "Get rid of my data",
        "Could you please delete my account?",
        "Can you help me erase my records?",
        "I want to have my data deleted",
        "How can I have my medication history removed?",
    ],
)
def test_is_erase_request_recognizes_all_erasure_forms(text: str) -> None:
    # Given/When/Then
    assert is_erase_request(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "What are the side effects of my medication?",
        "Log my weight at 190 lb",
        "hey what's up",
    ],
)
def test_is_erase_request_rejects_non_erasure_text(text: str) -> None:
    # Given/When/Then
    assert is_erase_request(text) is False
