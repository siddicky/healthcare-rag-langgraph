"""Per-turn LLM usage accumulation for the graph runtime."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult


class UsageRecorder(AsyncCallbackHandler):
    """Mutable per-run callback accumulator; one instance belongs to one turn."""

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self._started: dict[UUID, tuple[float, str]] = {}

    async def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        del prompts, parent_run_id, tags, kwargs
        model = str(
            (metadata or {}).get("ls_model_name") or serialized.get("name") or "?"
        )
        self._started[run_id] = (time.perf_counter(), model)

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        del parent_run_id, tags, kwargs
        from evals.usage import LLMCallUsage

        started, model_hint = self._started.pop(run_id, (time.perf_counter(), "?"))
        output = response.llm_output or {}
        model = str(output.get("model_name") or output.get("model") or model_hint)
        usage: dict[str, Any] = {}
        if response.generations and response.generations[0]:
            message = getattr(response.generations[0][0], "message", None)
            usage = dict(getattr(message, "usage_metadata", None) or {})
        if not usage:
            usage = dict(output.get("token_usage") or {})
        details = usage.get("input_token_details") or {}
        self.calls.append(
            LLMCallUsage(
                model=model,
                prompt_tokens=int(
                    usage.get("input_tokens") or usage.get("prompt_tokens") or 0
                ),
                completion_tokens=int(
                    usage.get("output_tokens") or usage.get("completion_tokens") or 0
                ),
                cached_prompt_tokens=int(details.get("cache_read") or 0),
                latency_s=time.perf_counter() - started,
                kind="create",
            )
        )
