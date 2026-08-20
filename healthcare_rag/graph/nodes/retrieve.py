from __future__ import annotations

import logging
from typing import Any, Final

import anyio
from langsmith.run_helpers import traceable
from weaviate.exceptions import WeaviateBaseError

from healthcare_rag.graph.resources import get
from healthcare_rag.graph.state import RetrieveInput, dump_results, load_results
from healthcare_rag.models.retrieval import QueryResultList
from healthcare_rag.processors.pageindex_retrieval import pageindex_search
from healthcare_rag.processors.retrieval import (
    hybrid_search,
    union_results,
)
from healthcare_rag.processors.safety import scrub_phi

logger = logging.getLogger("MedicalRAG")

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
    pageindex = resources.settings.retriever == "pageindex"
    # An explicit injection always wins; otherwise the knob picks the arm.
    search = resources.hybrid_search or (pageindex_search if pageindex else hybrid_search)
    for tool_call in tool_calls:
        collection_name = tool_call["name"].removeprefix("query_").capitalize()
        routed_query = scrub_phi(str(tool_call["args"].get("query", query)))[0]

        @traceable(name="retrieve_documents", run_type="retriever")
        async def traced_search() -> QueryResultList:
            # The PageIndex arm reads cached trees/chunks: never open Weaviate for it.
            client = None if pageindex else await resources.weaviate()
            return await search(client, collection_name, routed_query)

        for attempt in range(3):
            try:
                results.append(await traced_search())
                break
            except WeaviateBaseError:
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


async def merge_retrievals(state: dict[str, Any]) -> dict[str, Any]:
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

    return {
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
