from healthcare_rag.graph.build import build_graph

graph = build_graph().compile(name="healthcare_rag")

__all__ = ["build_graph", "graph"]
