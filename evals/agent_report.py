from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from pydantic import JsonValue

from .agent_cases import AgentCaseResult

RESULTS_DIR: Final = Path(__file__).parent / "results"


def write_agent_report(
    stem: str,
    metrics: Mapping[str, float | tuple[float, ...]],
    cases: tuple[AgentCaseResult, ...],
    *,
    kind: str,
) -> tuple[Path, Path]:
    _ = RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload: dict[str, JsonValue] = {
        "metadata": {
            "engine": "coach-in-process",
            "mode": "offline",
            "kind": kind,
            "judge_runs": 3,
            "judge_seeds": [17, 29, 43],
            "deployed_judging": "real gateway",
            "baseline_scope": "current checkout",
        },
        "metrics": {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in metrics.items()
        },
        "aggregate": {
            "overall": {
                key: sorted(value)[1] if isinstance(value, tuple) else value
                for key, value in metrics.items()
            }
        },
        "rows": [
            {"case_id": case.case_id, "tag": case.tag, "passed": case.passed}
            for case in cases
        ],
    }
    json_path = RESULTS_DIR / f"{stem}.json"
    md_path = RESULTS_DIR / f"{stem}.md"
    _ = json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    overall = payload["aggregate"]
    assert isinstance(overall, dict)
    values = overall["overall"]
    assert isinstance(values, dict)
    metric_lines = [f"| {key} | {values[key]} |" for key in sorted(metrics)]
    case_lines = [
        f"| {case.case_id} | {case.tag} | {'PASS' if case.passed else 'FAIL'} |"
        for case in cases
    ]
    _ = md_path.write_text(
        "\n".join(
            (
                f"# {stem}",
                "",
                "Current-checkout offline CoachEngine report; no historical numbers are used.",
                "Fake judges run deterministic seeds 17/29/43; deployed judging reuses the real gateway.",
                "",
                "## Overall",
                "",
                "| Metric | Value |",
                "|---|---:|",
                *metric_lines,
                "",
                "## Tagged cases",
                "",
                "| Case | Tag | Result |",
                "|---|---|---|",
                *case_lines,
                "",
            )
        ),
        encoding="utf-8",
    )
    return json_path, md_path


__all__ = ["write_agent_report"]
