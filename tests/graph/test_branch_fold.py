from __future__ import annotations

import random

import pytest

from healthcare_rag.graph.engine import fold_branches


@pytest.mark.parametrize(
    ("events", "selected", "validated", "expected"),
    [
        (
            [{"phase": 0, "kind": "clarify", "index": 0, "branch": "initial", "status": "COMPLETED"}],
            "initial",
            "answer",
            [("initial", "COMPLETED")],
        ),
        (
            [{"phase": 0, "kind": "clarify", "index": 0, "branch": "clarified", "status": "COMPLETED"}],
            "clarified",
            "answer",
            [("clarified", "COMPLETED")],
        ),
        (
            [
                {"phase": 0, "kind": "retrieve", "index": 2, "branch": "decomposed_1", "status": "FAILED"},
                {"phase": 0, "kind": "retrieve", "index": 0, "branch": "initial", "status": "COMPLETED"},
                {"phase": 0, "kind": "retrieve", "index": 3, "branch": "decomposed_2", "status": "COMPLETED"},
                {"phase": 0, "kind": "retrieve", "index": 1, "branch": "decomposed_0", "status": "COMPLETED"},
                {"phase": 0, "kind": "merge", "index": 0, "branch": "synthesized", "status": "COMPLETED"},
            ],
            "synthesized",
            "answer",
            [
                ("initial", "COMPLETED"),
                ("decomposed_0", "COMPLETED"),
                ("decomposed_1", "FAILED"),
                ("decomposed_2", "COMPLETED"),
                ("synthesized", "COMPLETED"),
            ],
        ),
        (
            [
                {"phase": 0, "kind": "clarify", "index": 0, "branch": "initial", "status": "COMPLETED"},
                {"phase": 0, "kind": "merge", "index": 0, "branch": "initial", "status": "COMPLETED"},
            ],
            "initial",
            None,
            [("initial", "FAILED")],
        ),
    ],
)
def test_fold_branches_when_events_arrive_out_of_order(
    events: list[dict[str, str | int]],
    selected: str,
    validated: str | None,
    expected: list[tuple[str, str]],
) -> None:
    assert fold_branches(events, selected, validated, refusal=False, validate_disabled=False) == expected


def test_fold_branches_when_arrival_is_randomized() -> None:
    events = [
        {"phase": 0, "kind": "retrieve", "index": 0, "branch": "initial", "status": "COMPLETED"},
        {"phase": 0, "kind": "retrieve", "index": 1, "branch": "decomposed_0", "status": "COMPLETED"},
        {"phase": 0, "kind": "merge", "index": 0, "branch": "synthesized", "status": "COMPLETED"},
        {"phase": 1, "kind": "merge", "index": 0, "branch": "synthesized", "status": "COMPLETED"},
    ]
    expected = [("initial", "COMPLETED"), ("decomposed_0", "COMPLETED"), ("synthesized", "COMPLETED")]
    for seed in range(20):
        shuffled = list(events)
        random.Random(seed).shuffle(shuffled)
        assert fold_branches(shuffled, "synthesized", "answer", refusal=False, validate_disabled=False) == expected
