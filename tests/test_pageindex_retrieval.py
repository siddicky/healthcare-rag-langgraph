"""Unit tests for the PageIndex retrieval arm (no network, no Weaviate).

Covers the three things that can silently break the A/B comparison:
the page-range → chunk mapping, result-shape parity with the Weaviate arm
(``chunk_recall`` / ``page_recall`` read ``metadata["id_"]`` and
``page_numbers``), and the knob defaulting to Weaviate.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import ToolCall

from healthcare_rag.graph import resources as resources_module
from healthcare_rag.graph.llm import LangChainLLMGateway
from healthcare_rag.graph.nodes import retrieve as retrieve_node
from healthcare_rag.graph.resources import Resources
from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.models.retrieval import PageIndexSelection
from healthcare_rag.processors import pageindex_retrieval as pi
from healthcare_rag.processors.retrieval import to_query_documents as weaviate_documents
from healthcare_rag.services.models import retriever_backend

TREE: dict[str, Any] = {
    "source_pdf": "docs/fake.pdf",
    "collection": "Fake",
    "page_count": 10,
    "tree": [
        {
            "node_id": "0000",
            "title": "Parent",
            "start_index": 1,
            "end_index": 1,
            "summary": "parent section",
            "nodes": [
                {
                    "node_id": "0001",
                    "title": "Child A",
                    "start_index": 1,
                    "end_index": 2,
                    "summary": "child a",
                    "nodes": [],
                },
                {
                    "node_id": "0002",
                    "title": "Child B",
                    "start_index": 5,
                    "end_index": 6,
                    "summary": "child b",
                    "nodes": [],
                },
            ],
        },
        {
            "node_id": "0003",
            "title": "Other",
            "start_index": 8,
            "end_index": 9,
            "summary": "other section",
            "nodes": [],
        },
        {
            "node_id": "0004",
            "title": "Ghost",
            "start_index": 50,
            "end_index": 60,
            "summary": "pages that do not exist",
            "nodes": [],
        },
    ],
}

CHUNKS: list[dict[str, Any]] = [
    {"id": i, "text": f"text {i}", "contextualized": f"ctx {i}",
     "doc_source": "fake", "page_numbers": pages}
    for i, pages in enumerate(
        [[1], [2], [2, 3], [5], [6], [8], [9], [55]],
    )
]


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
    }
    return GraphSettings(**(base | overrides))


class StubGateway(LangChainLLMGateway):
    """Records stage calls and returns a scripted PageIndexSelection."""

    def __init__(
        self,
        selection: PageIndexSelection | None = None,
        tool_calls: list[ToolCall] | None = None,
    ) -> None:
        super().__init__(make_settings())
        self.selection = selection or PageIndexSelection()
        self.tool_calls = tool_calls or []
        self.calls: list[dict[str, Any]] = []

    async def astructured(self, stage, model_type, **variables):  # type: ignore[override]
        self.calls.append({"stage": stage, **variables})
        return self.selection

    async def aroute_tools(self, query: str) -> list[ToolCall]:
        self.calls.append({"stage": "route", "query": query})
        return self.tool_calls


@pytest.fixture
def pageindex_resources(monkeypatch: pytest.MonkeyPatch):
    """Install Resources with the pageindex arm and the in-memory fixtures."""

    def install(
        selection: PageIndexSelection, *, settings: GraphSettings | None = None
    ) -> StubGateway:
        gateway = StubGateway(selection)
        resources = Resources(settings or make_settings(retriever="pageindex"))
        resources._gateway = gateway
        resources_module.override(resources)
        monkeypatch.setattr(pi, "load_tree", lambda _name: TREE)
        monkeypatch.setattr(pi, "load_chunks", lambda _name: CHUNKS)
        return gateway

    yield install
    resources_module.override(Resources(make_settings()))


# --------------------------------------------------------------------------- #
# page-range → chunk mapping                                                    #
# --------------------------------------------------------------------------- #


def test_parent_node_expands_to_descendant_page_ranges() -> None:
    """The parent covers p1 only, but its children add p2 and p5-6."""
    picked = pi.select_chunks(TREE, CHUNKS, ["0000"], max_chunks=8)
    assert [c["id"] for c in picked] == [0, 1, 2, 3, 4]


def test_empty_selection_yields_no_chunks() -> None:
    assert pi.select_chunks(TREE, CHUNKS, [], max_chunks=8) == []
    assert pi.to_query_documents([], "Fake") == []


def test_pages_outside_the_document_are_ignored() -> None:
    """Node 0004 covers p50-60 of a 10-page document: nothing is retrievable."""
    assert pi.select_chunks(TREE, CHUNKS, ["0004"], max_chunks=8) == []


def test_unknown_node_ids_are_ignored() -> None:
    assert pi.select_chunks(TREE, CHUNKS, ["nope", "0003"], max_chunks=8) == [
        CHUNKS[5],
        CHUNKS[6],
    ]


def test_chunk_cap_is_respected() -> None:
    picked = pi.select_chunks(TREE, CHUNKS, ["0000", "0003"], max_chunks=3)
    assert [c["id"] for c in picked] == [0, 1, 2]


def test_overlapping_nodes_deduplicate_in_first_seen_order() -> None:
    """Child A's pages are already covered by the parent; chunks appear once."""
    picked = pi.select_chunks(TREE, CHUNKS, ["0002", "0000"], max_chunks=8)
    assert [c["id"] for c in picked] == [3, 4, 0, 1, 2]


