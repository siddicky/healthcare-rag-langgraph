"""
Multi-turn eval harness: plays a whole conversation through the *real*
orchestrator and records per-turn telemetry.

Design
------
The application has no session object: state lives entirely in
``rag.conversation_history``, keyed by ``user_id``. So a "conversation" here is
simply **one fresh ``user_id``**, through which turns are played sequentially.
A new :class:`RefactoredOrchestrator` is constructed per turn — that is exactly
what the interactive CLI does, so the eval measures the shipped behaviour rather
than a special eval-only code path.

Two kinds of conversation:

* ``scripted`` — a fixed list of user utterances, replayed in order.
* ``simulated`` — an LLM plays the user (openevals
  ``run_multiturn_simulation_async`` + ``create_async_llm_simulated_user``). The
  ``app`` callback maps ``thread_id`` → ``user_id`` and calls the orchestrator,
  so the simulated user reacts to what the system actually said.

Per turn we capture the same fields ``evals.harness.run_one`` captures (answer,
retrieved contexts, branch info, latency, token usage, estimated cost) plus
``used_history``: whether the orchestrator's history-extraction stage decided the
previous turns were *required* to answer this one. That flag is read off
``orchestrator.summary_result.required_context`` after the call — no application
code is touched.

Nothing here changes application behaviour; it only observes it.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from healthcare_rag.graph.engine import Engine

load_dotenv()

logger = logging.getLogger("evals")

# The user simulator is a *plain chat* model call through langchain's
# ``init_chat_model``. openevals never passes ``temperature``, and
# ``ChatOpenAI.temperature`` defaults to None, so nothing is sent — which means
# the GPT-5.6 reasoning models are safe to use here (verified 2026-08-18: both
# "openai:gpt-5.6-luna" and "openai:gpt-4o-mini" invoke cleanly). We default to
# gpt-5.6-luna to stay in the same family as the system under test; override with
# EVAL_SIM_USER_MODEL if a future openevals release starts sending temperature.
SIM_USER_MODEL = os.getenv("EVAL_SIM_USER_MODEL", "openai:gpt-5.6-luna")

_ANSWER_PLACEHOLDER = ""


# --------------------------------------------------------------------------- #
# One turn                                                                     #
# --------------------------------------------------------------------------- #

async def run_turn(engine: Engine, user_id: str, question: str, index: int) -> dict[str, Any]:
    """Run a single user utterance for ``user_id`` and return a rich turn record.

    Mirrors :func:`evals.harness.run_one`, but keeps the caller's ``user_id`` so
    that ``rag.conversation_history`` accumulates across turns.
    """
    result = await engine.run_turn(user_id, question)
    return {
        "index": index,
        "user": question,
        "answer": result["answer"],
        "answered": result["answered"],
        "raw_answer": result["raw_answer"],
        "follow_ups": result["follow_ups"],
        "contexts": result["contexts"],
        "retrieved_chunk_ids": result["retrieved_chunk_ids"],
        "retrieved_pages": result["retrieved_pages"],
        "retrieved_sources": result["retrieved_sources"],
        "latency_s": result["latency_s"],
        "time_to_first_answer_s": result["time_to_first_answer_s"],
        "usage": result["usage"],
        "safety_outcome": result["safety_outcome"],
        "error": result["error"],
        "used_history": engine.history_used(user_id),
        "n_branches": result["n_branches"],
        "selected_branch_type": result["selected_branch_type"],
    }


# --------------------------------------------------------------------------- #
# Simulated-user stopping condition                                            #
# --------------------------------------------------------------------------- #

# The dataset writes ``stop_when`` as an instruction to the persona — "Say DONE
# once you have asked four out-of-scope questions" — so the primary stop signal is
# the persona emitting the sentinel. A ``stop_when`` that never mentions DONE is
# treated as a plain condition on the trajectory and judged by the LLM instead.
DONE_SENTINEL = "DONE"
_DONE_RE = re.compile(rf"\b{DONE_SENTINEL}\b")
_DONE_ONLY_RE = re.compile(rf"^\W*{DONE_SENTINEL}\W*$", re.IGNORECASE)


def _mentions_sentinel(stop_when: str) -> bool:
    return bool(_DONE_RE.search(stop_when))


def is_done_message(text: str) -> bool:
    """True if the simulated user signalled the end of the conversation."""
    return bool(_DONE_RE.search(text or ""))


def _is_done_only(text: str) -> bool:
    """True if the message is *nothing but* the sentinel — no question to answer."""
    return bool(_DONE_ONLY_RE.match((text or "").strip()))


class _StopVerdict(BaseModel):
    stop: bool = Field(description="True if the described stopping condition is now satisfied.")
    rationale: str = Field(description="One short sentence.")


def _last_user_message(trajectory: list[dict[str, Any]]) -> str:
    for m in reversed(trajectory):
        if m.get("role") == "user":
            return m.get("content") or ""
    return ""


def _make_stopping_condition(stop_when: str):
    """Turn a natural-language ``stop_when`` into an openevals stopping condition.

    Two conventions are supported:

    * ``stop_when`` mentions ``DONE`` — the persona has been told to emit the
      sentinel, so stopping is a free string check on the last user message.
    * anything else — the condition is judged against the trajectory by the shared
      eval judge model (see :mod:`evals.evaluators`), so the stop decision stays
      consistent with every other LLM check in the suite. Judge calls use the
      un-traced judge client and do not pollute the system-under-test's
      cost/latency numbers. Note this costs one judge call per turn, and it runs
      even under ``--no-judges`` (the conversation cannot be played without it).
    """
    if _mentions_sentinel(stop_when):

        async def _stop_on_sentinel(
            trajectory: list[dict[str, Any]], *, turn_counter: int, **_: Any
        ) -> bool:
            return is_done_message(_last_user_message(trajectory))

        return _stop_on_sentinel

    from .evaluators import _judge  # imported lazily: judges are optional

    async def _stop(
        trajectory: list[dict[str, Any]], *, turn_counter: int, **_: Any
    ) -> bool:
        convo = _render_trajectory(trajectory)
        try:
            v = await _judge(
                "You decide whether a simulated conversation should stop. Given the CONDITION and "
                "the CONVERSATION SO FAR, answer whether the condition is now satisfied. Be "
                "conservative: only stop when the condition is clearly met.",
                f"CONDITION:\n{stop_when}\n\nCONVERSATION SO FAR:\n{convo}",
                _StopVerdict,
            )
        except Exception as exc:  # never let the stop check kill the conversation
            logger.warning("stopping-condition judge failed (%s); continuing", exc)
            return False
        return _StopVerdict.model_validate(v.model_dump()).stop

    return _stop


def _render_trajectory(trajectory: list[dict[str, Any]]) -> str:
    return "\n".join(f"{m.get('role')}: {m.get('content') or ''}" for m in trajectory)


# --------------------------------------------------------------------------- #
# One conversation                                                             #
# --------------------------------------------------------------------------- #

def _totals(turns: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "latency_s": round(sum(t["latency_s"] for t in turns), 3),
        "est_cost_usd": round(sum((t["usage"] or {}).get("est_cost_usd") or 0.0 for t in turns), 6),
        "total_tokens": sum((t["usage"] or {}).get("total_tokens") or 0 for t in turns),
        "llm_calls": sum((t["usage"] or {}).get("llm_calls") or 0 for t in turns),
    }


def _result(
    conversation_id: str,
    kind: str,
    turns: list[dict[str, Any]],
    trajectory: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "conversation_id": conversation_id,
        "kind": kind,
        "turns": turns,
        "trajectory": trajectory,
        "totals": _totals(turns),
        "per_turn_latency": [t["latency_s"] for t in turns],
        "per_turn_cost": [round((t["usage"] or {}).get("est_cost_usd") or 0.0, 6) for t in turns],
        "turns_completed": len(turns),
    }


async def _run_scripted(engine: Engine, conv: dict[str, Any], user_id: str) -> dict[str, Any]:
    turns: list[dict[str, Any]] = []
    trajectory: list[dict[str, Any]] = []
    for i, text in enumerate(conv.get("turns") or [], start=1):
        turn = await run_turn(engine, user_id, text, i)
        turns.append(turn)
        trajectory.append({"role": "user", "content": text})
        trajectory.append({"role": "assistant", "content": turn["answer"] or _ANSWER_PLACEHOLDER})
    return _result(conv["conversation_id"], "scripted", turns, trajectory)


async def _run_simulated(engine: Engine, conv: dict[str, Any], user_id: str) -> dict[str, Any]:
    from openevals.simulators import create_async_llm_simulated_user, run_multiturn_simulation_async

    spec = conv.get("simulated_user") or {}
    max_turns = int(spec.get("max_turns") or 6)
    stop_when = (spec.get("stop_when") or "").strip()
    opening = (spec.get("opening") or "").strip()

    # thread_id → user_id. The simulator owns thread ids; the application keys its
    # memory by user_id, so this dict is the bridge between the two namespaces.
    threads: dict[str, str] = {}
    turns: list[dict[str, Any]] = []

    async def app(
        inputs: dict[str, Any], *, thread_id: str, **_: Any
    ) -> dict[str, Any]:
        uid = threads.setdefault(thread_id, user_id)
        text = inputs.get("content") or ""
        # A bare "DONE" is the persona signalling the end, not a question. The
        # stopping condition fires immediately after this, so short-circuit rather
        # than spending a full pipeline turn (and a metric row) answering it.
        if _is_done_only(text):
            return {"role": "assistant", "content": "(simulated user ended the conversation)"}
        turn = await run_turn(engine, uid, text, len(turns) + 1)
        turns.append(turn)
        return {"role": "assistant", "content": turn["answer"] or _ANSWER_PLACEHOLDER}

    system = spec.get("system") or "You are a patient asking about your medication."
    if stop_when:
        system = f"{system}\n\nEnd the conversation when this is true: {stop_when}"

    user = create_async_llm_simulated_user(
        system=system,
        model=SIM_USER_MODEL,
        # Turn 1 is scripted so every run of this conversation starts identically;
        # turns 2..N are generated by the persona.
        fixed_responses=[opening] if opening else None,
    )

    result = await run_multiturn_simulation_async(
        app=app,
        user=user,
        max_turns=max_turns,
        stopping_condition=_make_stopping_condition(stop_when) if stop_when else None,
        thread_id=user_id,
    )
    trajectory = [
        {"role": m.get("role"), "content": m.get("content") or ""} for m in (result.get("trajectory") or [])
    ]
    return _result(conv["conversation_id"], "simulated", turns, trajectory)


async def run_conversation(engine: Engine, conv: dict[str, Any]) -> dict[str, Any]:
    """Play one conversation end-to-end and return its record.

    Args:
        engine: the selected runtime engine shared by every turn.
        conv: the LangSmith example ``inputs`` — ``{conversation_id, kind, turns |
            simulated_user}``.

    Returns:
        ``{conversation_id, kind, turns, trajectory, totals, per_turn_latency,
        per_turn_cost, turns_completed}``. ``turns`` holds one record per turn (see
        :func:`run_turn`).
    """
    # One fresh user id per conversation → the history store starts empty and is
    # never shared with another conversation, even at concurrency > 1.
    user_id = f"eval_mt_{uuid.uuid4().hex[:10]}"
    kind = conv.get("kind") or "scripted"
    if kind == "simulated":
        return await _run_simulated(engine, conv, user_id)
    return await _run_scripted(engine, conv, user_id)


def make_target(engine: Engine):
    """Return the async target callable expected by ``langsmith.aevaluate``."""

    async def target(inputs: dict[str, Any]) -> dict[str, Any]:
        return await run_conversation(engine, inputs)

    return target


__all__ = [
    "SIM_USER_MODEL",
    "make_target",
    "run_conversation",
    "run_turn",
]
