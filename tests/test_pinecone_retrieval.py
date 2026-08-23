"""Unit tests for the Pinecone hybrid retrieval arm (no network, no Pinecone, no OpenAI).

Covers what can silently break the A/B comparison: the convex-scaling maths that
gives ``alpha`` the same meaning it has on the Weaviate arm, result-shape parity
with ``retrieval.to_query_documents`` (``chunk_recall`` / ``page_recall`` read
``metadata["id_"]`` and ``page_numbers``), the namespace naming rule shared with
the ingest, the knobs, and the arm/limit wiring in the retrieve node.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import ToolCall

from healthcare_rag.graph import resources as resources_module
from healthcare_rag.graph.llm import LangChainLLMGateway
from healthcare_rag.graph.nodes import retrieve as retrieve_node
from healthcare_rag.graph.resources import Resources
from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.models.retrieval import QueryResult, QueryResultList
from healthcare_rag.processors import pinecone_retrieval as pc
from healthcare_rag.processors.retrieval import to_query_documents as weaviate_documents
from healthcare_rag.services import models as model_settings
from healthcare_rag.storage import pinecone_store

CHUNK: dict[str, Any] = {
    "id": 7,
    "text": "raw text 7",
    "contextualized": "ctx 7",
    "doc_source": "lipitor.pdf",
    "page_numbers": [2, 3],
}


def make_settings(**overrides: Any) -> GraphSettings:
    base: dict[str, Any] = {
        "safety_gate_enabled": True,
        "max_subqueries": 3,
        "decompose_only_complex": True,
        "disabled_stages": frozenset(),
        "llm_model": "fake-default",
        "validator_model": "fake-validator",
        "reasoning_effort": "none",
        "history_max_tokens": 4000,
        "structured_strict": False,
        "checkpoint_uri": "",
        "openai_api_key": "test",
        "pinecone_api_key": "test-pinecone",
    }
    return GraphSettings(**(base | overrides))


class StubIndex:
    """Records the single ``query`` call the arm makes and replays canned matches."""

    def __init__(self, matches: list[Any] | None = None) -> None:
        self.matches = matches or []
        self.calls: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(matches=self.matches)


def pinecone_match(chunk: dict[str, Any], score: float = 0.42) -> SimpleNamespace:
    """A match shaped the way the ingest writes metadata (page numbers as strings)."""
    return SimpleNamespace(
        id=f"lipitor-{chunk['id']}",
        score=score,
        metadata={
            # Pinecone returns numeric metadata as floats.
            "id_": float(chunk["id"]),
            "text": chunk["text"],
            "contextualized": chunk["contextualized"],
            "doc_source": chunk["doc_source"],
            "page_numbers": [str(page) for page in chunk["page_numbers"]],
        },
    )


# --------------------------------------------------------------------------- #
# convex scaling                                                                #
# --------------------------------------------------------------------------- #


def test_convex_scaling_splits_weight_between_the_two_halves() -> None:
    dense, sparse = pc.convex_scale(
        [1.0, 2.0], {"indices": [3, 9], "values": [10.0, 20.0]}, 0.65
    )
    assert dense == pytest.approx([0.65, 1.30])
    assert sparse["indices"] == [3, 9]
    assert sparse["values"] == pytest.approx([3.5, 7.0])


def test_convex_scaling_endpoints_are_dense_only_and_sparse_only() -> None:
    sparse_in = {"indices": [1], "values": [4.0]}
    dense, sparse = pc.convex_scale([2.0], sparse_in, 1.0)
    assert dense == pytest.approx([2.0])
    assert sparse["values"] == pytest.approx([0.0])

    dense, sparse = pc.convex_scale([2.0], sparse_in, 0.0)
    assert dense == pytest.approx([0.0])
    assert sparse["values"] == pytest.approx([4.0])


def test_convex_scaling_does_not_mutate_its_input() -> None:
    sparse_in = {"indices": [1], "values": [4.0]}
    pc.convex_scale([2.0], sparse_in, 0.5)
    assert sparse_in["values"] == [4.0]


@pytest.mark.parametrize("alpha", [-0.1, 1.1])
def test_convex_scaling_rejects_an_out_of_range_alpha(alpha: float) -> None:
    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        pc.convex_scale([1.0], {"indices": [], "values": []}, alpha)


# --------------------------------------------------------------------------- #
# result-shape parity with the Weaviate arm                                     #
# --------------------------------------------------------------------------- #


def _weaviate_object(chunk: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        properties={
            "id_": chunk["id"],
            "text": chunk["text"],
            "contextualized": chunk["contextualized"],
            "doc_source": chunk["doc_source"],
            "page_numbers": chunk["page_numbers"],
        },
        metadata=SimpleNamespace(score=0.5),
        uuid="11111111-2222-3333-4444-555555555555",
    )


def test_query_documents_match_the_weaviate_shape() -> None:
    (pinecone_doc,) = pc.to_query_documents([pinecone_match(CHUNK)], "Lipitor")
    (weaviate_doc,) = weaviate_documents([_weaviate_object(CHUNK)], "Lipitor")

    assert set(pinecone_doc.metadata or {}) == set(weaviate_doc.metadata or {})
    assert pinecone_doc.content == weaviate_doc.content == CHUNK["contextualized"]
    assert pinecone_doc.page_numbers == weaviate_doc.page_numbers == [2, 3]
    assert pinecone_doc.source_name == weaviate_doc.source_name
    # engine_record derives retrieved_chunk_ids from metadata["id_"].
    assert (pinecone_doc.metadata or {})["id_"] == CHUNK["id"]
    assert isinstance((pinecone_doc.metadata or {})["id_"], int)
    assert (pinecone_doc.metadata or {})["page_numbers"] == [2, 3]
    assert pinecone_doc.doc_id == "pinecone:Lipitor:7"
    assert pinecone_doc.score == pytest.approx(0.42)


def test_page_numbers_survive_the_string_round_trip() -> None:
    """Pinecone metadata has no int lists, so the ingest stores strings."""
    stored = pinecone_store.chunk_metadata(CHUNK)
    assert stored["page_numbers"] == ["2", "3"]
    assert stored["id_"] == 7
    assert pc.page_numbers_from_metadata(stored) == [2, 3]


def test_unparseable_page_numbers_are_dropped_not_fatal() -> None:
    assert pc.page_numbers_from_metadata({"page_numbers": ["4", None, "x", 5.0]}) == [4, 5]
    assert pc.page_numbers_from_metadata({}) == []


def test_empty_matches_yield_no_documents() -> None:
    assert pc.to_query_documents([], "Lipitor") == []
    assert pc.to_query_documents(None, "Lipitor") == []


# --------------------------------------------------------------------------- #
# namespace naming (the one rule the ingest and the query must agree on)        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("collection", "expected"), [("Lipitor", "lipitor"), ("Metformin", "metformin")]
)
def test_namespace_is_the_lower_cased_collection(collection: str, expected: str) -> None:
    assert pinecone_store.namespace_for(collection) == expected


# --------------------------------------------------------------------------- #
# pinecone_search end to end (stubbed index + stubbed embeddings)               #
# --------------------------------------------------------------------------- #


@pytest.fixture
def pinecone_resources(monkeypatch: pytest.MonkeyPatch):
    """Install Resources on the pinecone arm with every network call stubbed out."""

    def install(*, settings: GraphSettings | None = None) -> Resources:
        resources = Resources(settings or make_settings(retriever="pinecone"))
        resources._pinecone_client = SimpleNamespace(inference=SimpleNamespace())
        resources_module.override(resources)
        monkeypatch.setattr(pc, "embedding_client", lambda _settings: object())
        monkeypatch.setattr(
            pc, "dense_embeddings", lambda _client, _model, texts: [[1.0, 2.0] for _ in texts]
        )
        monkeypatch.setattr(
            pc,
            "sparse_embeddings",
            lambda _pc, _model, texts, input_type="passage": [
                {"indices": [11], "values": [4.0], "input_type": input_type} for _ in texts
            ],
        )
        return resources

    yield install
    resources_module.override(Resources(make_settings()))


async def test_pinecone_search_queries_the_namespace_with_scaled_vectors(
    pinecone_resources,
) -> None:
    pinecone_resources()
    index = StubIndex([pinecone_match(CHUNK)])

    result = await pc.pinecone_search(index, "Lipitor", "what is the dose?")

    (call,) = index.calls
    assert call["namespace"] == "lipitor"
    assert call["top_k"] == 4
    assert call["include_metadata"] is True
    assert call["vector"] == pytest.approx([0.65, 1.30])
    assert call["sparse_vector"]["values"] == pytest.approx([1.4])

    (query_result,) = result.results
    assert query_result.source == "Lipitor"
    assert query_result.query == "what is the dose?"
    assert [doc.metadata["id_"] for doc in query_result.docs] == [7]


async def test_pinecone_search_embeds_the_query_with_query_input_type(
    pinecone_resources,
) -> None:
    """Sparse passage and query embeddings are asymmetric; the query side must say so."""
    resources = pinecone_resources()
    _, sparse = await pc.embed_query("q", resources.settings, resources._pinecone_client)
    assert sparse["input_type"] == "query"


async def test_pinecone_search_honours_the_limit(pinecone_resources) -> None:
    pinecone_resources()
    index = StubIndex([])
    await pc.pinecone_search(index, "Metformin", "q", limit=12)
    assert index.calls[0]["top_k"] == 12
    assert index.calls[0]["namespace"] == "metformin"


async def test_pinecone_search_uses_the_alpha_knob(pinecone_resources) -> None:
    pinecone_resources(settings=make_settings(retriever="pinecone", pinecone_alpha=0.25))
    index = StubIndex([])
    await pc.pinecone_search(index, "Lipitor", "q")
    assert index.calls[0]["vector"] == pytest.approx([0.25, 0.5])
    assert index.calls[0]["sparse_vector"]["values"] == pytest.approx([3.0])


async def test_pinecone_resources_fail_fast_without_a_key() -> None:
    resources = Resources(make_settings(retriever="pinecone", pinecone_api_key=""))
    with pytest.raises(ValueError, match="PINECONE_API_KEY is not set"):
        await resources.pinecone_client()
    with pytest.raises(ValueError, match="PINECONE_API_KEY is not set"):
        await resources.pinecone()


def test_pinecone_store_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="PINECONE_API_KEY is not set"):
        pinecone_store.require_pinecone_api_key()
    assert pinecone_store.require_pinecone_api_key("abc") == "abc"


def test_build_vectors_pairs_chunks_with_both_halves() -> None:
    vectors = pinecone_store.build_vectors(
        [CHUNK], "lipitor", [[0.1, 0.2]], [{"indices": [3], "values": [1.0]}]
    )
    (vector,) = vectors
    assert vector["id"] == "lipitor-7"
    assert vector["values"] == [0.1, 0.2]
    assert vector["sparse_values"] == {"indices": [3], "values": [1.0]}
    assert vector["metadata"]["page_numbers"] == ["2", "3"]


def test_build_vectors_refuses_a_length_mismatch() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        pinecone_store.build_vectors([CHUNK], "lipitor", [], [])


def test_the_vectorised_text_is_the_field_weaviate_searches() -> None:
    assert pinecone_store.chunk_text(CHUNK) == CHUNK["contextualized"]


# --------------------------------------------------------------------------- #
# the knobs                                                                     #
# --------------------------------------------------------------------------- #


def test_knob_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HC_RAG_RETRIEVER",
        "HC_RAG_RERANKER",
        "HC_RAG_RERANK_CANDIDATES",
        "HC_RAG_RERANK_TOP_K",
        "HC_RAG_RERANK_MODEL",
        "HC_RAG_PINECONE_INDEX",
        "HC_RAG_PINECONE_SPARSE_MODEL",
        "HC_RAG_PINECONE_ALPHA",
        "HC_RAG_EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    assert model_settings.retriever_backend() == "weaviate"
    assert model_settings.reranker_backend() == "none"
    assert model_settings.rerank_candidates() == 12
    assert model_settings.rerank_top_k() == 4
    assert model_settings.rerank_model() == "bge-reranker-v2-m3"
    assert model_settings.pinecone_index_name() == "healthcare-rag"
    assert model_settings.pinecone_sparse_model() == "pinecone-sparse-english-v0"
    assert model_settings.pinecone_alpha() == 0.65
    assert model_settings.embedding_model() == "text-embedding-3-small"


def test_pinecone_is_a_valid_retriever(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_RAG_RETRIEVER", "PineCone")
    assert model_settings.retriever_backend() == "pinecone"
    assert "pinecone" in model_settings.VALID_RETRIEVERS


def test_unknown_retriever_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_RAG_RETRIEVER", "chroma")
    with pytest.raises(ValueError, match="HC_RAG_RETRIEVER must be one of"):
        model_settings.retriever_backend()


def test_unknown_reranker_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_RAG_RERANKER", "cohere")
    with pytest.raises(ValueError, match="HC_RAG_RERANKER must be one of"):
        model_settings.reranker_backend()


@pytest.mark.parametrize("raw", ["0", "-1", "nope"])
def test_rerank_counts_reject_nonsense(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("HC_RAG_RERANK_CANDIDATES", raw)
    with pytest.raises(ValueError, match="HC_RAG_RERANK_CANDIDATES"):
        model_settings.rerank_candidates()
    monkeypatch.setenv("HC_RAG_RERANK_TOP_K", raw)
    with pytest.raises(ValueError, match="HC_RAG_RERANK_TOP_K"):
        model_settings.rerank_top_k()


@pytest.mark.parametrize("raw", ["-0.1", "1.5", "loads"])
def test_alpha_rejects_values_outside_the_unit_interval(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("HC_RAG_PINECONE_ALPHA", raw)
    with pytest.raises(ValueError, match="HC_RAG_PINECONE_ALPHA"):
        model_settings.pinecone_alpha()


def test_blank_knobs_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_RAG_RERANKER", "  ")
    monkeypatch.setenv("HC_RAG_PINECONE_INDEX", "  ")
    monkeypatch.setenv("HC_RAG_PINECONE_ALPHA", " ")
    assert model_settings.reranker_backend() == "none"
    assert model_settings.pinecone_index_name() == "healthcare-rag"
    assert model_settings.pinecone_alpha() == 0.65


def test_settings_snapshot_carries_every_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_RAG_RETRIEVER", "pinecone")
    monkeypatch.setenv("HC_RAG_RERANKER", "pinecone")
    monkeypatch.setenv("HC_RAG_RERANK_CANDIDATES", "20")
    monkeypatch.setenv("HC_RAG_RERANK_TOP_K", "5")
    monkeypatch.setenv("PINECONE_API_KEY", "pc-key")
    settings = GraphSettings.from_env()
    assert settings.retriever == "pinecone"
    assert settings.reranker == "pinecone"
    assert settings.rerank_candidates == 20
    assert settings.rerank_top_k == 5
    assert settings.pinecone_api_key == "pc-key"


# --------------------------------------------------------------------------- #
# retrieve node wiring: arm selection, candidate limit, rerank hand-off         #
# --------------------------------------------------------------------------- #


class RoutingGateway(LangChainLLMGateway):
    """Routes every query to one collection, with no LLM anywhere."""

    def __init__(self, collection: str = "Lipitor") -> None:
        super().__init__(make_settings())
        self.collection = collection

    async def aroute_tools(self, query: str) -> list[ToolCall]:
        return [
            ToolCall(
                name=f"query_{self.collection.lower()}",
                args={"query": query},
                id="call-1",
            )
        ]


class RecordingSearch:
    """Stand-in for ``hybrid_search``: same signature, records how it was called."""

    def __init__(self, docs_per_call: int = 12) -> None:
        self.docs_per_call = docs_per_call
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, _client: Any, collection_name: str, query: str, *, limit: int = 4
    ) -> QueryResultList:
        self.calls.append({"collection": collection_name, "query": query, "limit": limit})
        docs = pc.to_query_documents(
            [pinecone_match({**CHUNK, "id": i}, score=1.0 - i / 100) for i in range(limit)],
            collection_name,
        )
        return QueryResultList(
            results=[QueryResult(source=collection_name, query=query, docs=docs)]
        )


def _retrieve_state() -> dict[str, Any]:
    return {
        "query": "what is the dose?",
        "phase": 0,
        "kind": "initial",
        "index": 0,
        "branch": "original",
    }


@pytest.fixture
def node_resources(monkeypatch: pytest.MonkeyPatch, seal_offline_resources):
    """Install a faked `Resources`; see `seal_offline_resources` for why sealing
    the client slots is what keeps these tests off the network."""

    def install(search: RecordingSearch, **overrides: Any) -> Resources:
        resources = seal_offline_resources(Resources(make_settings(**overrides)))
        resources._gateway = RoutingGateway()
        resources.hybrid_search = search
        resources_module.override(resources)
        return resources

    yield install
    resources_module.override(seal_offline_resources(Resources(make_settings())))


async def test_default_path_passes_no_limit_and_never_reranks(
    node_resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Weaviate default must stay byte-identical: limit 4, no rerank call."""
    reranks: list[Any] = []
    monkeypatch.setattr(
        retrieve_node,
        "rerank_documents",
        lambda *args, **kwargs: reranks.append(args) or [],
    )
    search = RecordingSearch()
    node_resources(search)

    output = await retrieve_node.retrieve_documents(_retrieve_state())

    assert search.calls == [
        {"collection": "Lipitor", "query": "what is the dose?", "limit": 4}
    ]
    assert reranks == []
    assert len(output["retrievals"][0]["results"]["results"][0]["docs"]) == 4


