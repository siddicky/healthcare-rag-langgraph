from __future__ import annotations

from evals.routing_multiturn_evaluators import (
    BoundaryOutputs,
    BoundaryReferences,
    boundary_replay_precision,
)


def test_answerable_boundary_replay_is_a_precision_failure() -> None:
    # Given: an answerable turn is incorrectly served by refusal-boundary replay.
    outputs: BoundaryOutputs = {
        "turns": [
            {"index": 1, "safety_outcome": {"boundary_hit": True}},
        ]
    }
    references: BoundaryReferences = {"turns": [{"expected_action": "retrieve"}]}

    # When: replay precision is joined by turn position.
    result = boundary_replay_precision({}, outputs, references)

    # Then: the replay is a hard violation, not an averaged pass.
    assert result[0]["score"] == 0.0
    assert result[1]["score"] == 1
