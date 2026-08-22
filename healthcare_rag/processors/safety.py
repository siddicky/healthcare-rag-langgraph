"""
Runtime safety gate — the first thing that touches a user message.

Why this exists (docs/journey.json F13, F18)
--------------------------------------------
The baseline had no runtime guard at all: "My sugar was 14, should I just double my
metformin tonight?" came back with a dosing table, out-of-scope questions came back
empty, and ``safe_redirect`` scored 0.00-0.33 across every model configuration. The
prompts asked for good behaviour; nothing enforced it. LangSmith Insights over the
luna+terra traces independently flagged personal-dosing advice as ~10% of sampled
turns (F18).

Design
------
Privacy sanitization runs first and is independent of the classification switch. Safety
classification then uses two layers, OR-ed together, because they fail differently:

1. **Deterministic pre-checks** (this module, no network): regexes for
   instruction-override attempts, requests to recite identifiers back, and emergency
   red-flag symptoms. Cheap, auditable, and unaffected by how the model feels today.
   This is the *floor* — it can only ever escalate the outcome, never relax it.
2. **One LLM classification call** (``prompts/safety_gate.yaml.j2`` ->
   :class:`~healthcare_rag.models.safety.SafetyAssessment`, temperature 0, default
   model). It catches the wording the regexes cannot enumerate.

Merge precedence, highest first: emergency red flag > prompt injection > identifier
recall > whatever the LLM said. ``contains_phi`` and identifier kinds come only from the
local sanitizer. If the LLM call fails, the deterministic layer still decides.

Everything the caller needs is in :class:`SafetyDecision`. Templates live in
``safety_responses.py``; the policy that picks one lives in :meth:`SafetyGate.evaluate`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..models.safety import SafetyAssessment
from ..services.models import default_llm_model
from .base import log_timing
from .safety_patterns import NUMERIC_DOSE
from .safety_responses import (
    INJECTION_NOTICE,
    PHI_NOTICE,
    emergency_response,
    identifier_recall_response,
    injection_response,
    out_of_scope_response,
    personal_advice_response,
)
from .safety_signals import (
    DOSING_QUESTION,
    SALVAGEABLE_INJECTION_FLAGS,
    contains_phi,
    identifier_recall_requested,
    injection_flags,
    red_flag_terms,
    scrub_phi,
    strip_injection,
)

logger = logging.getLogger("MedicalRAG")


@dataclass
class SafetyDecision:
    """What the caller should do with one user message."""

    assessment: SafetyAssessment
    scrubbed_query: str
    phi_kinds: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    short_circuit: bool = False
    kind: str = "none"  # which template (or "none" = run the pipeline)
    response: str | None = None  # templated body, when short-circuiting
    notices: list[str] = field(
        default_factory=list
    )  # one-line prefixes (PHI / injection)
    llm_calls: int = 1

    @property
    def contains_phi(self) -> bool:
        return bool(self.phi_kinds) or self.assessment.contains_phi

    def render(self) -> str:
        """Assemble notices and the templated response body."""
        parts: list[str] = list(self.notices)
        if self.response:
            parts.append(self.response)
        return "\n\n".join(p for p in parts if p)

    def prefix_notices(self, answer: str) -> str:
        """Prepend the notices to an answer produced by the normal pipeline."""
        if not self.notices or not answer:
            return answer
        return "\n\n".join(self.notices + [answer])


# --------------------------------------------------------------------------- #
# 7. The gate                                                                  #
# --------------------------------------------------------------------------- #

SafetyLLMCall = Callable[..., Awaitable[SafetyAssessment | None]]


class SafetyGate:
    """Classify a user message and decide how the application must respond.

    One LLM call per query (two only when an instruction-override attempt is unpacked),
    at temperature 0 on the default model — see ``prompts/safety_gate.yaml.j2``.
    """

    def __init__(
        self,
        gateway: SafetyLLMCall | None = None,
        temperature: float = 0.0,
        llm_call: SafetyLLMCall | None = None,
        *,
        llm_model: str | None = None,
    ) -> None:
        self.gateway: SafetyLLMCall | None = gateway
        self.temperature: float = temperature
        self.llm_model: str = llm_model or default_llm_model()
        self._llm_call: SafetyLLMCall | None = llm_call or gateway

    async def _llm_assess(
        self, query: str, history_context: str = ""
    ) -> SafetyAssessment:
        """The single structured-output call. Isolated so tests can stub it."""
        default = SafetyAssessment(
            category="ambiguous",
            contains_phi=False,
            phi_spans=[],
            drug_mentioned="none",
            rationale="safety-gate LLM call failed; deterministic checks only",
        )
        result = (
            await self._llm_call(
                prompt_name="safety_gate",
                temperature=0.0,
                response_format=SafetyAssessment,
                default_response=default,
                user_query=query,
                conversation_context=history_context or "",
            )
            if self._llm_call is not None
            else None
        )
        return result or default

    @log_timing
    async def assess(self, query: str, history_context: str = "") -> SafetyAssessment:
        """Deterministic pre-checks OR-ed with one LLM classification.

        The pre-checks can only escalate: they force ``emergency_red_flag`` /
        ``prompt_injection`` and can set ``contains_phi``, but never downgrade a category
        the model chose.
        """
        det_red_flags = red_flag_terms(query)
        det_injection = injection_flags(query)
        scrubbed_query, phi_kinds = scrub_phi(query)
        scrubbed_history = scrub_phi(history_context)[0]
        llm = await self._llm_assess(scrubbed_query, scrubbed_history)

        category = llm.category
        if det_red_flags:
            category = "emergency_red_flag"
        elif det_injection and category != "emergency_red_flag":
            category = "prompt_injection"

        accepted_social_intent = (
            llm.social_intent
            if llm.benign_social and category == "out_of_scope"
            else None
        )
        return SafetyAssessment(
            category=category,
            contains_phi=bool(phi_kinds),
            phi_spans=list(llm.phi_spans or []),
            drug_mentioned=llm.drug_mentioned,
            rationale=llm.rationale,
            benign_social=accepted_social_intent is not None,
            social_intent=accepted_social_intent,
        )

    async def evaluate(self, query: str, history_context: str = "") -> SafetyDecision:
        """Full policy: assess, scrub, and choose the response for one message."""
        return await self._evaluate(query, history_context, injection_pass=False)

    async def _evaluate(
        self, query: str, history_context: str, injection_pass: bool
    ) -> SafetyDecision:
        assessment = await self.assess(query, history_context)
        flags: list[str] = []
        flags += [f"red_flag:{f}" for f in red_flag_terms(query)]
        flags += [f"injection:{f}" for f in injection_flags(query)]
        if identifier_recall_requested(query):
            flags.append("identifier_recall")

        scrubbed, phi_kinds = scrub_phi(query)
        notices: list[str] = []
        if phi_kinds:
            notices.append(PHI_NOTICE)

        decision = SafetyDecision(
            assessment=assessment,
            scrubbed_query=scrubbed,
            phi_kinds=phi_kinds,
            flags=flags,
            notices=notices,
            llm_calls=1,
        )

        category = assessment.category

        # --- prompt injection: never comply, then look for a real question underneath ---
        if category == "prompt_injection":
            if injection_pass:
                # Second pass and it is *still* an override attempt: nothing legitimate here.
                decision.short_circuit = True
                decision.kind = "injection"
                decision.response = injection_response()
                return decision
            residual = strip_injection(query)
            unsalvageable = set(injection_flags(query)) - SALVAGEABLE_INJECTION_FLAGS
            if unsalvageable or len(residual.split()) < 3:
                decision.short_circuit = True
                decision.kind = "injection"
                decision.response = injection_response()
                return decision
            inner = await self._evaluate(residual, history_context, injection_pass=True)
            inner.llm_calls = decision.llm_calls + inner.llm_calls
            inner.flags = decision.flags + [
                f for f in inner.flags if f not in decision.flags
            ]
            # Keep the identifiers found in the *original* message redacted.
            merged_notices = [INJECTION_NOTICE]
            if decision.notices and PHI_NOTICE not in inner.notices:
                merged_notices.insert(0, PHI_NOTICE)
            inner.notices = merged_notices + [
                n for n in inner.notices if n != INJECTION_NOTICE
            ]
            inner.phi_kinds = list(dict.fromkeys(decision.phi_kinds + inner.phi_kinds))
            return inner

        # --- emergency red flag: urgent-care redirect, no monograph content ---
        if category == "emergency_red_flag":
            decision.short_circuit = True
            decision.kind = "emergency"
            decision.response = emergency_response(
                overdose="red_flag:possible_overdose" in flags
            )
            return decision

        # --- "read my identifiers back to me": there is nothing to read back ---
        if identifier_recall_requested(query):
            decision.short_circuit = True
            decision.kind = "identifier_recall"
            decision.response = identifier_recall_response()
            return decision

        # --- personal medical advice: decline the individual decision ---
        if category == "personal_medical_advice":
            decision.short_circuit = True
            decision.kind = "personal_advice"
            decision.response = personal_advice_response()
            return decision

        # --- out of scope: say what we cover; no retrieval at all ---
        if category == "out_of_scope":
            decision.short_circuit = True
            decision.kind = "out_of_scope"
            decision.response = out_of_scope_response()
            return decision

        # --- in_scope_informational / ambiguous: run the normal pipeline ---
        # ("ambiguous" is deliberately passed through: the clarify stage already handles it.)
        decision.short_circuit = False
        decision.kind = "none"
        return decision


__all__ = [
    "DOSING_QUESTION",
    "NUMERIC_DOSE",
    "SafetyDecision",
    "SafetyGate",
    "contains_phi",
    "identifier_recall_requested",
    "injection_flags",
    "red_flag_terms",
    "scrub_phi",
    "strip_injection",
]
