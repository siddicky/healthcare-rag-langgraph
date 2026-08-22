from __future__ import annotations

import argparse
from pathlib import Path
from typing import NamedTuple, cast

from evals.agent_parity import compare_reports


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check current CoachEngine eval parity"
    )
    _ = parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("evals/results/coach-agent-current-baseline.json"),
    )
    _ = parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("evals/results/coach-agent-current.json"),
    )
    _ = parser.add_argument(
        "--multiturn-baseline",
        type=Path,
        default=Path("evals/results/coach-agent-multiturn-current-baseline.json"),
    )
    _ = parser.add_argument(
        "--multiturn-candidate",
        type=Path,
        default=Path("evals/results/coach-agent-multiturn-current.json"),
    )
    return parser


class ParityPaths(NamedTuple):
    baseline: Path
    candidate: Path
    multiturn_baseline: Path
    multiturn_candidate: Path


def _paths() -> ParityPaths:
    args = vars(_parser().parse_args())
    return ParityPaths(
        baseline=cast(Path, args["baseline"]),
        candidate=cast(Path, args["candidate"]),
        multiturn_baseline=cast(Path, args["multiturn_baseline"]),
        multiturn_candidate=cast(Path, args["multiturn_candidate"]),
    )


def main() -> int:
    args = _paths()
    single = compare_reports(args.baseline, args.candidate)
    multiturn = compare_reports(
        args.multiturn_baseline,
        args.multiturn_candidate,
        required_metrics=("safety_drift", "boundary_violations"),
    )
    failures = (*single.failures, *multiturn.failures)
    if failures:
        for failure in failures:
            print(f"PARITY FAIL: {failure}")
        return 1
    print("PARITY PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
