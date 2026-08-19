from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal, TypeAlias, assert_never

import pytest

from evals.parity_drills import (
    BASE_SHA_FILE,
    CANDIDATE,
    CODE_SHA_FILE,
    GATE,
    MEASUREMENT_SOURCES,
    MT_CANDIDATE,
    MULTITURN_BASELINE,
    SINGLE_BASELINE,
    SyntheticReport,
    git,
    write_json,
)

pytest_plugins = ("evals.parity_drills",)
Defect: TypeAlias = Literal["breach", "missing", "duplicate", "wrong_sha", "turns", "nonfinite"]


def _run_gate(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(GATE),
            "--baseline-json",
            str(SINGLE_BASELINE),
            "--candidate-json",
            str(CANDIDATE),
            "--mt-baseline-json",
            str(MULTITURN_BASELINE),
            "--mt-candidate-json",
            str(MT_CANDIDATE),
            "--code-sha",
            str(CODE_SHA_FILE),
            "--base-sha",
            str(BASE_SHA_FILE),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GIT_MASTER": "1"},
    )


def test_positive_control_passes(
    sealed_reports: tuple[Path, SyntheticReport, SyntheticReport],
) -> None:
    repo, _, _ = sealed_reports
    result = _run_gate(repo)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


@pytest.mark.parametrize(
    ("defect", "reason"),
    [
        ("breach", "correctness"),
        ("missing", "missing"),
        ("duplicate", "example-ID multiset"),
        ("wrong_sha", "code SHA"),
        ("turns", "turns_completed"),
        ("nonfinite", "non-finite"),
    ],
)
def test_negative_drill_fails(
    sealed_reports: tuple[Path, SyntheticReport, SyntheticReport],
    defect: Defect,
    reason: str,
) -> None:
    repo, _, _ = sealed_reports
    candidate = json.loads((repo / CANDIDATE).read_text(encoding="utf-8"))
    mt_candidate = json.loads((repo / MT_CANDIDATE).read_text(encoding="utf-8"))
    match defect:
        case "breach":
            candidate["aggregate"]["overall"]["correctness"] = 0.0
        case "missing":
            del candidate["aggregate"]["overall"]["groundedness"]
        case "duplicate":
            candidate["rows"][1]["example_id"] = "a"
        case "wrong_sha":
            candidate["metadata"]["git_sha"] = "HEAD~1"
        case "turns":
            mt_candidate["rows"][1]["outputs"]["turns"] = mt_candidate["rows"][1][
                "outputs"
            ]["turns"][:1]
            mt_candidate["aggregate"]["overall"]["turns_completed"] = 4.0
        case "nonfinite":
            candidate["aggregate"]["overall"]["correctness"] = float("nan")
        case unreachable:
            assert_never(unreachable)
    write_json(repo / CANDIDATE, candidate)
    write_json(repo / MT_CANDIDATE, mt_candidate)
    result = _run_gate(repo)
    print(result.stdout)
    assert result.returncode == 1
    assert reason in result.stdout


def test_scripted_turn_truncation_fails(
    sealed_reports: tuple[Path, SyntheticReport, SyntheticReport],
) -> None:
    repo, _, _ = sealed_reports
    candidate = json.loads((repo / MT_CANDIDATE).read_text(encoding="utf-8"))
    candidate["rows"][0]["outputs"]["turns"].pop()
    write_json(repo / MT_CANDIDATE, candidate)
    result = _run_gate(repo)
    print(result.stdout)
    assert result.returncode == 1
    assert "scripted turn exposure" in result.stdout


@pytest.mark.parametrize("source", (MEASUREMENT_SOURCES[0], MEASUREMENT_SOURCES[2]))
def test_measurement_source_mutation_fails(
    sealed_reports: tuple[Path, SyntheticReport, SyntheticReport], source: Path
) -> None:
    repo, _, _ = sealed_reports
    (repo / source).write_text("mutated\n", encoding="utf-8")
    result = _run_gate(repo)
    print(result.stdout)
    assert result.returncode == 1
    assert str(source) in result.stdout


def test_committed_baseline_tamper_fails(
    sealed_reports: tuple[Path, SyntheticReport, SyntheticReport],
) -> None:
    repo, _, _ = sealed_reports
    baseline = json.loads((repo / SINGLE_BASELINE).read_text(encoding="utf-8"))
    baseline["aggregate"]["overall"]["correctness"] = 1.0
    write_json(repo / SINGLE_BASELINE, baseline)
    git(repo, "add", str(SINGLE_BASELINE))
    git(repo, "commit", "-m", "tamper baseline")
    code_sha = git(repo, "rev-parse", "HEAD")
    (repo / CODE_SHA_FILE).write_text(code_sha, encoding="utf-8")
    for path in (repo / CANDIDATE, repo / MT_CANDIDATE):
        candidate = json.loads(path.read_text(encoding="utf-8"))
        candidate["metadata"]["git_sha"] = code_sha
        write_json(path, candidate)
    result = _run_gate(repo)
    print(result.stdout)
    assert result.returncode == 1
    assert "baseline provenance" in result.stdout
