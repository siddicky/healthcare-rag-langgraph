"""
Regression tests for the eval graders themselves (see evals/calibrate.py).

* Deterministic evaluators are checked offline against evals/judge_calibration.json.
* LLM-judge expectations run only when OPENAI_API_KEY is set (`pytest -m judge`).
"""
from __future__ import annotations

import asyncio
import os

import pytest

from evals import calibrate
from evals import evaluators as ev


def test_deterministic_evaluators_match_calibration():
    results, n_fail = asyncio.run(calibrate.run(use_judges=False))
    assert results, "no deterministic expectations found"
    assert n_fail == 0, "\n".join(f"{r['id']}: {r['failures']}" for r in results if r["failures"])


def test_every_evaluator_declares_its_keys():
    for fn in ev.ALL_EVALUATORS:
        assert fn.__name__ in ev.EVALUATOR_KEYS, f"{fn.__name__} missing from EVALUATOR_KEYS"


@pytest.mark.judge
@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="needs OPENAI_API_KEY (integration)")
def test_llm_judges_match_calibration():
    results, n_fail = asyncio.run(calibrate.run(use_judges=True))
    assert n_fail == 0, "\n".join(f"{r['id']}: {r['failures']}" for r in results if r["failures"])
