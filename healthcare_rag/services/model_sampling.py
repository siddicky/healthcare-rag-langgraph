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
