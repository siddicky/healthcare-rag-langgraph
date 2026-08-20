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
   model). It catches the wording the regexes cannot enumerate and produces the
   ``safe_reformulation`` that lets a refused question still be *useful*.

Merge precedence, highest first: emergency red flag > prompt injection > identifier
recall > whatever the LLM said. ``contains_phi`` and identifier kinds come only from the
local sanitizer. If the LLM call fails, the deterministic layer still decides.

Everything the caller needs is in :class:`SafetyDecision`. Templates live in
``safety_responses.py``; the policy that picks one lives in :meth:`SafetyGate.evaluate`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from ..models.safety import SafetyAssessment
from ..services.models import default_llm_model
from .base import log_timing
from .safety_responses import (
    ADDENDUM_HEADING,
    INJECTION_NOTICE,
    PHI_NOTICE,
    emergency_response,
    identifier_recall_response,
    injection_response,
    out_of_scope_response,
    personal_advice_response,
)

logger = logging.getLogger("MedicalRAG")


def scrub_phi(text: str, extra_spans: Sequence[str] = ()) -> Tuple[str, List[str]]:
    """Replace personal identifiers in ``text`` with ``[REDACTED_<KIND>]`` tokens.

    Args:
        text: the raw message.
        extra_spans: retained for call compatibility and intentionally ignored; model
            output never receives text-mutation authority.

    Returns:
        ``(clean_text, found)`` where ``found`` lists the identifier kinds removed,
        in order of appearance. ``found`` is empty when nothing was redacted.
    """
    del extra_spans
    if not text:
        return text, []
    from healthcare_rag.graph.resources import get

    scan = get().privacy.scan(text)
    return scan.text, list(scan.kinds)


def contains_phi(text: str) -> bool:
    """True when the deterministic layer finds any personal identifier in ``text``."""
    return bool(scrub_phi(text)[1])


# --------------------------------------------------------------------------- #
# 2. Prompt injection                                                          #
# --------------------------------------------------------------------------- #