def test_selection_order_follows_node_order() -> None:
    picked = pi.select_chunks(TREE, CHUNKS, ["0003", "0001"], max_chunks=8)
    assert [c["id"] for c in picked] == [5, 6, 0, 1, 2]


def test_outline_lists_every_node_with_pages_and_summary() -> None:
    outline = pi.render_outline(TREE)
    assert "[0000] Parent (p1)" in outline
    assert "[0002]   Child B (p5-6) — child b" in outline
    assert len(outline.splitlines()) == 5


# --------------------------------------------------------------------------- #
# result-shape parity with the Weaviate arm                                     #
# --------------------------------------------------------------------------- #


def _weaviate_object(chunk: dict[str, Any]) -> SimpleNamespace:
    properties = {
        "id_": chunk["id"],
        "text": chunk["text"],
        "contextualized": chunk["contextualized"],
        "doc_source": chunk["doc_source"],
        "page_numbers": chunk["page_numbers"],
    }
    return SimpleNamespace(
        properties=properties,
        metadata=SimpleNamespace(score=0.5),
        uuid="11111111-2222-3333-4444-555555555555",
    )


def test_query_documents_match_the_weaviate_shape() -> None:
    chunk = CHUNKS[2]
    (pageindex_doc,) = pi.to_query_documents([chunk], "Fake")
    (weaviate_doc,) = weaviate_documents([_weaviate_object(chunk)], "Fake")

    assert set(pageindex_doc.metadata or {}) == set(weaviate_doc.metadata or {})
    assert pageindex_doc.content == weaviate_doc.content == chunk["contextualized"]
    assert pageindex_doc.page_numbers == weaviate_doc.page_numbers
    assert pageindex_doc.source_name == weaviate_doc.source_name
    # engine_record derives retrieved_chunk_ids from metadata["id_"].
    assert (pageindex_doc.metadata or {})["id_"] == chunk["id"]
    assert isinstance((pageindex_doc.metadata or {})["id_"], int)
    assert pageindex_doc.doc_id == "pageindex:Fake:2"


def test_scores_decrease_with_rank_and_stay_positive() -> None:
    docs = pi.to_query_documents(CHUNKS, "Fake")
    scores = [doc.score for doc in docs]
    assert scores[0] == 1.0
    assert scores == sorted(scores, reverse=True)
    assert min(scores) > 0


# --------------------------------------------------------------------------- #
# pageindex_search end to end (stubbed gateway)                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_pageindex_search_uses_one_llm_call_and_returns_documents(
    pageindex_resources,
) -> None:
    gateway = pageindex_resources(PageIndexSelection(node_ids=["0003"], rationale="x"))
    result = await pi.pageindex_search(None, "Fake", "does it interact with alcohol?")

    structured = [call for call in gateway.calls if call["stage"] == "pageindex_select"]
    assert len(structured) == 1
    assert structured[0]["question"] == "does it interact with alcohol?"
    assert "[0003] Other (p8-9)" in structured[0]["outline"]

    (query_result,) = result.results
    assert query_result.source == "Fake"
    assert query_result.query == "does it interact with alcohol?"
    assert [doc.metadata["id_"] for doc in query_result.docs] == [5, 6]


@pytest.mark.asyncio
async def test_pageindex_search_honours_the_node_cap(pageindex_resources) -> None:
    gateway = pageindex_resources(
        PageIndexSelection(node_ids=["0001", "0002", "0003"]),
        settings=make_settings(retriever="pageindex", pageindex_max_nodes=1),
    )
    result = await pi.pageindex_search(None, "Fake", "q")
    assert gateway.calls[0]["max_nodes"] == "1"
    assert [doc.metadata["id_"] for doc in result.results[0].docs] == [0, 1, 2]


@pytest.mark.asyncio
async def test_pageindex_search_survives_a_failed_selection(pageindex_resources) -> None:
    """astructured is fail-soft: an empty selection degrades to zero documents."""
    pageindex_resources(PageIndexSelection())
    result = await pi.pageindex_search(None, "Fake", "q")
    assert result.results[0].docs == []


