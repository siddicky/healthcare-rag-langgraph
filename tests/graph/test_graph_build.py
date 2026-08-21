from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from healthcare_rag.graph.build import build_graph, build_pipeline
from healthcare_rag.graph.routers import (
    NODE_CLARIFY,
    NODE_CONTEXT,
    NODE_DECOMPOSE,
    NODE_EVALUATE,
    NODE_FINALIZE,
    NODE_FOLLOW_UPS,
    NODE_GENERATE,
    NODE_MERGE,
    NODE_RETRIEVE,
    NODE_SAFETY,
    NODE_VALIDATE,
)

EXPECTED_NODES = {
    NODE_SAFETY,
    NODE_CLARIFY,
    NODE_CONTEXT,
    NODE_DECOMPOSE,
    NODE_RETRIEVE,
    NODE_MERGE,
    NODE_EVALUATE,
    NODE_GENERATE,
    NODE_VALIDATE,
    NODE_FOLLOW_UPS,
    NODE_FINALIZE,
}


def test_pipeline_and_full_graph_compile() -> None:
    build_pipeline().compile()
    build_graph().compile()


def test_full_graph_compiles_with_external_checkpointer() -> None:
    build_graph().compile(checkpointer=InMemorySaver())


def test_full_graph_contains_all_runtime_stage_nodes() -> None:
    graph = build_graph().compile()

    assert set(graph.get_graph().nodes) >= EXPECTED_NODES


def test_pipeline_can_omit_follow_up_node_for_internal_runs() -> None:
    graph = build_pipeline(include_follow_ups=False).compile()

    assert NODE_FOLLOW_UPS not in graph.get_graph().nodes


def test_compiled_graph_mermaid_matches_committed_artifact() -> None:
    graph = build_graph().compile(name="healthcare_rag")
    expected = Path("docs/graph.mmd").read_text()

    assert graph.get_graph().draw_mermaid() == expected
