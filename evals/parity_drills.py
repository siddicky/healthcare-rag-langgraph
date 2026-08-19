from __future__ import annotations

import copy
import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict

import pytest

from evals.parity import JSONValue

ROOT = Path(__file__).parents[1]
GATE = ROOT / "scripts" / "parity_gate.py"
SINGLE_BASELINE = Path("evals/results/safety-luna-terra-e9214cbf.json")
MULTITURN_BASELINE = Path("evals/results/multiturn-safety-853f353d.json")
MEASUREMENT_SOURCES = (
    Path("evals/golden_dataset.json"),
    Path("evals/multiturn_dataset.json"),
    Path("evals/evaluators.py"),
    Path("evals/multiturn_evaluators.py"),
    Path("evals/pricing.py"),
)
CANDIDATE = Path("evals/results/candidate.json")
MT_CANDIDATE = Path("evals/results/mt-candidate.json")
CODE_SHA_FILE = Path(".omo/code-sha.txt")
BASE_SHA_FILE = Path(".omo/base-sha.txt")


class SyntheticReport(TypedDict):
    metadata: dict[str, JSONValue]
    aggregate: dict[str, JSONValue]
    rows: list[dict[str, JSONValue]]


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_MASTER": "1"}
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def single_report() -> SyntheticReport:
    metrics: dict[str, JSONValue] = {
        "correctness": 0.8,
        "groundedness": 0.9,
        "behavior_match": 0.85,
        "safe_redirect": 0.7,
        "chunk_recall": 0.65,
        "must_mention_recall": 0.6,
        "hallucinated": 0.3,
        "forbidden_content": 0.01,
        "numeric_advice_leak": 0.04,
        "pipeline_error": 0.0,
        "est_cost_usd": 0.02,
        "latency_p50_s": 12.0,
    }
    return {
        "metadata": {
            "git_sha": "unused",
            "git_dirty": False,
            "engine": "legacy",
            "safety": True,
            "max_subqueries": 3,
            "decompose_only_complex": True,
            "structured_strict": False,
            "llm_model": "gpt-5.6-luna",
            "validator_model": "gpt-5.6-terra",
            "judge_model": "gpt-5.6-sol",
            "reasoning_effort": "none",
            "disabled_stages": None,
            "concurrency": 3,
            "pricing_as_of": "2026-08-18",
            "n_examples": 2,
            "chunk_file_hashes": {"chunks.json": "abc"},
            "judge_usage": {"reasoning_effort": "medium"},
        },
        "aggregate": {
            "overall": {"n": 2, **metrics},
            "by_split": {"core": {"n": 2, **metrics}},
        },
        "rows": [
            {"example_id": "a", "split": "core", "category": "factual",
             "outputs": {"answer": "baseline answer a"}, "feedback": {}},
            {"example_id": "b", "split": "core", "category": "safety",
             "outputs": {"answer": "baseline answer b"}, "feedback": {}},
            {"example_id": "c", "split": "core", "category": "factual",
             "outputs": {"answer": None}, "feedback": {}},
        ],
    }


def multiturn_report() -> SyntheticReport:
    return {
        "metadata": {
            "git_sha": "unused",
            "git_dirty": False,
            "engine": "legacy",
            "safety": True,
            "max_subqueries": 3,
            "decompose_only_complex": True,
            "structured_strict": False,
            "llm_model": "gpt-5.6-luna",
            "validator_model": "gpt-5.6-terra",
            "judge_model": "gpt-5.6-sol",
            "sim_user_model": "openai:gpt-5.6-luna",
            "reasoning_effort": "none",
            "disabled_stages": None,
            "concurrency": 3,
            "pricing_as_of": "2026-08-18",
            "n_conversations": 2,
        },
        "aggregate": {
            "overall": {
                "n": 2,
                "safety_drift": 0.35,
                "pii_persistence": 0.15,
                "self_contradiction": 0.2,
                "escalated_red_flags": 1.0,
                "context_carryover": 0.85,
                "consistency": 0.8,
                "turn_answered_rate": 0.98,
                "pipeline_error_any": 0.0,
                "cost_per_turn_usd": 0.02,
                "turns_completed": 5.0,
            }
        },
        "rows": [
            {
                "example_id": "mt-a",
                "split": "core",
                "category": "carryover",
                "kind": "scripted",
                "n_turns_expected": 4,
                "outputs": {"turns": [{"index": index} for index in range(4)]},
            },
            {
                "example_id": "mt-b",
                "split": "core",
                "category": "safety",
                "kind": "simulated",
                "n_turns_expected": 6,
                "outputs": {"turns": [{"index": index} for index in range(6)]},
            },
        ],
    }


def write_json(path: Path, value: SyntheticReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def sealed_reports(tmp_path: Path) -> Iterator[tuple[Path, SyntheticReport, SyntheticReport]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "parity@example.com")
    git(repo, "config", "user.name", "Parity Drill")
    single = single_report()
    multiturn = multiturn_report()
    write_json(repo / SINGLE_BASELINE, single)
    write_json(repo / MULTITURN_BASELINE, multiturn)
    for source in MEASUREMENT_SOURCES:
        path = repo / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"sealed:{source}\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "baseline")
    base_sha = git(repo, "rev-parse", "HEAD")
    (repo / BASE_SHA_FILE).parent.mkdir(parents=True)
    (repo / BASE_SHA_FILE).write_text(base_sha, encoding="utf-8")
    code_sha = git(repo, "rev-parse", "HEAD")
    (repo / CODE_SHA_FILE).write_text(code_sha, encoding="utf-8")
    for report, name in ((single, CANDIDATE), (multiturn, MT_CANDIDATE)):
        candidate = copy.deepcopy(report)
        candidate["metadata"].update(
            engine="graph",
            git_sha=code_sha,
            git_dirty=False,
            safety=True,
            max_subqueries=3,
            decompose_only_complex=True,
            structured_strict=False,
        )
        write_json(repo / name, candidate)
    yield repo, single, multiturn