def test_missing_tree_names_the_make_target(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_RAG_PAGEINDEX_DIR", str(tmp_path))
    pi.clear_cache()
    with pytest.raises(FileNotFoundError, match="make index-pageindex"):
        pi.load_tree("Lipitor")


def test_tree_and_chunks_load_from_the_data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pageindex_tree_fake.json").write_text(json.dumps(TREE))
    (tmp_path / "chunks_fake.json").write_text(json.dumps(CHUNKS))
    monkeypatch.setenv("HC_RAG_PAGEINDEX_DIR", str(tmp_path))
    pi.clear_cache()
    try:
        assert pi.load_tree("Fake")["page_count"] == 10
        assert len(pi.load_chunks("Fake")) == len(CHUNKS)
    finally:
        pi.clear_cache()


# --------------------------------------------------------------------------- #
# the knob                                                                      #
# --------------------------------------------------------------------------- #


def test_retriever_backend_defaults_to_weaviate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HC_RAG_RETRIEVER", raising=False)
    assert retriever_backend() == "weaviate"
    monkeypatch.setenv("HC_RAG_RETRIEVER", "  ")
    assert retriever_backend() == "weaviate"
    assert GraphSettings.from_env().retriever == "weaviate"


def test_retriever_backend_accepts_pageindex_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HC_RAG_RETRIEVER", "PageIndex")
    assert retriever_backend() == "pageindex"


def test_retriever_backend_rejects_unknown_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HC_RAG_RETRIEVER", "faiss")
    with pytest.raises(ValueError, match="HC_RAG_RETRIEVER"):
        retriever_backend()


@pytest.mark.parametrize(
    ("env", "expected"),
    [("HC_RAG_PAGEINDEX_MAX_NODES", "pageindex_max_nodes"),
     ("HC_RAG_PAGEINDEX_MAX_CHUNKS", "pageindex_max_chunks")],
)
def test_pageindex_caps_come_from_the_environment(
    env: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(env, "2")
    assert getattr(GraphSettings.from_env(), expected) == 2
    monkeypatch.setenv(env, "0")
    with pytest.raises(ValueError, match=env):
        GraphSettings.from_env()


# --------------------------------------------------------------------------- #
# the retrieve node picks the arm                                               #
# --------------------------------------------------------------------------- #


def _install_arm_spies(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    seen: dict[str, list[Any]] = {"weaviate": [], "pageindex": [], "client": []}

    async def fake_hybrid(client, collection_name, query):
        seen["weaviate"].append((collection_name, query))
        seen["client"].append(client)
        return retrieve_node.QueryResultList(results=[])

    async def fake_pageindex(client, collection_name, query):
        seen["pageindex"].append((collection_name, query))
        seen["client"].append(client)
        return retrieve_node.QueryResultList(results=[])

    monkeypatch.setattr(retrieve_node, "hybrid_search", fake_hybrid)
    monkeypatch.setattr(retrieve_node, "pageindex_search", fake_pageindex)
    return seen


def _state() -> dict[str, Any]:
    return {"query": "what is the dose?", "phase": 0, "kind": "initial",
            "index": 0, "branch": "primary"}


@pytest.mark.asyncio
async def test_retrieve_node_uses_weaviate_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _install_arm_spies(monkeypatch)
    gateway = StubGateway(
        tool_calls=[ToolCall(name="query_lipitor", args={"query": "dose"}, id="1")]
    )
    resources = Resources(make_settings())
    resources._gateway = gateway
    sentinel = object()

    async def fake_weaviate():
        return sentinel

    resources.weaviate = fake_weaviate  # type: ignore[method-assign]
    resources_module.override(resources)
    try:
        await retrieve_node.retrieve_documents(_state())
    finally:
        resources_module.override(Resources(make_settings()))

    assert seen["weaviate"] == [("Lipitor", "dose")]
    assert seen["pageindex"] == []
    assert seen["client"] == [sentinel]


@pytest.mark.asyncio
async def test_retrieve_node_uses_pageindex_without_touching_weaviate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _install_arm_spies(monkeypatch)
    gateway = StubGateway(
        tool_calls=[ToolCall(name="query_metformin", args={"query": "dose"}, id="1")]
    )
    resources = Resources(make_settings(retriever="pageindex"))
    resources._gateway = gateway

    async def exploding_weaviate():
        raise AssertionError("the pageindex arm must not connect to Weaviate")

    resources.weaviate = exploding_weaviate  # type: ignore[method-assign]
    resources_module.override(resources)
    try:
        await retrieve_node.retrieve_documents(_state())
    finally:
        resources_module.override(Resources(make_settings()))

    assert seen["pageindex"] == [("Metformin", "dose")]
    assert seen["weaviate"] == []
    assert seen["client"] == [None]


@pytest.mark.asyncio
async def test_injected_retriever_still_wins_over_the_knob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _install_arm_spies(monkeypatch)
    injected: list[str] = []

    async def custom(_client, collection_name, query):
        injected.append(collection_name)
        return retrieve_node.QueryResultList(results=[])

    gateway = StubGateway(
        tool_calls=[ToolCall(name="query_lipitor", args={"query": "dose"}, id="1")]
    )
    resources = Resources(make_settings(retriever="pageindex"))
    resources._gateway = gateway
    resources.hybrid_search = custom
    resources_module.override(resources)
    try:
        await retrieve_node.retrieve_documents(_state())
    finally:
        resources_module.override(Resources(make_settings()))

    assert injected == ["Lipitor"]
    assert seen["weaviate"] == seen["pageindex"] == []
