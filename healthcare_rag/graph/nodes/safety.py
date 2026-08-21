from __future__ import annotations

import logging
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import TypeAlias, cast

from langchain_core.messages import BaseMessage
from langgraph.types import Command, Overwrite

from healthcare_rag.graph.history import build_history_views
from healthcare_rag.graph.llm import LangChainLLMGateway
from healthcare_rag.graph.nodes import safety_classifier, safety_finalize
from healthcare_rag.graph.resources import get as get_resources
from healthcare_rag.graph.routers import GateTarget, route_after_gate
from healthcare_rag.graph.state import JSONValue, RAGState
from healthcare_rag.models.safety import SafetyAssessment, SafetyCategory, SafetyOutcome
from healthcare_rag.processors.refusal_boundary import (
    TEMPLATE_VERSION,
    BoundaryKind,
    RefusalBoundary,
    allowed_responses,
    boundary_hit,
    derive_boundary_topic,
    load_boundaries,
    upsert_boundary,
)
from healthcare_rag.processors.safety import scrub_phi
from healthcare_rag.processors.safety_responses import (
    PHI_NOTICE,
    emergency_response,
    injection_response,
    personal_advice_response,
)
from healthcare_rag.processors.social_responses import default_social_arm_output
from healthcare_rag.services.models import refusal_boundary_enabled, safety_gate_enabled

LangChainSafetyGate = safety_classifier.LangChainSafetyGate
finalize = safety_finalize.finalize

logger = logging.getLogger("MedicalRAG")
GATEWAY: LangChainLLMGateway | None = None
KIND_TO_CATEGORY: dict[BoundaryKind, SafetyCategory] = {
    "personal_advice": "personal_medical_advice",
    "emergency": "emergency_red_flag",
    "injection": "prompt_injection",
}


SafetyUpdateValue: TypeAlias = (
    JSONValue | Overwrite | list[str] | list[dict[str, JSONValue]]
)
SafetyGateUpdate: TypeAlias = dict[str, SafetyUpdateValue]


def _gate_command(state: RAGState, update: SafetyGateUpdate) -> Command[GateTarget]:
    return Command(
        update=update,
        goto=route_after_gate(cast(RAGState, cast(object, {**state, **update}))),
    )


