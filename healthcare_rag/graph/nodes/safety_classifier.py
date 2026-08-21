from __future__ import annotations

from typing import override

from healthcare_rag.models.safety import SafetyAssessment
from healthcare_rag.processors.safety import SafetyGate


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
