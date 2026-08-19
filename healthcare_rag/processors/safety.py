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
Two layers, OR-ed together, because they fail differently:

1. **Deterministic pre-checks** (this module, no network): regexes for personal
   identifiers, instruction-override attempts, requests to recite identifiers back, and
   emergency red-flag symptoms. Cheap, auditable, and unaffected by how the model feels
   today. This is the *floor* — it can only ever escalate the outcome, never relax it.
2. **One LLM classification call** (``prompts/safety_gate.yaml.j2`` ->
   :class:`~healthcare_rag.models.safety.SafetyAssessment`, temperature 0, default
   model). It catches the wording the regexes cannot enumerate and produces the
   ``safe_reformulation`` that lets a refused question still be *useful*.

Merge precedence, highest first: emergency red flag > prompt injection > identifier
recall > whatever the LLM said. ``contains_phi`` is the OR of both layers. If the LLM
call fails, the deterministic layer still decides (fail-safe for the four things it can
see; fail-open for classification, which is exactly what the pre-existing pipeline did).

Everything the caller needs is in :class:`SafetyDecision`. Templates live in
``safety_responses.py``; the policy that picks one lives in :meth:`SafetyGate.evaluate`.
"""

from __future__ import annotations

import html
import logging
import re
import openai
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from ..models.safety import SafetyAssessment
from ..services.llm import LLMParserService
from ..services.models import default_llm_model
from .base import PromptManager, log_timing
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


# --------------------------------------------------------------------------- #
# 1. Personal identifiers (PHI)                                                #
# --------------------------------------------------------------------------- #

# A name token: capital + lowercase ("Smith", "O'Brien") or an initial ("J.").
# All-caps tokens are excluded on purpose so "pt J. Tremblay MRN 004512" stops the
# name at "Tremblay" and leaves "MRN 004512" for the MRN pattern.
_NAME_TOKEN = r"(?:[A-Z][a-z][A-Za-z'’\-]*|[A-Z]\.)"

#: First name tokens that are almost always a false positive after "I'm"/"patient".
_NAME_STOPWORDS = frozenset(
    {
        "type", "lipitor", "metformin", "atorvastatin", "teva", "ontario", "canadian",
        "sorry", "just", "also", "hi", "hello", "yes", "no", "not", "still", "really",
        "very", "currently", "on", "taking", "trying", "worried", "scared", "confused",
        "fine", "okay", "ok", "diabetic", "male", "female", "the", "a", "an", "my",
        "she", "he", "they", "asking", "wondering", "having", "getting", "new",
    }
)

_PHI_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    # Ontario health card "1234-567-890" (optionally + two-letter version code).
    ("HEALTH_CARD", re.compile(r"(?<!\d)\d{4}[-\s]\d{3}[-\s]\d{3}(?:[-\s][A-Z]{2})?(?!\d)")),
    (
        "HEALTH_CARD",
        re.compile(
            r"\b(?:health\s*card|hc|ohip)\s*(?:number|num|no\.?|#)?\s*[:#]?\s*\d[\d\-\s]{6,14}\d",
            re.IGNORECASE,
        ),
    ),
    (
        "MRN",
        re.compile(
            r"\b(?:mrn|m\.r\.n\.|medical\s+record\s*(?:number|num|no\.?|#)?|chart\s*(?:number|no\.?|#)"
            r"|hospital\s*(?:number|no\.?|#)|patient\s*(?:id|number|no\.?|#))\s*[:#]?\s*[A-Za-z]{0,3}\d{4,10}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "DOB",
        re.compile(
            r"\b(?:dob|d\.o\.b\.|date\s+of\s+birth|born(?:\s+on)?)\b\s*[:\-]?\s*"
            r"(?:\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}"
            r"|\d{1,2}\s+[A-Za-z]{3,9},?\s+\d{4}"
            r"|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
            re.IGNORECASE,
        ),
    ),
    ("DOB", re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")),
    ("POSTAL_CODE", re.compile(r"\b[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d\b")),
    (
        "ADDRESS",
        re.compile(
            r"\b\d{1,6}\s+(?:[A-Z][A-Za-z.\-']*\s+){1,3}"
            r"(?i:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|court|ct|way"
            r"|crescent|cres|place|pl|terrace|trail|parkway|pkwy)\b\.?"
            r"(?:\s*,?\s*(?i:apt|apartment|unit|suite|ste)\.?\s*#?\s*\w+)?"
        ),
    ),
    (
        "NAME",
        re.compile(
            r"\b(?i:my name is|i'?m|i am|this is|name is|name:|patient|pt\.?|resident|client)\s+"
            rf"({_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{0,3}})"
        ),
    ),
    # "save my details for next time - Emeka Okafor, 22 Maple Ave ..." — the name is not
    # introduced by "my name is", so allow a short lower-case run between the cue and it.
    (
        "NAME",
        re.compile(
            r"\b(?i:save my details|my details are|my details|my info|my contact info|contact details|"
            r"details for (?:next time|your records)|here are my details|for my file)"
            r"[^A-Z\n]{0,25}"
            rf"({_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{1,3}})"
        ),
    ),
)

#: Words the *model* sometimes returns as "identifiers" that must never be redacted.
_PHI_SPAN_DENYLIST = frozenset(
    {
        "lipitor", "metformin", "atorvastatin", "teva-metformin", "patient", "doctor",
        "pharmacist", "nurse", "me", "my", "i", "you", "diabetes", "cholesterol",
    }
)


def _phi_matches(text: str) -> List[Tuple[int, int, str, str]]:
    """Return ``(start, end, kind, matched_text)`` for every deterministic PHI hit."""
    spans: List[Tuple[int, int, str, str]] = []
    for kind, pattern in _PHI_PATTERNS:
        for m in pattern.finditer(text):
            # For NAME the identifier is group 1 (the intro phrase is not PHI).
            start, end = (m.span(1) if kind == "NAME" and m.lastindex else m.span())
            value = text[start:end]
            if kind == "NAME":
                first = re.split(r"\s+", value.strip())[0].strip(".'’").lower()
                if first in _NAME_STOPWORDS or len(value.strip()) < 2:
                    continue
            spans.append((start, end, kind, value))
    return spans


def _extra_span_matches(text: str, extra_spans: Sequence[str]) -> List[Tuple[int, int, str, str]]:
    """Locate model-reported identifier spans that really occur in ``text``.

    ``PromptManager`` renders prompts with Jinja autoescaping on, so the model sees
    ``O&#39;Brien`` where the raw message says ``O'Brien``. Both forms are tried, so a
    faithfully copied span still lines up with the text we are about to scrub.
    """
    out: List[Tuple[int, int, str, str]] = []
    for span in extra_spans or ():
        raw = (span or "").strip()
        if len(raw) < 3 or len(raw) > 120 or raw.lower() in _PHI_SPAN_DENYLIST:
            continue
        for candidate in dict.fromkeys([raw, html.unescape(raw), html.escape(raw, quote=True)]):
            if not candidate:
                continue
            for m in re.finditer(re.escape(candidate), text):
                out.append((m.start(), m.end(), "IDENTIFIER", candidate))
    return out


def _merge_spans(spans: List[Tuple[int, int, str, str]]) -> List[Tuple[int, int, str, str]]:
    """Drop overlapping spans, keeping the longest (leftmost wins on a tie)."""
    ordered = sorted(spans, key=lambda s: (s[0], -(s[1] - s[0])))
    kept: List[Tuple[int, int, str, str]] = []
    for span in ordered:
        if kept and span[0] < kept[-1][1]:
            if span[1] > kept[-1][1] and span[0] >= kept[-1][0]:
                continue  # partial overlap to the right: keep the earlier, longer span
            continue
        kept.append(span)
    return kept


def scrub_phi(text: str, extra_spans: Sequence[str] = ()) -> Tuple[str, List[str]]:
    """Replace personal identifiers in ``text`` with ``[REDACTED_<KIND>]`` tokens.

    Args:
        text: the raw message.
        extra_spans: additional verbatim identifier strings (e.g. the LLM's
            ``phi_spans``); only used when they actually occur in ``text``.

    Returns:
        ``(clean_text, found)`` where ``found`` lists the identifier kinds removed,
        in order of appearance. ``found`` is empty when nothing was redacted.
    """
    if not text:
        return text, []
    spans = _merge_spans(_phi_matches(text) + _extra_span_matches(text, extra_spans))
    if not spans:
        return text, []
    out = text
    for start, end, kind, _value in sorted(spans, key=lambda s: s[0], reverse=True):
        out = f"{out[:start]}[REDACTED_{kind}]{out[end:]}"
    return out, [kind for _s, _e, kind, _v in sorted(spans, key=lambda s: s[0])]


def contains_phi(text: str) -> bool:
    """True when the deterministic layer finds any personal identifier in ``text``."""
    return bool(_phi_matches(text))


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
        async_client: openai.AsyncOpenAI | None = None,
        prompt_manager: PromptManager | None = None,
        parser_service: LLMParserService | None = None,
    ) -> None:
        self.gateway: SafetyLLMCall | None = gateway
        self.temperature: float = temperature
        self.llm_model: str = llm_model or default_llm_model()
        self.async_client: openai.AsyncOpenAI | None = async_client
        self.pm: PromptManager = prompt_manager or PromptManager()
        self.parser_service: LLMParserService | None = parser_service
        self._llm_call: SafetyLLMCall = (
            llm_call or gateway or self._legacy_llm_call
        )

    async def _legacy_llm_call(
        self,
        prompt_name: str,
        temperature: float,
        response_format: type[SafetyAssessment],
        default_response: SafetyAssessment | None = None,
        **prompt_args: str,
    ) -> SafetyAssessment | None:
        messages = self.pm.messages(prompt_name, **prompt_args)
        if self.parser_service is None:
            logger.error("LLM parser service is not initialized")
            return default_response
        return await self.parser_service.parse_completion(
            model=self.llm_model,
            messages=messages,
            temperature=temperature,
            response_format=response_format,
            default_response=default_response,
        )

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
        result = await self._llm_call(
            prompt_name="safety_gate",
            temperature=0.0,
            response_format=SafetyAssessment,
            default_response=default,
            user_query=query,
            conversation_context=history_context or "",
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
        det_phi = _phi_matches(query)

        llm = await self._llm_assess(query, history_context)

        category = llm.category
        if det_red_flags:
            category = "emergency_red_flag"
        elif det_injection and category != "emergency_red_flag":
            category = "prompt_injection"

        spans = list(llm.phi_spans or [])
        for _s, _e, _kind, value in det_phi:
            if value not in spans:
                spans.append(value)

        return SafetyAssessment(
            category=category,
            contains_phi=bool(llm.contains_phi or det_phi),
            phi_spans=spans,
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

        scrubbed, phi_kinds = scrub_phi(query, extra_spans=assessment.phi_spans)
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
                reformulation, _ = scrub_phi(
                    assessment.safe_reformulation or "", extra_spans=assessment.phi_spans
                )
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
