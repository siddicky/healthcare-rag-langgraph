"""Unit tests for the Pinecone Inference reranking stage (no network).

The reranker is a re-ordering of an already-retrieved candidate set, so the
contract under test is narrow: it reorders, it truncates to ``top_k``, it
restamps scores, and it never raises — a rerank outage degrades quality, not
availability.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from healthcare_rag.models.retrieval import QueryDocument
from healthcare_rag.processors.rerank import reorder, rerank_documents


def make_docs(count: int = 4) -> list[QueryDocument]:
    return [
        QueryDocument(
            content=f"ctx {i}",
            score=1.0 - i / 100,
            doc_id=f"pinecone:Lipitor:{i}",
            metadata={"id_": i, "text": f"text {i}", "doc_source": "l.pdf", "page_numbers": [i]},
            source_name="Lipitor",
            page_numbers=[i],
        )
        for i in range(count)
    ]


def ranking(*pairs: tuple[int, float]) -> SimpleNamespace:
    return SimpleNamespace(
        data=[SimpleNamespace(index=index, score=score) for index, score in pairs]
    )


class StubInference:
    """Captures the rerank payload and replays a canned ranking (or an error)."""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def rerank(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class StubResources:
    def __init__(self, inference: StubInference, *, model: str = "bge-reranker-v2-m3") -> None:
        self._inference = inference
        self.settings = SimpleNamespace(rerank_model=model)
        self.client_calls = 0

    async def pinecone_client(self) -> Any:
        self.client_calls += 1
        return SimpleNamespace(inference=self._inference)


# --------------------------------------------------------------------------- #
# reorder: the pure part                                                        #
# --------------------------------------------------------------------------- #


def test_reorder_follows_the_ranking_and_restamps_scores() -> None:
    docs = make_docs(4)
    result = reorder(docs, ranking((3, 0.9), (0, 0.7), (1, 0.2)), top_k=4)
    assert [doc.metadata["id_"] for doc in result] == [3, 0, 1]
    assert [doc.score for doc in result] == pytest.approx([0.9, 0.7, 0.2])


def test_reorder_truncates_to_top_k() -> None:
    docs = make_docs(12)
    result = reorder(docs, ranking(*[(i, 1.0 - i / 20) for i in range(12)]), top_k=4)
    assert len(result) == 4
    assert [doc.metadata["id_"] for doc in result] == [0, 1, 2, 3]


def test_reorder_keeps_the_rest_of_the_document_intact() -> None:
    docs = make_docs(2)
    (first,) = reorder(docs, ranking((1, 0.5)), top_k=1)
    assert first.doc_id == "pinecone:Lipitor:1"
    assert first.metadata == docs[1].metadata
    assert first.content == docs[1].content
    assert first.page_numbers == [1]
    # The originals are untouched, so a partial failure cannot corrupt them.
    assert docs[1].score == pytest.approx(0.99)


def test_reorder_skips_out_of_range_and_repeated_indices() -> None:
    docs = make_docs(3)
    result = reorder(docs, ranking((99, 0.9), (-1, 0.8), (2, 0.7), (2, 0.6)), top_k=4)
    assert [doc.metadata["id_"] for doc in result] == [2]


def test_reorder_of_an_empty_ranking_is_empty() -> None:
    assert reorder(make_docs(3), SimpleNamespace(data=[]), top_k=4) == []


def test_reorder_reads_dict_shaped_rankings() -> None:
    docs = make_docs(2)
    result = reorder(docs, {"data": [{"index": 1, "score": 0.4}]}, top_k=2)
    assert [doc.metadata["id_"] for doc in result] == [1]
    assert result[0].score == pytest.approx(0.4)


# --------------------------------------------------------------------------- #
# rerank_documents: the call, and the fail-soft guarantee                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_rerank_sends_id_and_text_and_asks_for_top_n() -> None:
    inference = StubInference(ranking((2, 0.9), (0, 0.8)))
    resources = StubResources(inference)
    docs = make_docs(4)

    result = await rerank_documents(resources, "what is the dose?", docs, 2)

    (call,) = inference.calls
    assert call["model"] == "bge-reranker-v2-m3"
    assert call["query"] == "what is the dose?"
    assert call["top_n"] == 2
    assert call["return_documents"] is False
    assert call["documents"] == [
        {"id": doc.doc_id, "text": doc.content} for doc in docs
    ]
    assert [doc.metadata["id_"] for doc in result] == [2, 0]


@pytest.mark.asyncio
async def test_rerank_honours_the_configured_model() -> None:
    inference = StubInference(ranking((0, 1.0)))
    resources = StubResources(inference, model="cohere-rerank-3.5")
    await rerank_documents(resources, "q", make_docs(2), 1)
    assert inference.calls[0]["model"] == "cohere-rerank-3.5"


@pytest.mark.asyncio
async def test_rerank_failure_falls_back_to_search_order(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inference = StubInference(error=RuntimeError("pinecone is down"))
    docs = make_docs(12)
    with caplog.at_level(logging.WARNING, logger="MedicalRAG"):
        result = await rerank_documents(StubResources(inference), "q", docs, 4)

    assert result == docs[:4]
    assert "RERANK_FAILED" in caplog.text


@pytest.mark.asyncio
async def test_a_missing_pinecone_key_degrades_instead_of_failing_the_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenResources:
        settings = SimpleNamespace(rerank_model="bge-reranker-v2-m3")

        async def pinecone_client(self) -> Any:
            message = "PINECONE_API_KEY is not set"
            raise ValueError(message)

    docs = make_docs(6)
    with caplog.at_level(logging.WARNING, logger="MedicalRAG"):
        result = await rerank_documents(BrokenResources(), "q", docs, 4)
    assert result == docs[:4]
    assert "RERANK_FAILED" in caplog.text


@pytest.mark.asyncio
async def test_an_empty_ranking_falls_back_to_search_order(
    caplog: pytest.LogCaptureFixture,
) -> None:
    inference = StubInference(SimpleNamespace(data=[]))
    docs = make_docs(6)
    with caplog.at_level(logging.WARNING, logger="MedicalRAG"):
        result = await rerank_documents(StubResources(inference), "q", docs, 3)
    assert result == docs[:3]
    assert "RERANK_EMPTY" in caplog.text


@pytest.mark.asyncio
async def test_no_candidates_means_no_network_call() -> None:
    inference = StubInference(ranking((0, 1.0)))
    resources = StubResources(inference)
    assert await rerank_documents(resources, "q", [], 4) == []
    assert inference.calls == []
    assert resources.client_calls == 0


@pytest.mark.asyncio
async def test_fewer_candidates_than_top_k_still_reranks() -> None:
    inference = StubInference(ranking((1, 0.9), (0, 0.1)))
    result = await rerank_documents(StubResources(inference), "q", make_docs(2), 4)
    assert [doc.metadata["id_"] for doc in result] == [1, 0]


@pytest.mark.asyncio
async def test_rerank_logs_the_applied_stage_with_timing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Wall-time has no per-stage hook in the runtime, so the log line is the record."""
    inference = StubInference(ranking((0, 0.9)))
    with caplog.at_level(logging.INFO, logger="MedicalRAG"):
        await rerank_documents(StubResources(inference), "q", make_docs(12), 4)
    assert "RERANK_APPLIED candidates=12 kept=1" in caplog.text
    assert "ms=" in caplog.text
