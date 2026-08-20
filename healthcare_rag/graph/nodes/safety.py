from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from importlib import import_module
from typing import Protocol, override

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Overwrite

from healthcare_rag.graph.history import build_history_views
from healthcare_rag.graph.llm import LangChainLLMGateway
from healthcare_rag.graph.nodes import render_display_answer
from healthcare_rag.graph.resources import get as get_resources
from healthcare_rag.graph.state import JSONValue, RAGState
from healthcare_rag.models.safety import SafetyAssessment, SafetyOutcome
from healthcare_rag.processors.safety import SafetyGate, addendum_is_safe, scrub_phi
from healthcare_rag.processors.safety_responses import ADDENDUM_HEADING
from healthcare_rag.services.models import safety_gate_enabled

logger = logging.getLogger("MedicalRAG")
GATEWAY: LangChainLLMGateway | None = None


class _Pipeline(Protocol):
    async def ainvoke(
        self,
        state: RAGState,
        config: RunnableConfig | None = None,
    ) -> RAGState: ...


PIPELINE: _Pipeline | None = None
type SafetyUpdateValue = (
    JSONValue | Overwrite | list[str] | list[dict[str, JSONValue]]
)
type SafetyGateUpdate = dict[str, SafetyUpdateValue]


class LangChainSafetyGate(SafetyGate):
    @override
    async def _llm_assess(
        self,
        query: str,
        history_context: str = "",
    ) -> SafetyAssessment:
        default = SafetyAssessment(
            category="ambiguous",
            contains_phi=False,
            phi_spans=[],
            drug_mentioned="none",
            rationale="safety-gate LLM call failed; deterministic checks only",
            safe_reformulation=None,
        )
        llm_call = self._llm_call
        if llm_call is None:
            return default
        result = await llm_call(
            prompt_name="safety_gate",
            temperature=0.0,
            response_format=SafetyAssessment,
            default_response=default,
            user_query=query,
            conversation_context=history_context or "",
        )
        return result or default


def _get_pipeline() -> _Pipeline:
    pipeline = PIPELINE
    if pipeline is None:
        build_module = import_module("healthcare_rag.graph.build")
        pipeline = build_module.build_pipeline(include_follow_ups=False).compile(
            checkpointer=False
        )
        globals()["PIPELINE"] = pipeline
    return pipeline


async def safety_gate(state: RAGState) -> SafetyGateUpdate:
    question = state.get("question", "")
    gate_on = safety_gate_enabled() and not state.get("skip_safety", False)
    settings = get_resources().settings
    messages: list[BaseMessage] = list(state.get("messages", []))
    history_context, processed_history = build_history_views(
        messages,
        settings.history_max_tokens,
        gate_on,
    )
    serialized_history: list[dict[str, JSONValue]] = [
        {
            "timestamp": entry["timestamp"],
            "user_query": entry["user_query"],
            "answer": entry["answer"],
        }
        for entry in processed_history
    ]
    reset: SafetyGateUpdate = {
        "question": "",
        "safety": None,
        "safety_kind": "none",
        "safety_response": "",
        "safety_notices": [],
        "addendum_query": None,
        "addendum_answer": None,
        "summary": None,
        "clarified": None,
        "decomposed": False,
        "sub_queries": [],
        "retrievals": Overwrite([]),
        "merged": None,
        "evaluation": None,
        "gap_round": 0,
        "gap_pending": False,
        "gap_filled": False,
        "generation": None,
        "structured": None,
        "validated": None,
        "answer": None,
        "follow_ups": [],
        "route": Overwrite(["safety_gate:off"]),
        "branch_events": Overwrite([]),
        "selected_branch_type": None,
        "selected_branch_query": None,
        "error": None,
        "history_context": history_context,
        "processed_history": serialized_history,
    }
    if not gate_on:
        scrubbed_question, _ = scrub_phi(question)
        return {
            **reset,
            "scrubbed_question": scrubbed_question,
            "working_query": scrubbed_question,
        }

    async def adapter(
        prompt_name: str,
        temperature: float,
        response_format: type[SafetyAssessment],
        default_response: SafetyAssessment | None = None,
        **prompt_args: str,
    ) -> SafetyAssessment | None:
        try:
            gateway = GATEWAY or get_resources().gateway
            return await gateway.astructured(
                prompt_name,
                response_format,
                temperature=temperature,
                default=default_response,
                **prompt_args,
            )
        except Exception:  # noqa: BLE001 - safety classification must fail soft.
            logger.warning("SAFETY_CLASSIFICATION_FAILED")
            return default_response

    started = time.perf_counter()
    decision = await LangChainSafetyGate(gateway=adapter, temperature=0.0).evaluate(
        question,
        history_context,
    )
    latency = time.perf_counter() - started
    rationale = scrub_phi(decision.assessment.rationale)[0]
    outcome = SafetyOutcome(
        category=decision.assessment.category,
        contains_phi=decision.contains_phi,
        short_circuited=decision.short_circuit,
        response_kind=decision.kind,
        deterministic_flags=decision.flags,
        phi_kinds=decision.phi_kinds,
        llm_calls=decision.llm_calls,
        gate_latency_s=round(latency, 3),
        rationale=rationale,
    )
    return {
        **reset,
        "scrubbed_question": decision.scrubbed_query,
        "working_query": decision.scrubbed_query,
        "safety": outcome.model_dump(mode="json"),
        "safety_kind": decision.kind,
        "safety_response": decision.response or "",
        "safety_notices": decision.notices,
        "addendum_query": decision.addendum_query,
        "route": Overwrite([f"safety_gate:{decision.kind}"]),
    }


