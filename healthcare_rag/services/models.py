"""
Central place for *which* OpenAI models the pipeline uses and *how* to call them.

Why this exists
---------------
The GPT-5.x family (gpt-5.6-luna / -terra / -sol) are reasoning models: they
reject ``temperature``/``top_p`` unless ``reasoning_effort="none"`` and accept a
``reasoning_effort`` knob that older chat models (gpt-4o family) do not. Every
call site used to hard-code ``temperature=0.1``; ``sampling_params`` below turns a
"desired temperature" into whatever the selected model actually accepts, so the
model can be swapped from the environment without touching processors.

Configuration (env vars, all optional)
--------------------------------------
HC_RAG_LLM_MODEL          default model for routing / preprocessing / retrieval-eval /
                          generation / follow-ups         (default: gpt-5.6-luna)
HC_RAG_VALIDATOR_MODEL    model for answer structuring+validation (default: gpt-5.6-terra)
HC_RAG_REASONING_EFFORT   reasoning effort for GPT-5.x models: none|low|medium|high
                          (default: none — keeps latency/cost comparable to gpt-4o-mini)
HC_RAG_DISABLE_STAGES     comma-separated pipeline stages to short-circuit for ablation
                          experiments: safety (the runtime safety gate), clarify, decompose,
                          evaluate (retrieval gap-fill), validate (citation validation),
                          followups. Disabled stages return a pass-through result and make no
                          LLM call. Default: none disabled.
HC_RAG_SAFETY_GATE        when true (default), every query goes through the safety gate
                          (healthcare_rag/processors/safety.py) before anything else: PHI is
                          scrubbed and personal-advice / emergency / out-of-scope / prompt-
                          injection messages get a templated response instead of a monograph
                          answer (journey findings F13, F18). Costs one extra LLM call (~1 s).
                          Set false — or put "safety" in HC_RAG_DISABLE_STAGES — to measure
                          the un-gated behaviour for an ablation.
HC_RAG_MAX_SUBQUERIES     hard cap on the number of sub-query branches one decomposition
                          may spawn; extra sub-queries are dropped (default: 3). Added
                          because gpt-5.6-luna emits up to 8 sub-queries and every branch
                          pays retrieve+evaluate (journey finding F07).
HC_RAG_DECOMPOSE_ONLY_COMPLEX
                          when true (default), only decompose when the decomposer labelled
                          the query `query_complexity == "complex"`. Set false to decompose
                          whenever 2+ sub-queries come back.
HC_RAG_RETRIEVER          which retrieval arm runs: weaviate (default) or pageindex. The
                          pageindex arm replaces only the per-collection search callable with
                          a PageIndex tree-search adapter (one LLM call picks tree nodes, the
                          selected page ranges map back onto the same contextualised chunks);
                          routing, merge and every downstream stage are unchanged.
HC_RAG_PAGEINDEX_MAX_NODES
                          cap on tree nodes the selection call may keep (default: 4).
HC_RAG_PAGEINDEX_MAX_CHUNKS
                          cap on chunks the selected page ranges expand to (default: 8).
HC_RAG_PAGEINDEX_DIR      directory holding pageindex_tree_*.json / chunks_*.json (default: data).

Model history
-------------
* Original repo: gpt-4o-mini everywhere, gpt-4o for validation.
* 2026-08: moved to gpt-5.6-luna / gpt-5.6-terra (see evals/results/ for the
  before/after comparison that justified the defaults).
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_LLM_MODEL = "gpt-5.6-luna"
DEFAULT_VALIDATOR_MODEL = "gpt-5.6-terra"
DEFAULT_REASONING_EFFORT = "none"

_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def default_llm_model() -> str:
    return os.getenv("HC_RAG_LLM_MODEL", DEFAULT_LLM_MODEL).strip() or DEFAULT_LLM_MODEL


def default_validator_model() -> str:
    return os.getenv("HC_RAG_VALIDATOR_MODEL", DEFAULT_VALIDATOR_MODEL).strip() or DEFAULT_VALIDATOR_MODEL


def default_reasoning_effort() -> str:
    return os.getenv("HC_RAG_REASONING_EFFORT", DEFAULT_REASONING_EFFORT).strip() or DEFAULT_REASONING_EFFORT


def is_reasoning_model(model: str) -> bool:
    m = model.lower()
    return m.startswith(_REASONING_PREFIXES)


def sampling_params(model: str, temperature: float | None = None, reasoning_effort: str | None = None) -> dict[str, Any]:
    """Return the kwargs to pass to ``chat.completions.create/parse`` for ``model``.

    * gpt-4o family (non-reasoning): ``{"temperature": temperature}``
    * gpt-5.x / o-series (reasoning): ``{"reasoning_effort": effort}`` and, only
      when effort == "none", also the temperature (verified 2026-08-18: GPT-5.6
      accepts temperature with reasoning_effort="none" and rejects it otherwise).
    """
    params: dict[str, Any] = {}
    if is_reasoning_model(model):
        effort = reasoning_effort or default_reasoning_effort()
        if effort == "none" and model.lower().startswith(("o1", "o3", "o4")):
            effort = "low"  # o-series has no "none" level
        params["reasoning_effort"] = effort
        if effort == "none" and temperature is not None:
            params["temperature"] = temperature
    elif temperature is not None:
        params["temperature"] = temperature
    return params


VALID_STAGES = {"safety", "clarify", "decompose", "evaluate", "validate", "followups"}


def disabled_stages() -> frozenset[str]:
    """Stages short-circuited via HC_RAG_DISABLE_STAGES (for ablation experiments)."""
    raw = os.getenv("HC_RAG_DISABLE_STAGES", "")
    stages = {s.strip().lower() for s in raw.split(",") if s.strip()}
    unknown = stages - VALID_STAGES
    if unknown:
        raise ValueError(f"HC_RAG_DISABLE_STAGES has unknown stage(s) {sorted(unknown)}; valid: {sorted(VALID_STAGES)}")
    return frozenset(stages)


def stage_enabled(name: str) -> bool:
    return name not in disabled_stages()


# --------------------------------------------------------------------------- #
# Decomposition settings                                                       #
# --------------------------------------------------------------------------- #

DEFAULT_MAX_SUBQUERIES = 3

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in _TRUTHY:
        return True
    if val in _FALSY:
        return False
    if val == "":
        return default
    raise ValueError(f"{name} must be a boolean (one of {sorted(_TRUTHY | _FALSY)}), got {raw!r}")


def max_subqueries() -> int:
    """Hard cap on how many sub-query branches a decomposition may spawn."""
    raw = os.getenv("HC_RAG_MAX_SUBQUERIES")
    if raw is None or not raw.strip():
        return DEFAULT_MAX_SUBQUERIES
    try:
        value = int(raw.strip())
    except ValueError:
        raise ValueError(f"HC_RAG_MAX_SUBQUERIES must be an integer, got {raw!r}") from None
    if value < 1:
        raise ValueError(f"HC_RAG_MAX_SUBQUERIES must be >= 1, got {value}")
    return value


def decompose_only_complex() -> bool:
    """True when decomposition only applies to queries the decomposer called 'complex'."""
    return _env_bool("HC_RAG_DECOMPOSE_ONLY_COMPLEX", True)


# --------------------------------------------------------------------------- #
# Safety gate                                                                  #
# --------------------------------------------------------------------------- #

def safety_gate_enabled() -> bool:
    """True when the runtime safety gate runs before the pipeline.

    Honours both knobs: the dedicated ``HC_RAG_SAFETY_GATE`` flag and the generic
    ablation switch ``HC_RAG_DISABLE_STAGES=safety``. Either one turns it off.
    """
    return _env_bool("HC_RAG_SAFETY_GATE", True) and stage_enabled("safety")


# --------------------------------------------------------------------------- #
# Retrieval backend (PageIndex A/B arm)                                        #
# --------------------------------------------------------------------------- #

DEFAULT_RETRIEVER = "weaviate"
VALID_RETRIEVERS = ("weaviate", "pageindex")

DEFAULT_PAGEINDEX_MAX_NODES = 4
DEFAULT_PAGEINDEX_MAX_CHUNKS = 8


def retriever_backend() -> str:
    """Which retrieval arm the graph uses: ``weaviate`` (default) or ``pageindex``.

    ``pageindex`` swaps *only* the per-collection search callable for the
    tree-search adapter in ``healthcare_rag/processors/pageindex_retrieval.py``;
    routing, merging and every downstream stage are untouched.
    """
    raw = os.getenv("HC_RAG_RETRIEVER")
    if raw is None or not raw.strip():
        return DEFAULT_RETRIEVER
    value = raw.strip().lower()
    if value not in VALID_RETRIEVERS:
        raise ValueError(
            f"HC_RAG_RETRIEVER must be one of {sorted(VALID_RETRIEVERS)}, got {raw!r}"
        )
    return value


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    return value


def pageindex_max_nodes() -> int:
    """Hard cap on how many PageIndex tree nodes one selection call may keep."""
    return _env_positive_int("HC_RAG_PAGEINDEX_MAX_NODES", DEFAULT_PAGEINDEX_MAX_NODES)


def pageindex_max_chunks() -> int:
    """Hard cap on how many chunks the selected page ranges may expand to."""
    return _env_positive_int("HC_RAG_PAGEINDEX_MAX_CHUNKS", DEFAULT_PAGEINDEX_MAX_CHUNKS)
