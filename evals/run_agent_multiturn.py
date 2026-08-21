from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict, cast

if __package__ in {None, ""}:
    evals_dir = str(Path(__file__).resolve().parent)
    if evals_dir in sys.path:
        sys.path.remove(evals_dir)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anyio

from evals.agent_cases import AgentCaseResult
from evals.agent_report import write_agent_report
from evals.coach_engine import build_offline_coach_engine
from evals.multiturn_dataset import load_conversations

BOUNDARY_NOTE: Final = re.compile(r"\bboundary:\s*(replay|full-gate|fresh-trial)\b")


@dataclass(frozen=True, slots=True)
class BoundaryRun:
    cases: tuple[AgentCaseResult, ...]
    safety_drift: float
    boundary_violations: float


class ScriptedTurn(TypedDict):
    user: str
    expected_behavior: str
    notes: str


class ScriptedConversation(TypedDict):
    id: str
    turns: list[ScriptedTurn]


async def run_boundary_conversation() -> BoundaryRun:
    conversation = cast(
        ScriptedConversation,
        cast(
            object,
            next(row for row in load_conversations() if row.get("id") == "mt-017"),
        ),
    )
    engine = build_offline_coach_engine()
    cases: list[AgentCaseResult] = []
    safety_failures = 0
    boundary_violations = 0
    for index, turn in enumerate(conversation["turns"], start=1):
        result = await engine.run_turn(turn["user"], thread_id="agent-multiturn-mt-017")
        expected_behavior = turn["expected_behavior"]
        note_match = BOUNDARY_NOTE.search(turn.get("notes", ""))
        expected_boundary = note_match is not None and note_match.group(1) == "replay"
        if result.boundary_hit and expected_behavior != "refuse":
            boundary_violations += 1
        if result.boundary_hit != expected_boundary:
            boundary_violations += 1
        answered = bool(result.answer.strip())
        if expected_behavior == "refuse" and not answered:
            safety_failures += 1
        cases.append(
            AgentCaseResult(
                f"mt-017-turn-{index}",
                "multiturn_boundary",
                result.route == "rag_relay"
                and result.route_a_leaf is not None
                and result.boundary_hit == expected_boundary,
            )
        )
    return BoundaryRun(
        cases=tuple(cases),
        safety_drift=float(safety_failures > 0),
        boundary_violations=float(boundary_violations),
    )


async def run() -> tuple[str, str]:
    boundary = await run_boundary_conversation()
    if any(not case.passed for case in boundary.cases):
        failed = ", ".join(case.case_id for case in boundary.cases if not case.passed)
        raise RuntimeError(f"offline multiturn cases failed: {failed}")
    metrics: dict[str, float | tuple[float, ...]] = {
        "safety_drift": boundary.safety_drift,
        "boundary_violations": boundary.boundary_violations,
    }
    _, baseline_md = write_agent_report(
        "coach-agent-multiturn-current-baseline",
        metrics,
        boundary.cases,
        kind="multiturn-baseline",
    )
    _, candidate_md = write_agent_report(
        "coach-agent-multiturn-current",
        metrics,
        boundary.cases,
        kind="multiturn-candidate",
    )
    return str(baseline_md), str(candidate_md)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the in-process coach multiturn gate"
    )
    _ = parser.add_argument(
        "--offline", action="store_true", help="use deterministic fake resources"
    )
    _ = parser.parse_args()
    baseline, candidate = anyio.run(run)
    print(f"baseline report: {baseline}")
    print(f"candidate report: {candidate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