async def answer_addendum(
    state: RAGState,
    config: RunnableConfig | None = None,
) -> RAGState:
    aq = state.get("addendum_query")
    if not aq:
        return {"addendum_answer": None}
    try:
        sub = await _get_pipeline().ainvoke(
            {
                "question": aq,
                "scrubbed_question": aq,
                "working_query": aq,
                "messages": [],
                "skip_safety": True,
                "user_id": "",
            },
            config,
        )
    except Exception:  # noqa: BLE001 - mandatory fail-safe refusal boundary.
        logger.error("SAFETY_ADDENDUM_FAILED")
        return {"addendum_answer": None}

    addendum = sub.get("validated") or sub.get("answer")
    if addendum and not addendum_is_safe(addendum):
        logger.info("Safety addendum dropped because it contains a clinical quantity")
        addendum = None
    safety_outcome = dict(state.get("safety") or {})
    safety_outcome["addendum_appended"] = bool(addendum)
    return {
        "addendum_answer": addendum,
        "merged": sub.get("merged"),
        "generation": sub.get("generation"),
        "route": sub.get("route", []),
        "branch_events": sub.get("branch_events", []),
        "selected_branch_type": sub.get("selected_branch_type"),
        "selected_branch_query": sub.get("selected_branch_query"),
        "safety": safety_outcome,
    }


async def finalize(state: RAGState) -> RAGState:
    safety_response = state.get("safety_response", "")
    if safety_response:
        parts = [*state.get("safety_notices", []), safety_response]
        addendum = state.get("addendum_answer")
        if addendum:
            parts.append(f"{ADDENDUM_HEADING}\n\n{addendum}")
        answer = "\n\n".join(part for part in parts if part)
        follow_ups: list[str] = []
    else:
        answer = render_display_answer(
            state.get("validated"),
            state.get("safety_notices", []),
        )
        follow_ups = state.get("follow_ups", [])

    answer = scrub_phi(answer)[0]
    follow_ups = [scrub_phi(question)[0] for question in follow_ups]
    selected_branch_query = state.get("selected_branch_query")
    if selected_branch_query is None:
        selected_branch_query = state.get("working_query")
    selected_branch_query = scrub_phi(selected_branch_query or "")[0] or None
    update: RAGState = {
        "answer": answer or None,
        "follow_ups": follow_ups,
        "selected_branch_query": selected_branch_query,
    }
    if answer:
        timestamp = datetime.now(timezone.utc).isoformat()
        update["messages"] = [
            HumanMessage(
                content=state.get("scrubbed_question", ""),
                additional_kwargs={"ts": timestamp},
            ),
            AIMessage(content=answer, additional_kwargs={"ts": timestamp}),
        ]
    return update
