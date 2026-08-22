"""Run the code-sealed parity gate over explicit single- and multi-turn reports."""

from __future__ import annotations

import argparse
from pathlib import Path

from evals.parity import GateInputs, ParityGate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-json", required=True, type=Path)
    parser.add_argument("--candidate-json", required=True, type=Path)
    parser.add_argument("--mt-baseline-json", required=True, type=Path)
    parser.add_argument("--mt-candidate-json", required=True, type=Path)
    parser.add_argument("--code-sha", required=True, type=Path)
    parser.add_argument("--base-sha", required=True, type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    inputs = GateInputs(
        baseline=args.baseline_json,
        candidate=args.candidate_json,
        multiturn_baseline=args.mt_baseline_json,
        multiturn_candidate=args.mt_candidate_json,
        code_sha=args.code_sha,
        base_sha=args.base_sha,
    )
    gate = ParityGate(inputs)
    breaches = gate.run()
    if breaches:
        print(f"PARITY GATE FAIL ({len(breaches)} breach(es))")
        for breach in breaches:
            print(f"- {breach}")
        for note in gate.notes:
            print(f"note: {note}")
        return 1
    print("PARITY GATE PASS: code seal, provenance, populations, metadata, and metrics match")
    for note in gate.notes:
        print(f"note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