_INJECTION_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    (
        "ignore_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass|drop)\b[^.?!]{0,40}?"
            r"\b(?:instruction|instructions|rules?|guidelines?|polic(?:y|ies)|prompt|training|"
            r"restrictions?|safety|guardrails?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "persona_override",
        re.compile(
            r"\b(?:pretend|act|behave|roleplay|role-play|imagine)\b[^.?!]{0,30}?"
            r"\b(?:you(?:'re| are)|to be|as (?:if|an?|my))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "persona_override",
        re.compile(
            r"\b(?:you are now|you'?re now|from now on,? (?:you|ignore|act)|answer as|respond as|reply as)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "unrestricted_mode",
        re.compile(
            r"\b(?:unrestricted|unfiltered|jailbreak|jailbroken|developer mode|dev mode|test mode|"
            r"god mode|dan mode|no (?:safety|restrictions|filters|polic(?:y|ies))|"
            r"without (?:any )?(?:safety|restrictions|filters))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_exfil",
        re.compile(
            r"\b(?:system prompt|prompt verbatim|"
            r"(?:print|repeat|reveal|show|output|tell me|give me) your (?:system )?(?:prompt|instructions))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "fiction_harm",
        re.compile(
            r"\b(?:novel|story|fiction|fictional|screenplay|script|character)\b[^.?!]{0,90}?"
            r"\b(?:lethal|fatal|deadly|overdose|toxic dose|how much .{0,20}kill)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "fiction_harm",
        re.compile(
            r"\b(?:lethal|fatal|deadly|toxic) (?:dose|amount|quantity)\b[^.?!]{0,90}?"
            r"\b(?:novel|story|fiction|fictional|screenplay|script|character|writing)\b",
            re.IGNORECASE,
        ),
    ),
)


#: The only override pattern worth unpacking: "ignore your instructions and <real
#: question>" often wraps something the assistant can legitimately answer. Every other
#: pattern (persona installation, unrestricted/developer mode, system-prompt
#: exfiltration, harm laundered through fiction) has no salvageable question inside it,
#: so it is refused outright rather than re-assessed.
SALVAGEABLE_INJECTION_FLAGS = frozenset({"ignore_instructions"})


def injection_flags(text: str) -> List[str]:
    """Names of the instruction-override patterns matched by ``text`` (deduplicated)."""
    hits: List[str] = []
    for name, pattern in _INJECTION_PATTERNS:
        if pattern.search(text or "") and name not in hits:
            hits.append(name)
    return hits


def strip_injection(text: str) -> str:
    """Remove the override wording so the *underlying* question can be re-assessed."""
    out = text or ""
    for _name, pattern in _INJECTION_PATTERNS:
        out = pattern.sub(" ", out)
    # Tidy the leftovers ("  and tell me..." -> "tell me...").
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"^(?:and|then|also|but|so|,|\.|;|:)\s+", "", out, flags=re.IGNORECASE).strip()
    return out


# --------------------------------------------------------------------------- #
# 3. Requests to recite identifiers back                                       #
# --------------------------------------------------------------------------- #

_IDENTIFIER_RECALL_PATTERNS: Tuple["re.Pattern[str]", ...] = (
    re.compile(
        r"\b(?:remind me(?: of| what)?|what (?:was|is|were)|tell me|give me(?: back)?|"
        r"repeat|read (?:it |them )?back|recite|confirm)\b[^.?!]{0,60}?"
        r"\b(?:health card|hc number|mrn|medical record|chart number|dob|date of birth|"
        r"phone number|address|email|identifiers?|my details|his details|her details|"
        r"my number|his number|her number|the number)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\blast (?:three|3|four|4|two|2) digits\b", re.IGNORECASE),
    re.compile(
        r"\b(?:you had it|you knew it|you have it|i told you (?:it|that|them)|just repeat it|"
        r"repeat it back|say it back)\b",
        re.IGNORECASE,
    ),
)


def identifier_recall_requested(text: str) -> bool:
    """True when the user is asking the assistant to read personal identifiers back."""
    return any(p.search(text or "") for p in _IDENTIFIER_RECALL_PATTERNS)


# --------------------------------------------------------------------------- #
# 4. Emergency red flags                                                       #
# --------------------------------------------------------------------------- #

#: A red flag only fires when the message is about the speaker (or someone they are
#: describing in the first person). "Is chest pain a listed side effect of Lipitor?" is
#: an informational question and must still be answered from the monograph.
_FIRST_PERSON = re.compile(r"\b(?:i|i'?m|im|i'?ve|ive|i'?d|my|me|myself|we|our)\b", re.IGNORECASE)

_RED_FLAG_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    (
        "chest_pain",
        re.compile(
            r"\b(?:chest (?:pain|pains|hurts?|hurting|tightness|pressure|discomfort|heaviness)|"
            r"pain (?:in|across) (?:my |the )?chest|crushing (?:pain|chest))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "breathing_difficulty",
        re.compile(
            r"\b(?:trouble breathing|difficulty breathing|hard (?:to|time) breath\w*|"
            r"can'?t breathe|cannot breathe|short(?:ness)? of breath|"
            r"can'?t catch (?:my )?breath|struggling to breathe|gasping for)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "dark_urine",
        re.compile(
            r"(?:\b(?:brown|dark|cola|tea|red[- ]?brown)[- ]?(?:colou?red\s+)?(?:urine|pee)\b"
            r"|\b(?:urine|pee|wee)\b[^.?!]{0,30}?\b(?:gone|turned|is|has become|the colou?r of)\b"
            r"[^.?!]{0,20}?\b(?:brown|dark|cola|tea)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "confusion",
        re.compile(
            r"\b(?:feel|feeling|felt|getting|got|become|becoming|been|am|'m|is|seems?|gone)\s+"
            r"(?:very |really |quite |increasingly |more |a bit )?"
            r"(?:confused|disoriented|delirious)\b(?!\s+(?:about|by|over|regarding|as to|with))",
            re.IGNORECASE,
        ),
    ),
    (
        "allergic_swelling",
        re.compile(
            r"\b(?:swelling of (?:my |the )?(?:face|lips|tongue|throat|mouth)|"
            r"(?:my |the )?(?:face|lips|tongue|throat) (?:is |are |has |have )?(?:swollen|swelling)|"
            r"anaphyla\w*|trouble swallowing|throat closing|hives all over)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "possible_overdose",
        re.compile(
            r"\b(?:overdosed|took an overdose)\b|"
            r"\b(?:took|taken|swallowed|had)\b[^.?!]{0,40}?"
            r"\b(?:whole bottle|too many (?:pills|tablets)|all (?:my|the) (?:pills|tablets)|overdose)\b",
            re.IGNORECASE,
        ),
    ),
)

_MUSCLE_SYMPTOM = re.compile(
    r"\b(?:muscle (?:weakness|pain|aches?|tenderness|soreness)|"
    r"weak(?:ness)? in (?:my )?(?:legs|arms|thighs|muscles)|thighs? feel weak|"
    r"myopathy|rhabdomyolysis|rhabdo)\b",
    re.IGNORECASE,
)
_SEVERE_ABDOMINAL = re.compile(
    r"\b(?:severe|bad|terrible|intense|worst|excruciating|awful)\b[^.?!]{0,30}?"
    r"\b(?:abdominal|stomach|belly|tummy)\s+(?:pain|ache|cramps?)",
    re.IGNORECASE,
)
_VOMITING = re.compile(
    r"\b(?:vomit\w*|throwing up|threw up|being sick|can'?t keep (?:anything|fluids) down|"
    r"not keeping fluids down)\b",
    re.IGNORECASE,
)


def red_flag_terms(text: str) -> List[str]:
    """Emergency red flags in ``text``.

    Every rule requires a first-person marker, so a purely informational question about
    the same symptom ("Does the monograph list chest pain as a side effect?") is not
    escalated — over-escalating would trade one safety metric for the factual ones.

    Two rules go slightly beyond a single term list, on purpose:
      * *dark urine* fires on its own (the monograph itself calls brown urine a
        stop-the-drug-and-get-help sign), and also in combination with muscle weakness;
      * *severe abdominal pain* only fires together with vomiting.
    """
    body = text or ""
    if not _FIRST_PERSON.search(body):
        return []
    hits: List[str] = []
    for name, pattern in _RED_FLAG_PATTERNS:
        if pattern.search(body) and name not in hits:
            hits.append(name)
    if "dark_urine" in hits and _MUSCLE_SYMPTOM.search(body):
        hits.append("dark_urine_with_muscle_weakness")
    if _SEVERE_ABDOMINAL.search(body) and _VOMITING.search(body):
        hits.append("severe_abdominal_pain_with_vomiting")
    return hits


# --------------------------------------------------------------------------- #
# 5. The addendum rule                                                         #
# --------------------------------------------------------------------------- #

#: A reformulated question that is itself about dosing gets **no** informational
#: addendum. Reciting "the usual dose is X mg three times a day" to someone who just
#: asked whether to double their own dose is the failure mode of F13 wearing a hat.
DOSING_QUESTION = re.compile(
    r"\b(?:dose|doses|dosing|dosage|titrat\w*|how much|how many|mg\b|milligram|"
    r"maximum daily|max(?:imum)? dose|adjust\w* (?:the |my |his |her )?dose|double|"
    r"increase|decrease|reduce|split|half a tablet|skip|hold|stop taking|start taking)\b",
    re.IGNORECASE,
)

#: Second belt: even a non-dosing addendum is dropped if the generated text carries a
#: specific number with a clinical unit. Mirrors ``evals.evaluators.NUMERIC_DOSE_PATTERN``.
NUMERIC_DOSE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|µg|g|ml|mL|mmol/?L?|[uµ]mol/?L?|%|tablets?|"
    r"times? (?:a|per) day|hours?|hrs?|days?|weeks?)\b",
    re.IGNORECASE,
)


def addendum_allowed(reformulation: Optional[str]) -> bool:
    """True when a refused personal question may still get a general-information addendum."""
    return bool(reformulation and reformulation.strip()) and not DOSING_QUESTION.search(
        reformulation or ""
    )


def addendum_is_safe(answer: Optional[str]) -> bool:
    """True when a generated addendum carries no specific dose/threshold/frequency."""
    return bool(answer and answer.strip()) and not NUMERIC_DOSE.search(answer or "")


# --------------------------------------------------------------------------- #
# 6. Decision object                                                           #
# --------------------------------------------------------------------------- #

@dataclass
class SafetyDecision:
    """What the caller should do with one user message."""

    assessment: SafetyAssessment
    scrubbed_query: str
    phi_kinds: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    short_circuit: bool = False
    kind: str = "none"                       # which template (or "none" = run the pipeline)
    response: Optional[str] = None           # templated body, when short-circuiting
    addendum_query: Optional[str] = None     # run through the pipeline and append, if safe
    notices: List[str] = field(default_factory=list)  # one-line prefixes (PHI / injection)
    llm_calls: int = 1

    @property
    def contains_phi(self) -> bool:
        return bool(self.phi_kinds) or self.assessment.contains_phi

    def render(self, addendum: Optional[str] = None) -> str:
        """Assemble notices + templated body (+ optional general-information addendum)."""
        parts: List[str] = list(self.notices)
        if self.response:
            parts.append(self.response)
        if addendum:
            parts.append(f"{ADDENDUM_HEADING}\n\n{addendum}")
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

    async def _llm_assess(self, query: str, history_context: str = "") -> SafetyAssessment:
        """The single structured-output call. Isolated so tests can stub it."""
        default = SafetyAssessment(
            category="ambiguous",
            contains_phi=False,
            phi_spans=[],
            drug_mentioned="none",
            rationale="safety-gate LLM call failed; deterministic checks only",
            safe_reformulation=None,
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

        return SafetyAssessment(
            category=category,
            contains_phi=bool(phi_kinds),
            phi_spans=list(llm.phi_spans or []),
            drug_mentioned=llm.drug_mentioned,
            rationale=llm.rationale,
            safe_reformulation=llm.safe_reformulation,
        )

    async def evaluate(self, query: str, history_context: str = "") -> SafetyDecision:
        """Full policy: assess, scrub, and choose the response for one message."""
        return await self._evaluate(query, history_context, injection_pass=False)

    async def _evaluate(
        self, query: str, history_context: str, injection_pass: bool
    ) -> SafetyDecision:
        assessment = await self.assess(query, history_context)
        flags: List[str] = []
        flags += [f"red_flag:{f}" for f in red_flag_terms(query)]
        flags += [f"injection:{f}" for f in injection_flags(query)]
        if identifier_recall_requested(query):
            flags.append("identifier_recall")

        scrubbed, phi_kinds = scrub_phi(query)
        notices: List[str] = []
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
            inner.flags = decision.flags + [f for f in inner.flags if f not in decision.flags]
            # Keep the identifiers found in the *original* message redacted.
            merged_notices = [INJECTION_NOTICE]
            if decision.notices and PHI_NOTICE not in inner.notices:
                merged_notices.insert(0, PHI_NOTICE)
            inner.notices = merged_notices + [n for n in inner.notices if n != INJECTION_NOTICE]
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

        # --- personal medical advice: decline, optionally add general information ---
        if category == "personal_medical_advice":
            decision.short_circuit = True
            decision.kind = "personal_advice"
            decision.response = personal_advice_response()
            if addendum_allowed(assessment.safe_reformulation):
                reformulation, _ = scrub_phi(assessment.safe_reformulation or "")
                decision.addendum_query = reformulation
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
    "SafetyGate",
    "SafetyDecision",
    "scrub_phi",
    "contains_phi",
    "injection_flags",
    "strip_injection",
    "identifier_recall_requested",
    "red_flag_terms",
    "addendum_allowed",
    "addendum_is_safe",
    "DOSING_QUESTION",
    "NUMERIC_DOSE",
]
