from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any, Final, cast

import anyio
from langgraph.types import Command
from langsmith.run_helpers import traceable
from pinecone.exceptions import PineconeException
from weaviate.exceptions import WeaviateBaseError

from healthcare_rag.graph.resources import get
from healthcare_rag.graph.routers import MergeTarget, route_after_merge
from healthcare_rag.graph.state import RAGState, RetrieveInput, dump_results, load_results
from healthcare_rag.models.retrieval import QueryResultList
from healthcare_rag.processors.pageindex_retrieval import pageindex_search
from healthcare_rag.processors.pinecone_retrieval import pinecone_search
from healthcare_rag.processors.rerank import rerank_documents
from healthcare_rag.processors.retrieval import (
    hybrid_search,
    union_results,
)
from healthcare_rag.processors.safety import scrub_phi

logger = logging.getLogger("MedicalRAG")

# One row per retrieval arm: the name of this module's search callable, and the
# SDK error class whose transient failures are worth a retry. PageIndex reads
# cached JSON and opens no client, so it keeps the Weaviate error class purely to
# leave its behaviour byte-identical to before this arm existed.
_ARMS: Final[dict[str, tuple[str, type[Exception]]]] = {
    "weaviate": (hybrid_search.__name__, WeaviateBaseError),
    "pageindex": (pageindex_search.__name__, WeaviateBaseError),
    "pinecone": (pinecone_search.__name__, PineconeException),
}


def resolve_arm(backend: str) -> tuple[Callable[..., Any], type[Exception]]:
    """The search callable and retry error for one arm.

    The callable is looked up by name in this module's namespace rather than
    captured in ``_ARMS``, so a test that patches ``retrieve.hybrid_search``
    still swaps the arm out.
    """
    try:
        attribute, retry_error = _ARMS[backend]
    except KeyError:
        message = f"Unknown retrieval arm {backend!r}; valid: {sorted(_ARMS)}"
        raise ValueError(message) from None
    return globals()[attribute], retry_error


def accepts_limit(search: Callable[..., Any]) -> bool:
    """True when ``search`` can be told how many candidates to fetch.

    An injected ``resources.hybrid_search`` (tests, fixtures) predates the
    ``limit`` kwarg, so it is only ever passed to callables that declare it.
    """
    try:
        parameters = inspect.signature(search).parameters
    except (TypeError, ValueError):
        return False
    return "limit" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


RETRIEVAL_KIND_RANK: Final = {
    "initial": 0,
    "clarified": 0,
    "decomposed": 1,
    "gap_fill": 2,
}
_RETRY_DELAYS: Final = (1.0, 2.0)


async def retrieve_documents(state: RetrieveInput) -> dict[str, Any]:
    resources = get()
    query = scrub_phi(state["query"])[0]
    try:
        tool_calls = await resources.gateway.aroute_tools(query)
    except Exception:  # noqa: BROAD_EXCEPT_OK - routing is a fail-soft external boundary.
        logger.warning("RETRIEVAL_ROUTING_FAILED")
        tool_calls = []

    results: list[QueryResultList | None] = []
    settings = resources.settings
    backend = settings.retriever
    arm_search, retry_error = resolve_arm(backend)
    # An explicit injection always wins; otherwise the knob picks the arm.
    search = resources.hybrid_search or arm_search
    reranking = settings.reranker != "none"
    # Reranking is a re-ordering of a wider candidate set, so the search fetches
    # `rerank_candidates` and the reranker trims back to the usual top-k. With no
    # reranker the limit is never passed at all: the default path stays untouched.
    search_kwargs: dict[str, Any] = (
        {"limit": settings.rerank_candidates}
        if reranking and accepts_limit(search)
        else {}
    )

    for tool_call in tool_calls:
        collection_name = tool_call["name"].removeprefix("query_").capitalize()
        routed_query = scrub_phi(str(tool_call["args"].get("query", query)))[0]

        # Loop variables are bound as defaults: the closure is awaited within
        # this iteration, but binding keeps that guarantee local and explicit.
        @traceable(name="retrieve_documents", run_type="retriever")
        async def traced_search(
            collection_name: str = collection_name, routed_query: str = routed_query
        ) -> QueryResultList:
            # Only the Weaviate/Pinecone arms need a client; PageIndex reads
            # cached trees and chunks, so nothing is opened for it.
            if backend == "weaviate":
                client = await resources.weaviate()
            elif backend == "pinecone":
                client = await resources.pinecone()
            else:
                client = None
            found = await search(client, collection_name, routed_query, **search_kwargs)
            if reranking:
                # Nested inside the retriever run so the trace shows rerank wall-time.
                for result in found.results:
                    result.docs = await rerank_documents(
                        resources,
                        result.query or routed_query,
                        result.docs,
                        settings.rerank_top_k,
                    )
            return found

        for attempt in range(3):
            try:
                results.append(await traced_search())
                break
            except retry_error:
                if attempt == 2:
                    logger.error("RETRIEVAL_FAILED")
                    break
                logger.warning("RETRIEVAL_RETRY")
                await anyio.sleep(_RETRY_DELAYS[attempt])

    combined = union_results(results)
    has_documents = any(result.docs for result in combined.results)
    envelope = {
        "phase": state["phase"],
        "kind": state["kind"],
        "index": state["index"],
        "branch": state["branch"],
        "results": dump_results(combined),
    }
    output: dict[str, Any] = {
        "retrievals": [envelope],
        "route": [
            f"retrieve:{state['kind']}:{state['index']}:{state['phase']}"
        ],
    }
    if state["kind"] != "gap_fill":
        output["branch_events"] = [
            {
                "phase": state["phase"],
                "kind": "retrieve",
                "index": state["index"],
                "branch": state["branch"],
                "status": "COMPLETED" if has_documents else "FAILED",
            }
        ]
    return output


async def merge_retrievals(state: dict[str, Any]) -> Command[MergeTarget]:
    """Fold every branch's retrieval into one result set and pick the next step.

    ``route_after_merge`` reads the post-update ``gap_filled`` channel — the flag
    this node just wrote — so the router runs on the state the update produces.
    """
    envelopes = sorted(
        state.get("retrievals", []),
        key=lambda envelope: (
            envelope["phase"],
            RETRIEVAL_KIND_RANK[envelope["kind"]],
            envelope["index"],
        ),
    )
    merged = union_results(
        [load_results(envelope["results"]) for envelope in envelopes]
    )
    has_documents = any(result.docs for result in merged.results)
    fan_out = any(envelope["kind"] == "decomposed" for envelope in envelopes)
    phase = max((envelope["phase"] for envelope in envelopes), default=0)
    branch = "synthesized" if fan_out else state["selected_branch_type"]

    update: dict[str, Any] = {
        "merged": dump_results(merged),
        "gap_filled": any(
            envelope["kind"] == "gap_fill" for envelope in envelopes
        ),
        "selected_branch_query": state.get("selected_branch_query")
        or state["working_query"],
        "branch_events": [
            {
                "phase": phase,
                "kind": "merge",
                "index": 0,
                "branch": branch,
                "status": "COMPLETED" if has_documents else "FAILED",
            }
        ],
    }
    return Command(
        update=update,
        goto=route_after_merge(cast(RAGState, cast(object, {**state, **update}))),
    )
