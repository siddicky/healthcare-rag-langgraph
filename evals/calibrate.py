"""
Calibrate the evaluators themselves: run every evaluator on hand-labelled cases
in evals/judge_calibration.json and check the scores land where a human said
they should.

    uv run python -m evals.calibrate                # all evaluators (needs OPENAI_API_KEY for judges)
    uv run python -m evals.calibrate --no-judges    # deterministic only, offline, fast
    uv run python -m evals.calibrate --json         # machine-readable

Each case has an `expect` block: `{metric: exact_value | [lo, hi] | null}`.
`null` means "must be n/a (score None)". Exit code 1 if any expectation fails,
so this doubles as a test (see tests/test_evaluators.py, which runs the
deterministic subset offline; the judge subset is an integration test).

Why this exists: an eval suite is only as trustworthy as its graders. This file
pins the tricky cases — "refuses but still gives the dose", PII echo, false
premise refuted vs accepted, wrong number in a fluent answer — so a judge-model
swap or prompt edit that regresses them is caught before it silently reshapes
every experiment's numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dotenv import load_dotenv

from . import evaluators as ev

load_dotenv()

CALIBRATION_PATH = Path(__file__).parent / "judge_calibration.json"


def load_cases(path: Path = CALIBRATION_PATH) -> list[dict]:
    return json.loads(path.read_text())


def _fake_example(case: dict) -> SimpleNamespace:
    """Evaluators that read example.metadata (category) get a stand-in Example."""
    return SimpleNamespace(metadata={"category": case["reference"].get("category")}, inputs=case["inputs"], outputs=case["reference"])


async def score_case(case: dict, use_judges: bool) -> dict[str, Any]:
    """Run all (or deterministic) evaluators on one case → {metric: score}."""
    fns = ev.ALL_EVALUATORS if use_judges else ev.DETERMINISTIC_EVALUATORS
    scores: dict[str, Any] = {}
    example = _fake_example(case)
    for fn in fns:
        kwargs = {"inputs": case["inputs"], "outputs": case["outputs"], "reference_outputs": case["reference"]}
        if "example" in inspect.signature(fn).parameters:
            kwargs["example"] = example
        res = fn(**kwargs)
        if inspect.isawaitable(res):
            res = await res
        for item in res if isinstance(res, list) else [res]:
            scores[item["key"]] = item.get("score")
    return scores


def check(expect: dict[str, Any], scores: dict[str, Any]) -> list[str]:
    """Return a list of human-readable failures for one case."""
    failures = []
    for metric, want in expect.items():
        if metric not in scores:
            failures.append(f"{metric}: not produced (expected {want})")
            continue
        got = scores[metric]
        if want is None:
            if got is not None:
                failures.append(f"{metric}: expected n/a, got {got}")
        elif isinstance(want, list):
            lo, hi = want
            if got is None or not (lo <= got <= hi):
                failures.append(f"{metric}: expected in [{lo}, {hi}], got {got}")
        else:
            if got is None or abs(float(got) - float(want)) > 1e-9:
                failures.append(f"{metric}: expected {want}, got {got}")
    return failures


async def run(use_judges: bool, only_metrics: set[str] | None = None) -> tuple[list[dict], int]:
    cases = load_cases()
    results = []
    n_fail = 0
    for case in cases:
        expect = case["expect"]
        if not use_judges:
            # keep only expectations that deterministic evaluators can produce
            det_keys = {k for f in ev.DETERMINISTIC_EVALUATORS for k in ev.EVALUATOR_KEYS.get(f.__name__, [])}
            expect = {k: v for k, v in expect.items() if k in det_keys}
        if only_metrics:
            expect = {k: v for k, v in expect.items() if k in only_metrics}
        if not expect:
            continue
        scores = await score_case(case, use_judges)
        failures = check(expect, scores)
        n_fail += bool(failures)
        results.append({"id": case["id"], "why": case["why"], "expect": expect,
                        "got": {k: scores.get(k) for k in expect}, "failures": failures})
    return results, n_fail


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-judges", action="store_true")
    ap.add_argument("--metric", action="append", default=None, help="only check these metrics")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results, n_fail = await run(use_judges=not args.no_judges, only_metrics=set(args.metric) if args.metric else None)
    if args.json:
        print(json.dumps({"judge_model": None if args.no_judges else ev.JUDGE_MODEL, "results": results}, indent=2))
    else:
        print(f"judge model: {'(deterministic only)' if args.no_judges else ev.JUDGE_MODEL}\n")
        for r in results:
            status = "PASS" if not r["failures"] else "FAIL"
            print(f"[{status}] {r['id']}  got={r['got']}")
            for f in r["failures"]:
                print(f"       ✗ {f}")
        print(f"\n{len(results) - n_fail}/{len(results)} cases passed")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
