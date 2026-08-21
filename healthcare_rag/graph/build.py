"""LangGraph builders for the internal pipeline and public healthcare graph."""

from typing import TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import END, START, StateGraph

from healthcare_rag.graph.nodes.evaluate import evaluate_retrieval
from healthcare_rag.graph.nodes.generate import (
    generate_answer,
    generate_follow_ups,
    validate_answer,
)
from healthcare_rag.graph.nodes.preprocess import (
    clarify_query,
    decompose_query,
    extract_conversation_context,
)
from healthcare_rag.graph.nodes.retrieve import merge_retrievals, retrieve_documents
from healthcare_rag.graph.nodes.safety import finalize, safety_gate
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
    route_after_decompose,
    route_after_evaluate,
    route_after_gate,
    route_after_merge,
    route_after_validate,
)
from healthcare_rag.graph.state import GraphInput, GraphOutput, RAGState, RetrieveInput


class PipelineInput(TypedDict, total=False):
    question: str
    scrubbed_question: str
    working_query: str
    user_id: str
    messages: list[AnyMessage]


type PipelineBuilder = StateGraph[RAGState, None, PipelineInput, RAGState]
type PublicBuilder = StateGraph[RAGState, None, GraphInput, GraphOutput]
type GraphBuilder = PipelineBuilder | PublicBuilder


def add_pipeline(
    builder: GraphBuilder,
    include_follow_ups: bool = True,
    terminal: str = END,
) -> GraphBuilder:
    """Append the shared post-gate pipeline to a graph builder."""
    builder.add_node(NODE_CLARIFY, clarify_query, input_schema=RAGState)
    builder.add_node(NODE_CONTEXT, extract_conversation_context, input_schema=RAGState)
    builder.add_node(NODE_DECOMPOSE, decompose_query, input_schema=RAGState)
    builder.add_node(NODE_RETRIEVE, retrieve_documents, input_schema=RetrieveInput)
    builder.add_node(NODE_MERGE, merge_retrievals, input_schema=RAGState)
    builder.add_node(NODE_EVALUATE, evaluate_retrieval, input_schema=RAGState)
    builder.add_node(NODE_GENERATE, generate_answer, input_schema=RAGState)
    builder.add_node(NODE_VALIDATE, validate_answer, input_schema=RAGState)
    builder.add_edge([NODE_CLARIFY, NODE_CONTEXT], NODE_DECOMPOSE)
    builder.add_conditional_edges(
        NODE_DECOMPOSE,
        route_after_decompose,
        [NODE_RETRIEVE],
    )
    builder.add_edge(NODE_RETRIEVE, NODE_MERGE)
    builder.add_conditional_edges(
        NODE_MERGE,
        route_after_merge,
        [NODE_EVALUATE, NODE_GENERATE],
    )
    builder.add_conditional_edges(
        NODE_EVALUATE,
        route_after_evaluate,
        [NODE_RETRIEVE, NODE_GENERATE],
    )
    builder.add_edge(NODE_GENERATE, NODE_VALIDATE)
    if include_follow_ups:
        builder.add_node(
            NODE_FOLLOW_UPS,
            generate_follow_ups,
            input_schema=RAGState,
        )
        builder.add_conditional_edges(
            NODE_VALIDATE,
            route_after_validate,
            {
                NODE_FOLLOW_UPS: NODE_FOLLOW_UPS,
                NODE_FINALIZE: terminal,
            },
        )
        builder.add_edge(NODE_FOLLOW_UPS, terminal)
    else:
        builder.add_conditional_edges(
            NODE_VALIDATE,
            route_after_validate,
            {
                NODE_FOLLOW_UPS: terminal,
                NODE_FINALIZE: terminal,
            },
        )
    return builder


def build_pipeline(include_follow_ups: bool = True) -> PipelineBuilder:
    """Build the trusted internal pipeline used after safety classification."""
    builder: PipelineBuilder = StateGraph(RAGState, input_schema=PipelineInput)
    add_pipeline(builder, include_follow_ups=include_follow_ups)
    builder.add_edge(START, NODE_CLARIFY)
    builder.add_edge(START, NODE_CONTEXT)
    return builder


def build_graph() -> PublicBuilder:
    """Build the public graph with constrained input and output schemas."""
    builder: PublicBuilder = StateGraph(
        RAGState,
        input_schema=GraphInput,
        output_schema=GraphOutput,
    )
    builder.add_node(NODE_SAFETY, safety_gate, input_schema=RAGState)
    builder.add_node(NODE_FINALIZE, finalize, input_schema=RAGState)
    add_pipeline(builder, terminal=NODE_FINALIZE)
    builder.add_edge(START, NODE_SAFETY)
    builder.add_conditional_edges(
        NODE_SAFETY,
        route_after_gate,
        [NODE_CLARIFY, NODE_CONTEXT, NODE_FINALIZE],
    )
    builder.add_edge(NODE_FINALIZE, END)
    return builder
