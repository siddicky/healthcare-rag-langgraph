"""LangGraph builders for the internal pipeline and public healthcare graph.

Nodes that both update state and choose a successor own their outgoing edges: they
return ``Command[Literal[...]]`` (``safety_gate``, ``decompose_query``,
``merge_retrievals``, ``evaluate_retrieval``) and no edge is wired for them here.
What stays below is the sequencing that is not a decision — plus ``validate_answer``,
whose "finalize" target differs between the two builders and so needs a path map.
"""

from typing import TypeAlias, TypedDict

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
from healthcare_rag.graph.nodes.query_or_respond import route_query_or_respond
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
    NODE_QUERY_OR_RESPOND,
    NODE_RETRIEVE,
    NODE_SAFETY,
    NODE_VALIDATE,
    route_after_validate,
)
from healthcare_rag.graph.state import GraphInput, GraphOutput, RAGState, RetrieveInput


class PipelineInput(TypedDict, total=False):
    question: str
    scrubbed_question: str
    working_query: str
    user_id: str
    messages: list[AnyMessage]


PipelineBuilder: TypeAlias = StateGraph[RAGState, None, PipelineInput, RAGState]
PublicBuilder: TypeAlias = StateGraph[RAGState, None, GraphInput, GraphOutput]
GraphBuilder: TypeAlias = PipelineBuilder | PublicBuilder


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
    # decompose_query, merge_retrievals and evaluate_retrieval route themselves via
    # Command; only the non-decision hops are edges.
    builder.add_edge(NODE_RETRIEVE, NODE_MERGE)
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
    builder.add_node(
        NODE_QUERY_OR_RESPOND,
        route_query_or_respond,
        input_schema=RAGState,
    )
    builder.add_node(NODE_FINALIZE, finalize, input_schema=RAGState)
    add_pipeline(builder, terminal=NODE_FINALIZE)
    builder.add_edge(START, NODE_SAFETY)
    # safety_gate routes itself: Command[GateTarget] covers both terminals and the
    # parallel hand-off to the two preprocessing nodes.
    builder.add_edge(NODE_FINALIZE, END)
    return builder
