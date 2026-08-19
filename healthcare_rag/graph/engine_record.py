"""Deterministic graph-state projection into the legacy evaluation record."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Final

from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.graph.state import load_results

EVENT_KIND_RANK: Final = {"clarify": 0, "retrieve": 1, "merge": 2}


@dataclass(frozen=True, slots=True)
class TurnTiming:
    started: float
    first_answer: float | None
    finalized: float | None
    ended: float


@dataclass(frozen=True, slots=True)
class ResultContext:
    timing: TurnTiming
    settings: GraphSettings
    error: str | None


def fold_branches(
    events: list[dict[str, Any]],
    selected: str | None,
    validated: str | None,
    *,
    refusal: bool,
    validate_disabled: bool,
) -> list[tuple[str, str]]:
    """Fold append-only events; arrival order never changes branch telemetry."""
    ordered = sorted(
        events,
        key=lambda event: (
            int(event.get("phase", 0)),
            EVENT_KIND_RANK.get(str(event.get("kind", "")), 99),
            int(event.get("index", 0)),
        ),
    )
    statuses: dict[str, str] = {}
    for event in ordered:
        branch = str(event.get("branch", ""))
        if branch:
            statuses[branch] = str(event.get("status", "FAILED"))
    if validated is None and not refusal and not validate_disabled and selected in statuses:
        statuses[selected] = "FAILED"
    return list(statuses.items())


def build_result(
    state: dict[str, Any],
    calls: list[Any],
    context: ResultContext,
) -> tuple[dict[str, Any], bool]:
    """Project persisted graph state without exposing the input question channel."""
    from evals.usage import summarize_usage

    contexts: list[dict[str, Any]] = []
    if merged_data := state.get("merged"):
        for result in load_results(merged_data).results:
            for document in result.docs:
                metadata = document.metadata or {}
                contexts.append(
                    {
                        "content": document.content,
                        "source": document.source_name,
                        "chunk_id": metadata.get("id_"),
                        "page_numbers": document.page_numbers or [],
                        "score": document.score,
                        "routed_query": result.query,
                    }
                )
    generation = state.get("generation") or {}
    folded = fold_branches(
        state.get("branch_events", []),
        state.get("selected_branch_type"),
        state.get("validated"),
        refusal=bool(state.get("safety_response")),
        validate_disabled="validate" in context.settings.disabled_stages,
    )
    answer = state.get("answer")
    first = context.timing.first_answer or context.timing.finalized
    record = {
        "answer": answer,
        "answered": bool(answer),
        "raw_answer": generation.get("plain_answer"),
        "follow_ups": state.get("follow_ups") or [],
        "contexts": contexts,
        "retrieved_chunk_ids": [c["chunk_id"] for c in contexts if c["chunk_id"] is not None],
        "retrieved_pages": sorted({page for c in contexts for page in c["page_numbers"]}),
        "retrieved_sources": sorted({c["source"] for c in contexts}),
        "latency_s": round(
            (context.timing.finalized or context.timing.ended) - context.timing.started,
            3,
        ),
        "time_to_first_answer_s": (
            round(first - context.timing.started, 3) if first is not None else None
        ),
        "usage": summarize_usage(calls),
        "per_call_usage": [asdict(call) | {"cost_usd": call.cost_usd} for call in calls],
        "safety_outcome": state.get("safety"),
        "error": context.error or state.get("error"),
        "n_branches": len(folded),
        "branch_types": [branch for branch, _ in folded],
        "branch_statuses": [status for _, status in folded],
        "selected_branch_type": state.get("selected_branch_type"),
        "selected_branch_query": state.get("selected_branch_query"),
    }
    used_history = bool((state.get("summary") or {}).get("required_context", False))
    return record, used_history
