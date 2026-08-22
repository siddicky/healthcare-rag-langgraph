from __future__ import annotations

from typing import Any, cast

from langgraph.types import Command

from healthcare_rag.graph.resources import get
from healthcare_rag.graph.routers import EvaluateCommandTarget, route_after_evaluate
from healthcare_rag.graph.state import RAGState, load_results
from healthcare_rag.models.queries import RetrievalEvaluation
from healthcare_rag.processors.safety import scrub_phi


async def evaluate_retrieval(state: dict[str, Any]) -> Command[EvaluateCommandTarget]:
    """Judge the merged retrieval and either gap-fill or move on to generation.

    ``route_after_evaluate`` reads the post-update ``gap_pending`` / ``evaluation``
    channels, so the router runs on the state this update produces; its ``Send``
    objects all target ``retrieve_documents``, which the Literal names alongside
    ``generate_answer``.
    """
    resources = get()
    merged_data = state.get("merged")
    merged = load_results(merged_data) if merged_data else None
    gap_round = state.get("gap_round", 0)

    if (
        merged is None
        or not any(result.docs for result in merged.results)
        or "evaluate" in resources.settings.disabled_stages
    ):
        return _evaluate_command(
            state,
            {
                "evaluation": {"is_sufficient": True},
                "gap_round": gap_round,
                "gap_pending": False,
            },
        )

    combined_context = ""
    sources: list[str] = []
    for result in merged.results:
        combined_context += "\n\n" + "\n\n".join(
            document.content for document in result.docs
        )
        sources.append(result.source)

    active_query = state["working_query"]
    default = RetrievalEvaluation(
        is_sufficient=True,
        missing_information="",
        additional_queries=[],
    )
    evaluation = await resources.gateway.astructured(
        "evaluate_retrieval",
        RetrievalEvaluation,
        temperature=0.1,
        default=default,
        original_query=active_query,
        clarified_query=active_query,
        retrieved_information=combined_context,
        sources=", ".join(sources),
    ) or default
    additional_queries = [
        scrub_phi(query)[0] for query in (evaluation.additional_queries or [])[:3]
    ]
    gap_pending = (
        not evaluation.is_sufficient and gap_round == 0 and bool(additional_queries)
    )
    evaluation_data = evaluation.model_dump(mode="json")
    evaluation_data["missing_information"] = scrub_phi(
        str(evaluation_data.get("missing_information") or "")
    )[0]
    if evaluation.additional_queries:
        evaluation_data["additional_queries"] = additional_queries

    return _evaluate_command(
        state,
        {
            "evaluation": evaluation_data,
            "gap_round": 1 if gap_pending else gap_round,
            "gap_pending": gap_pending,
        },
    )


def _evaluate_command(
    state: dict[str, Any],
    update: dict[str, Any],
) -> Command[EvaluateCommandTarget]:
    """Apply the evaluation update and route on the state it produces."""
    return Command(
        update=update,
        goto=route_after_evaluate(cast(RAGState, cast(object, {**state, **update}))),
    )
