"""Deterministic graph-state projection into the legacy evaluation record."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Final

from healthcare_rag.graph.settings import GraphSettings
from healthcare_rag.graph.state import JSONValue, load_results
from healthcare_rag.processors.safety import scrub_phi

EVENT_KIND_RANK: Final = {"clarify": 0, "retrieve": 1, "merge": 2}
ROUTER_SENSITIVE_KEYS: Final = frozenset(
    {"history", "input", "output", "prompt", "query", "question", "raw"}
)


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


def _safe_router_telemetry(value: JSONValue) -> JSONValue:
    if isinstance(value, str):
        return scrub_phi(value)[0]
    if isinstance(value, list):
        return [_safe_router_telemetry(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _safe_router_telemetry(item)
            for key, item in value.items()
            if not any(marker in key.lower() for marker in ROUTER_SENSITIVE_KEYS)
        }
    return value


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
    if (
        validated is None
        and not refusal
        and not validate_disabled
        and selected in statuses
    ):
        statuses[selected] = "FAILED"
    return list(statuses.items())


def build_result(
    state: dict[str, Any],
    calls: list[Any],
    context: ResultContext,
) -> tuple[dict[str, Any], bool]:
    """Project persisted graph state without exposing the input question channel."""
    from evals.usage import summarize_usage

    refusal = bool(state.get("safety_response"))
    direct_response = (
        None if refusal else scrub_phi(state.get("direct_response") or "")[0] or None
    )
    contexts: list[dict[str, Any]] = []
    if not refusal and direct_response is None and (merged_data := state.get("merged")):
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
    folded = (
        []
        if refusal or direct_response is not None
        else fold_branches(
            state.get("branch_events", []),
            state.get("selected_branch_type"),
            state.get("validated"),
            refusal=False,
            validate_disabled="validate" in context.settings.disabled_stages,
        )
    )
    answer_source = (
        "\n\n".join(
            part
            for part in [
                *(state.get("safety_notices") or []),
                state.get("safety_response") or "",
            ]
            if part
        )
        if refusal
        else state.get("answer") or ""
    )
    answer = scrub_phi(answer_source)[0] or None
    raw_answer = (
        None
        if refusal
        else (
            direct_response
            or scrub_phi(str(generation.get("plain_answer") or ""))[0]
            or None
        )
    )
    follow_ups = (
        []
        if refusal
        else [scrub_phi(item)[0] for item in state.get("follow_ups") or []]
    )
    selected_query = scrub_phi(state.get("selected_branch_query") or "")[0] or None
    first = context.timing.first_answer or context.timing.finalized
    record = {
        "answer": answer,
        "answered": bool(answer),
        "raw_answer": raw_answer,
        "follow_ups": follow_ups,
        "contexts": contexts,
        "retrieved_chunk_ids": [
            c["chunk_id"] for c in contexts if c["chunk_id"] is not None
        ],
        "retrieved_pages": sorted(
            {page for c in contexts for page in c["page_numbers"]}
        ),
        "retrieved_sources": sorted({c["source"] for c in contexts}),
        "latency_s": round(
            (context.timing.finalized or context.timing.ended) - context.timing.started,
            3,
        ),
        "time_to_first_answer_s": (
            round(first - context.timing.started, 3) if first is not None else None
        ),
        "usage": summarize_usage(calls),
        "per_call_usage": [
            asdict(call) | {"cost_usd": call.cost_usd} for call in calls
        ],
        "safety_outcome": state.get("safety"),
        "query_router": _safe_router_telemetry(state["query_router"])
        if not refusal and state.get("query_router") is not None
        else None,
        "error": context.error
        or ("PIPELINE_STATE_ERROR" if state.get("error") else None),
        "n_branches": len(folded),
        "branch_types": [branch for branch, _ in folded],
        "branch_statuses": [status for _, status in folded],
        "selected_branch_type": (
            None
            if refusal or direct_response is not None
            else state.get("selected_branch_type")
        ),
        "selected_branch_query": None
        if refusal or direct_response is not None
        else selected_query,
    }
    if direct_response is not None:
        record["response_action"] = state.get("response_action")
    used_history = bool((state.get("summary") or {}).get("required_context", False))
    return record, used_history