async def test_reranking_widens_the_search_then_trims_to_top_k(
    node_resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_rerank(_resources, query, docs, top_k):
        calls.append({"query": query, "candidates": len(docs), "top_k": top_k})
        return list(reversed(docs))[:top_k]

    monkeypatch.setattr(retrieve_node, "rerank_documents", fake_rerank)
    search = RecordingSearch()
    node_resources(search, reranker="pinecone", rerank_candidates=12, rerank_top_k=4)

    output = await retrieve_node.retrieve_documents(_retrieve_state())

    assert search.calls[0]["limit"] == 12
    assert calls == [
        {"query": "what is the dose?", "candidates": 12, "top_k": 4}
    ]
    docs = output["retrievals"][0]["results"]["results"][0]["docs"]
    assert [doc["metadata"]["id_"] for doc in docs] == [11, 10, 9, 8]


async def test_a_search_without_a_limit_kwarg_is_never_handed_one(
    node_resources, seal_offline_resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injected fixtures predate the kwarg; passing it would be a TypeError."""
    monkeypatch.setattr(
        retrieve_node, "rerank_documents", lambda _r, _q, docs, top_k: docs[:top_k]
    )
    calls: list[tuple[str, str]] = []

    async def legacy_search(_client: Any, collection_name: str, query: str):
        calls.append((collection_name, query))
        return QueryResultList(results=[])

    assert retrieve_node.accepts_limit(legacy_search) is False
    resources = seal_offline_resources(Resources(make_settings(reranker="pinecone")))
    resources._gateway = RoutingGateway()
    resources.hybrid_search = legacy_search
    resources_module.override(resources)

    await retrieve_node.retrieve_documents(_retrieve_state())
    assert calls == [("Lipitor", "what is the dose?")]


def test_every_arm_has_a_search_callable_and_a_retry_error() -> None:
    from pinecone.exceptions import PineconeException
    from weaviate.exceptions import WeaviateBaseError

    assert set(retrieve_node._ARMS) == set(model_settings.VALID_RETRIEVERS)
    assert retrieve_node.resolve_arm("pinecone") == (pc.pinecone_search, PineconeException)
    assert retrieve_node.resolve_arm("weaviate")[1] is WeaviateBaseError


def test_an_unknown_arm_names_the_valid_ones() -> None:
    with pytest.raises(ValueError, match="Unknown retrieval arm"):
        retrieve_node.resolve_arm("chroma")


def test_the_arm_is_resolved_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Arms are looked up by name so a patched module attribute really swaps them."""
    sentinel = object()
    monkeypatch.setattr(retrieve_node, "pinecone_search", sentinel)
    assert retrieve_node.resolve_arm("pinecone")[0] is sentinel
