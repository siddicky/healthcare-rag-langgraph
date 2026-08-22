"""Router Literals stay in sync with node constants, Commands and compiled edges."""

from typing import Literal, get_args, get_origin, get_type_hints

from langgraph.graph import END
from langgraph.types import Command

from healthcare_rag.graph import routers
from healthcare_rag.graph.build import build_graph, build_pipeline
from healthcare_rag.graph.nodes.evaluate import evaluate_retrieval
from healthcare_rag.graph.nodes.preprocess import decompose_query
from healthcare_rag.graph.nodes.query_or_respond import route_query_or_respond
from healthcare_rag.graph.nodes.retrieve import merge_retrievals
from healthcare_rag.graph.nodes.safety import safety_gate


def _command_targets(node) -> set[str]:
    """The node names LangGraph reads out of a ``Command[Literal[...]]`` return."""
    hint = get_type_hints(node)["return"]
    assert get_origin(hint) is Command, node.__name__
    (literal,) = get_args(hint)
    assert get_origin(literal) is Literal, node.__name__
    return set(get_args(literal))


def _targets(graph, source: str) -> set[str]:
    return {edge.target for edge in graph.get_graph().edges if edge.source == source}


def test_literal_targets_match_node_constants() -> None:
    assert set(get_args(routers.GateTerminalTarget)) == {routers.NODE_FINALIZE}
    assert set(get_args(routers.GateFanOutTarget)) == {
        routers.NODE_CLARIFY,
        routers.NODE_CONTEXT,
    }
    assert set(get_args(routers.GateQueryTarget)) == {routers.NODE_QUERY_OR_RESPOND}
    assert set(get_args(routers.MergeTarget)) == {
        routers.NODE_EVALUATE,
        routers.NODE_GENERATE,
    }
    assert set(get_args(routers.EvaluateTarget)) == {routers.NODE_GENERATE}
    assert set(get_args(routers.ValidateTarget)) == {
        routers.NODE_FOLLOW_UPS,
        routers.NODE_FINALIZE,
    }
    assert set(get_args(routers.DecomposeTarget)) == {routers.NODE_RETRIEVE}
    # Nested Literals must flatten, or LangGraph reads no targets off the Command.
    assert set(get_args(routers.GateTarget)) == {
        routers.NODE_FINALIZE,
        routers.NODE_QUERY_OR_RESPOND,
        routers.NODE_CLARIFY,
        routers.NODE_CONTEXT,
    }
    assert set(get_args(routers.QueryOrRespondTarget)) == {
        routers.NODE_FINALIZE,
        routers.NODE_CLARIFY,
        routers.NODE_CONTEXT,
    }
    assert set(get_args(routers.EvaluateCommandTarget)) == {
        routers.NODE_GENERATE,
        routers.NODE_RETRIEVE,
    }
    constants = {
        value
        for name, value in vars(routers).items()
        if name.startswith("NODE_") and isinstance(value, str)
    }
    assert set(get_args(routers.NodeName)) == constants


def test_return_annotations_are_literal_based() -> None:
    for router in (
        routers.route_after_gate,
        routers.route_after_merge,
        routers.route_after_evaluate,
        routers.route_after_validate,
    ):
        hint = get_type_hints(router)["return"]
        assert "Literal" in repr(hint), router.__name__


def test_self_routing_nodes_declare_their_command_targets() -> None:
    """Every node that routes itself annotates exactly the edges it can take."""
    assert _command_targets(safety_gate) == set(get_args(routers.GateTarget))
    assert _command_targets(route_query_or_respond) == set(
        get_args(routers.QueryOrRespondTarget)
    )
    assert _command_targets(decompose_query) == set(get_args(routers.DecomposeTarget))
    assert _command_targets(merge_retrievals) == set(get_args(routers.MergeTarget))
    assert _command_targets(evaluate_retrieval) == set(
        get_args(routers.EvaluateCommandTarget)
    )


def test_compiled_edges_match_router_literals() -> None:
    graph = build_graph().compile()
    # safety_gate, merge_retrievals, evaluate_retrieval and decompose_query wire no
    # edges in build.py: these edges come from their Command[Literal[...]] returns.
    assert _targets(graph, routers.NODE_SAFETY) == _command_targets(safety_gate)
    assert _targets(graph, routers.NODE_QUERY_OR_RESPOND) == _command_targets(
        route_query_or_respond
    )
    assert _targets(graph, routers.NODE_MERGE) == set(get_args(routers.MergeTarget))
    assert _targets(graph, routers.NODE_EVALUATE) == {
        routers.NODE_RETRIEVE,
        routers.NODE_GENERATE,
    }
    assert _targets(graph, routers.NODE_VALIDATE) == set(
        get_args(routers.ValidateTarget)
    )
    assert _targets(graph, routers.NODE_DECOMPOSE) == {routers.NODE_RETRIEVE}


def test_pipeline_without_follow_ups_maps_validate_to_end() -> None:
    graph = build_pipeline(include_follow_ups=False).compile()
    assert _targets(graph, routers.NODE_VALIDATE) == {END}


def test_routers_return_declared_literals() -> None:
    assert routers.route_after_gate({"safety_response": "x"}) == "finalize"
    assert routers.route_after_gate({"direct_response": "hello"}) == "finalize"
    assert routers.route_after_gate({"response_action": "query_or_respond"}) == (
        "generate_query_or_respond"
    )
    assert routers.route_after_gate({}) == [
        "clarify_query",
        "extract_conversation_context",
    ]
    assert (
        routers.route_after_query_or_respond({"direct_response": "hello"}) == "finalize"
    )
    assert routers.route_after_query_or_respond({}) == [
        "clarify_query",
        "extract_conversation_context",
    ]
    assert routers.route_after_merge({"gap_filled": True}) == "generate_answer"
    assert routers.route_after_merge({}) == "evaluate_retrieval"
    assert routers.route_after_evaluate({}) == "generate_answer"
    assert (
        routers.route_after_validate({"validated": "an answer"})
        == "generate_follow_ups"
    )
    assert routers.route_after_validate({}) == "finalize"