async def safety_gate(state: RAGState) -> Command[GateTarget]:
    question = state.get("question", "")
    settings = get_resources().settings
    messages: list[BaseMessage] = list(state.get("messages", []))
    history_context, processed_history = build_history_views(
        messages,
        settings.history_max_tokens,
        safety_gate_enabled(),
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
        "direct_response": None,
        "response_action": None,
        "query_router": None,
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
    if not safety_gate_enabled():
        scrubbed_question, _ = scrub_phi(question)
        return _gate_command(
            state,
            {
                **reset,
                "scrubbed_question": scrubbed_question,
                "working_query": scrubbed_question,
            },
        )

    boundary_on = refusal_boundary_enabled()
    valid: list[RefusalBoundary] = []
    if boundary_on:
        scrubbed, phi_kinds = scrub_phi(question)
        valid = load_boundaries(state.get("refusal_boundaries") or [])
        hit = boundary_hit(scrubbed, valid)
        if hit is not None:
            return _gate_command(
                state,
                {
                    **reset,
                    "scrubbed_question": scrubbed,
                    "working_query": scrubbed,
                    "safety_response": hit.response,
                    "safety_kind": f"boundary:{hit.kind}",
                    "safety_notices": [PHI_NOTICE] if phi_kinds else [],
                    "safety": SafetyOutcome(
                        category=KIND_TO_CATEGORY[hit.kind],
                        contains_phi=bool(phi_kinds),
                        short_circuited=True,
                        response_kind="boundary_replay",
                        deterministic_flags=[f"boundary_hit:{hit.kind}:{hit.topic}"],
                        phi_kinds=phi_kinds,
                        llm_calls=0,
                        classifier_backend="none",
                        classifier_calls=0,
                        embedding_calls=0,
                        boundary_hit=True,
                        boundaries_active=len(valid),
                    ).model_dump(mode="json"),
                    "route": Overwrite([f"safety_gate:boundary:{hit.kind}"]),
                },
            )

    async def adapter(
        prompt_name: str,
        temperature: float,
        response_format: type[SafetyAssessment],
        default_response: SafetyAssessment | None = None,
        **prompt_args: str,
    ) -> SafetyAssessment | None:
        with suppress(Exception):
            gateway = GATEWAY or get_resources().gateway
            return await gateway.astructured(
                prompt_name,
                response_format,
                temperature=temperature,
                default=default_response,
                **prompt_args,
            )
        logger.warning("SAFETY_CLASSIFICATION_FAILED")
        return default_response

    started = time.perf_counter()
    decision = await LangChainSafetyGate(gateway=adapter, temperature=0.0).evaluate(
        question,
        history_context,
    )
    boundary_update: SafetyGateUpdate = {}
    if (
        boundary_on
        and decision.short_circuit
        and decision.kind in {"personal_advice", "emergency", "injection"}
    ):
        boundary_kinds: dict[str, BoundaryKind] = {
            "personal_advice": "personal_advice",
            "emergency": "emergency",
            "injection": "injection",
        }
        boundary_kind = boundary_kinds[decision.kind]
        raw = list(state.get("refusal_boundaries") or [])
        fallback = {
            "personal_advice": personal_advice_response(),
            "emergency": emergency_response(
                overdose="red_flag:possible_overdose" in decision.flags
            ),
            "injection": injection_response(),
        }[decision.kind]
        new = RefusalBoundary(
            kind=boundary_kind,
            topic=derive_boundary_topic(
                decision.scrubbed_query,
                decision.assessment.drug_mentioned,
            ),
            response=(
                decision.response
                if decision.response in allowed_responses(boundary_kind)
                else fallback
            ),
            created_ts=datetime.now(UTC).isoformat(),
            template_version=TEMPLATE_VERSION,
        )
        boundary_update["refusal_boundaries"] = upsert_boundary(raw, new)
    latency = time.perf_counter() - started
    social_output = default_social_arm_output(
        decision.response or "", decision.kind, decision.short_circuit
    )
    social_intent = (
        decision.assessment.social_intent if decision.kind == "out_of_scope" else None
    )
    benign_social = decision.assessment.benign_social and social_intent is not None
    social_output = social_output.for_social_turn(
        settings.query_response_arm, social_intent, benign_social
    )
    outcome = SafetyOutcome(
        category=decision.assessment.category,
        contains_phi=decision.contains_phi,
        short_circuited=social_output.short_circuited,
        response_kind=social_output.response_kind,
        deterministic_flags=decision.flags,
        phi_kinds=decision.phi_kinds,
        llm_calls=decision.llm_calls,
        benign_social=benign_social,
        social_intent=social_intent,
        classifier_backend="llm",
        classifier_calls=decision.llm_calls,
        embedding_calls=0,
        classifier_latency_s=round(latency, 3),
        boundary_hit=False,
        boundaries_active=len(valid),
        gate_latency_s=round(latency, 3),
        rationale=scrub_phi(decision.assessment.rationale)[0],
    )
    return _gate_command(
        state,
        {
            **reset,
            **boundary_update,
            "scrubbed_question": decision.scrubbed_query,
            "working_query": decision.scrubbed_query,
            "safety": outcome.model_dump(mode="json"),
            "safety_kind": social_output.safety_kind,
            "safety_response": social_output.safety_response,
            "safety_notices": decision.notices,
            "direct_response": social_output.direct_response,
            "response_action": social_output.response_action,
            "route": Overwrite([f"safety_gate:{social_output.route_kind}"]),
        },
    )
