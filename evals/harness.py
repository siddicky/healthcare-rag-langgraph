"""
Eval harness: exposes a LangSmith-compatible ``target`` coroutine that runs one
example through the *real* graph engine and returns everything the evaluators
need (history seeding, thread ids).

Nothing here changes application behaviour; it only observes it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Optional

from dotenv import load_dotenv

if TYPE_CHECKING:
    from healthcare_rag.graph.engine import Engine

load_dotenv()

logger = logging.getLogger("evals")


async def run_one(
    engine: Engine,
    question: str,
    history: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Run one question through the engine with the legacy result contract."""
    user_id = f"eval_{uuid.uuid4().hex[:10]}"
    await engine.seed_history(
        user_id,
        [
            {
                "user_query": turn["question"],
                "answer": turn["answer"],
            }
            for turn in history or []
        ],
    )
    return await engine.run_turn(user_id, question)


def make_target(
    engine: Engine,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return the async target callable expected by ``langsmith.aevaluate``."""

    async def target(inputs: dict[str, Any]) -> dict[str, Any]:
        return await run_one(engine, inputs["question"], inputs.get("history") or [])

    return target
