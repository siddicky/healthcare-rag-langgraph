"""
OpenAI list prices used for *local* cost estimates (USD per 1M tokens).

LangSmith computes its own cost from token usage with its up-to-date pricing
table; the numbers here are a fallback so the local JSON/Markdown report has a
cost column even when LangSmith is offline. Keep both in the report and treat
LangSmith as the source of truth when they disagree.

GPT-5.6 prices were read from https://developers.openai.com/api/docs/models on
2026-08-18. The gpt-4o / 4.1 / o3 rows are legacy list prices as last known to
the author (early 2026) and were not re-verified — they only matter for the
"before" side of the migration comparison. Update PRICING_AS_OF when you change
a number.
"""

from __future__ import annotations

PRICING_AS_OF = "2026-08-18 (gpt-5.6 verified; legacy 4o rows unverified)"

# model -> (input $/1M, output $/1M)
PRICING_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-sol": (5.00, 30.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "o3-mini": (1.10, 4.40),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
    "text-embedding-ada-002": (0.10, 0.0),
}

# Cached input tokens are billed at a discount for the 4o family.
CACHED_INPUT_DISCOUNT: dict[str, float] = {
    "gpt-5.6-luna": 0.1,   # $0.02 cached vs $0.20
    "gpt-5.6-terra": 0.1,  # $0.20 cached vs $2.00
    "gpt-5.6-sol": 0.1,    # assumed same 10% ratio (not shown on the page)
    "gpt-4o-mini": 0.5,
    "gpt-4o": 0.5,
    "gpt-4.1": 0.25,
    "gpt-4.1-mini": 0.25,
    "gpt-4.1-nano": 0.25,
}


def _base_model(model: str) -> str:
    """Strip dated suffixes like 'gpt-4o-mini-2024-07-18' -> 'gpt-4o-mini'."""
    for known in sorted(PRICING_PER_1M, key=len, reverse=True):
        if model == known or model.startswith(known + "-"):
            return known
    return model


def estimate_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_prompt_tokens: int = 0,
) -> float | None:
    """Return estimated USD cost for one call, or None if the model is unknown."""
    base = _base_model(model)
    if base not in PRICING_PER_1M:
        return None
    in_price, out_price = PRICING_PER_1M[base]
    uncached = max(prompt_tokens - cached_prompt_tokens, 0)
    discount = CACHED_INPUT_DISCOUNT.get(base, 1.0)
    cost = (
        uncached * in_price
        + cached_prompt_tokens * in_price * discount
        + completion_tokens * out_price
    ) / 1_000_000
    return cost
