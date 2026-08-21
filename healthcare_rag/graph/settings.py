"""Immutable graph configuration sourced from centralized model getters."""

import os
from dataclasses import dataclass
from typing import ClassVar

from healthcare_rag.services.models import (
    decompose_only_complex,
    default_llm_model,
    default_reasoning_effort,
    default_validator_model,
    disabled_stages,
    embedding_model,
    max_subqueries,
    pageindex_max_chunks,
    pageindex_max_nodes,
    pinecone_alpha,
    pinecone_index_name,
    pinecone_sparse_model,
    query_response_arm,
    refusal_boundary_enabled,
    rerank_candidates,
    rerank_model,
    rerank_top_k,
    reranker_backend,
    retriever_backend,
    safety_classifier_backend,
    safety_gate_enabled,
)


@dataclass(frozen=True, slots=True)
class GraphSettings:
    safety_gate_enabled: bool
    max_subqueries: int
    decompose_only_complex: bool
    disabled_stages: frozenset[str]
    llm_model: str
    validator_model: str
    reasoning_effort: str
    history_max_tokens: int
    structured_strict: bool
    checkpoint_uri: str
    refusal_boundary_enabled: bool = True
    weaviate_host: str = "127.0.0.1"
    weaviate_http_port: int = 8080
    weaviate_grpc_port: int = 50051
    openai_api_key: str = ""
    collection_names: tuple[str, ...] = ("Lipitor", "Metformin")
    retriever: str = "weaviate"
    pageindex_max_nodes: int = 4
    pageindex_max_chunks: int = 8
    reranker: str = "none"
    rerank_candidates: int = 12
    rerank_top_k: int = 4
    rerank_model: str = "bge-reranker-v2-m3"
    pinecone_index_name: str = "healthcare-rag"
    pinecone_sparse_model: str = "pinecone-sparse-english-v0"
    pinecone_api_key: str = ""
    pinecone_alpha: float = 0.65
    embedding_model: str = "text-embedding-3-small"
    query_response_arm: str = "current"
    safety_classifier: str = "llm"

    _TRUTHY: ClassVar[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
    _FALSY: ClassVar[frozenset[str]] = frozenset({"0", "false", "no", "off"})

    @classmethod
    def from_env(cls) -> "GraphSettings":
        """Snapshot graph settings without reimplementing existing model rules."""
        raw_tokens = os.getenv("HC_RAG_HISTORY_MAX_TOKENS", "4000")
        try:
            history_max_tokens = int(raw_tokens)
        except ValueError:
            message = f"HC_RAG_HISTORY_MAX_TOKENS must be an integer, got {raw_tokens!r}"
            raise ValueError(message) from None

        raw_strict = os.getenv("HC_RAG_STRUCTURED_STRICT")
        if raw_strict is None or not raw_strict.strip():
            structured_strict = False
        elif raw_strict.strip().lower() in cls._TRUTHY:
            structured_strict = True
        elif raw_strict.strip().lower() in cls._FALSY:
            structured_strict = False
        else:
            allowed = sorted(cls._TRUTHY | cls._FALSY)
            message = (
                "HC_RAG_STRUCTURED_STRICT must be a boolean "
                f"(one of {allowed}), got {raw_strict!r}"
            )
            raise ValueError(message)

        return cls(
            safety_gate_enabled=safety_gate_enabled(),
            max_subqueries=max_subqueries(),
            decompose_only_complex=decompose_only_complex(),
            disabled_stages=disabled_stages(),
            llm_model=default_llm_model(),
            validator_model=default_validator_model(),
            reasoning_effort=default_reasoning_effort(),
            history_max_tokens=history_max_tokens,
            structured_strict=structured_strict,
            checkpoint_uri=os.getenv("HC_RAG_CHECKPOINT", ""),
            refusal_boundary_enabled=refusal_boundary_enabled(),
            weaviate_host=os.getenv("WEAVIATE_HOST", "127.0.0.1"),
            weaviate_http_port=int(os.getenv("WEAVIATE_PORT", "8080")),
            weaviate_grpc_port=int(os.getenv("WEAVIATE_GRPC_PORT", "50051")),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            retriever=retriever_backend(),
            pageindex_max_nodes=pageindex_max_nodes(),
            pageindex_max_chunks=pageindex_max_chunks(),
            reranker=reranker_backend(),
            rerank_candidates=rerank_candidates(),
            rerank_top_k=rerank_top_k(),
            rerank_model=rerank_model(),
            pinecone_index_name=pinecone_index_name(),
            pinecone_sparse_model=pinecone_sparse_model(),
            pinecone_api_key=os.getenv("PINECONE_API_KEY", ""),
            pinecone_alpha=pinecone_alpha(),
            embedding_model=embedding_model(),
            query_response_arm=query_response_arm(),
            safety_classifier=safety_classifier_backend(),
        )
