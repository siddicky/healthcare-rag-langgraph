from langgraph.types import Send

from healthcare_rag.graph.routers import (
    NODE_ADDENDUM,
    NODE_EVALUATE,
    NODE_FINALIZE,
    NODE_FOLLOW_UPS,
    NODE_GENERATE,
    NODE_RETRIEVE,
    route_after_decompose,
    route_after_evaluate,
    route_after_gate,
    route_after_merge,
    route_after_validate,
)
from healthcare_rag.graph.state import RAGState


def _send_args(sends: list[Send]) -> list[dict[str, str | int]]:
    return [send.arg for send in sends]


def test_route_after_gate_fans_out_safe_queries() -> None:
    assert route_after_gate({}) == [
        "clarify_query",
        "extract_conversation_context",
    ]


def test_route_after_gate_routes_addendum_refusals() -> None:
    state: RAGState = {
        "safety_response": "refusal",
        "addendum_query": "safe question",
    }

    assert route_after_gate(state) == NODE_ADDENDUM


def test_route_after_gate_finalizes_plain_refusals() -> None:
    state: RAGState = {"safety_response": "refusal"}

    assert route_after_gate(state) == NODE_FINALIZE


def test_route_after_decompose_always_sends_initial_parent() -> None:
    sends = route_after_decompose({"working_query": "parent"})

    assert _send_args(sends) == [
        {
            "query": "parent",
            "index": 0,
            "kind": "initial",
            "phase": 0,
            "branch": "initial",
        }
    ]
    assert sends[0].node == NODE_RETRIEVE


def test_route_after_decompose_labels_clarified_parent() -> None:
    sends = route_after_decompose(
        {"working_query": "clarified", "selected_branch_type": "clarified"}
    )

    assert _send_args(sends)[0] == {
        "query": "clarified",
        "index": 0,
        "kind": "clarified",
        "phase": 0,
        "branch": "clarified",
    }


def test_route_after_decompose_sends_parent_before_labeled_subqueries() -> None:
    sends = route_after_decompose(
        {
            "working_query": "parent",
            "decomposed": True,
            "sub_queries": ["one", "two", "three"],
        }
    )

    assert _send_args(sends) == [
        {
            "query": "parent",
            "index": 0,
            "kind": "initial",
            "phase": 0,
            "branch": "initial",
        },
        {
            "query": "one",
            "index": 1,
            "kind": "decomposed",
            "phase": 0,
            "branch": "decomposed_0",
        },
        {
            "query": "two",
            "index": 2,
            "kind": "decomposed",
            "phase": 0,
            "branch": "decomposed_1",
        },
        {
            "query": "three",
            "index": 3,
            "kind": "decomposed",
            "phase": 0,
            "branch": "decomposed_2",
        },
    ]


def test_route_after_decompose_caps_subqueries_at_three() -> None:
    sends = route_after_decompose(
        {
            "working_query": "parent",
            "decomposed": True,
            "sub_queries": ["one", "two", "three", "four", "five"],
        }
    )

    assert len(sends) == 4
    assert [send.arg["query"] for send in sends] == [
        "parent",
        "one",
        "two",
        "three",
    ]


def test_route_after_merge_selects_generation_after_gap_fill() -> None:
    assert route_after_merge({"gap_filled": True}) == NODE_GENERATE


def test_route_after_merge_selects_evaluation_before_gap_fill() -> None:
    assert route_after_merge({"gap_filled": False}) == NODE_EVALUATE


def test_route_after_evaluate_sends_capped_gap_round_metadata() -> None:
    sends = route_after_evaluate(
        {
            "gap_pending": True,
            "evaluation": {
                "additional_queries": ["one", "two", "three", "four"]
            },
        }
    )

    assert isinstance(sends, list)
    assert _send_args(sends) == [
        {
            "query": "one",
            "index": 0,
            "kind": "gap_fill",
            "phase": 1,
            "branch": "gap_fill",
        },
        {
            "query": "two",
            "index": 1,
            "kind": "gap_fill",
            "phase": 1,
            "branch": "gap_fill",
        },
        {
            "query": "three",
            "index": 2,
            "kind": "gap_fill",
            "phase": 1,
            "branch": "gap_fill",
        },
    ]


def test_route_after_evaluate_generates_without_pending_gap() -> None:
    assert route_after_evaluate({"gap_pending": False}) == NODE_GENERATE


def test_route_after_validate_routes_valid_answer_to_follow_ups() -> None:
    assert route_after_validate({"validated": "answer"}) == NODE_FOLLOW_UPS


def test_route_after_validate_finalizes_invalid_answer() -> None:
    assert route_after_validate({"validated": None}) == NODE_FINALIZE
