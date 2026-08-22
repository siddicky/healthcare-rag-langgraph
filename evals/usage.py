"""Shared usage record compatible across legacy and graph eval engines."""

from __future__ import annotations

import dataclasses
from typing import Any, Optional

from .pricing import estimate_cost_usd


@dataclasses.dataclass
class LLMCallUsage:
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int
    latency_s: float
    kind: str

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost_usd(self) -> Optional[float]:
        return estimate_cost_usd(
            self.model,
            self.prompt_tokens,
            self.completion_tokens,
            self.cached_prompt_tokens,
        )


def summarize_usage(calls: list[LLMCallUsage]) -> dict[str, Any]:
    by_model: dict[str, dict[str, Any]] = {}
    for call in calls:
        model = by_model.setdefault(
            call.model,
            {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0},
        )
        model["calls"] += 1
        model["prompt_tokens"] += call.prompt_tokens
        model["completion_tokens"] += call.completion_tokens
        model["cost_usd"] += call.cost_usd or 0.0
    return {
        "llm_calls": len(calls),
        "prompt_tokens": sum(call.prompt_tokens for call in calls),
        "completion_tokens": sum(call.completion_tokens for call in calls),
        "total_tokens": sum(call.total_tokens for call in calls),
        "cached_prompt_tokens": sum(call.cached_prompt_tokens for call in calls),
        "est_cost_usd": round(sum(value["cost_usd"] for value in by_model.values()), 6),
        "by_model": {
            key: {**value, "cost_usd": round(value["cost_usd"], 6)}
            for key, value in by_model.items()
        },
    }
