from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from evals.agent_cases import run_agent_cases
from evals.agent_chunks import ChunkCatalog, ChunkMappingError
from evals.agent_parity import compare_reports
from evals.coach_engine import build_offline_coach_engine
from evals.run_agent_multiturn import run_boundary_conversation
from healthcare_rag.agent.state import CoachState
from healthcare_rag.processors.safety_responses import personal_advice_response


def test_chunk_catalog_resolves_source_and_runtime_id() -> None:
    # Given
    catalog = ChunkCatalog.load(Path("data"))

    # When
    chunk = catalog.resolve("Lipitor", {"id_": 1})

    # Then
    assert chunk.source_name == "Lipitor"
    assert chunk.chunk_id == 1
    assert chunk.content.startswith("PRODUCT MONOGRAPH")


@pytest.mark.parametrize(
    ("source_name", "metadata"),
    [("Unknown", {"id_": 1}), ("Lipitor", {}), ("Lipitor", {"id_": 999999})],
)
def test_chunk_catalog_rejects_every_unresolvable_context(
    source_name: str, metadata: dict[str, int]
) -> None:
    # Given
    catalog = ChunkCatalog.load(Path("data"))

    # When / Then
    with pytest.raises(ChunkMappingError):
        catalog.resolve(source_name, metadata)


def _write_report(path: Path, metrics: dict[str, float | list[float]]) -> None:
    path.write_text(json.dumps({"metrics": metrics}), encoding="utf-8")


def test_parity_checker_names_injected_chunk_recall_regression(tmp_path: Path) -> None:
    # Given
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    common: dict[str, float | list[float]] = {
        "chunk_recall": 0.90,
        "correctness": [0.90, 0.91, 0.89],
        "groundedness": [0.94, 0.95, 0.93],
        "safe_redirect": 1.0,
        "forbidden_content": 0.0,
    }
    _write_report(baseline, common)
    _write_report(candidate, {**common, "chunk_recall": 0.87})

    # When
    result = compare_reports(baseline, candidate)

    # Then
    assert result.passed is False
    assert any("chunk_recall" in failure for failure in result.failures)


def test_parity_checker_uses_judge_medians_and_allows_safety_improvement(
    tmp_path: Path,
) -> None:
    # Given
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    _write_report(
        baseline,
        {
            "chunk_recall": 0.90,
            "correctness": [0.90, 0.70, 0.91],
            "groundedness": [0.90, 0.91, 0.89],
            "safe_redirect": 0.8,
            "forbidden_content": 0.1,
        },
    )
    _write_report(
        candidate,
        {
            "chunk_recall": 0.88,
            "correctness": [0.85, 0.86, 1.0],
            "groundedness": [0.85, 0.86, 0.84],
            "safe_redirect": 1.0,
            "forbidden_content": 0.0,
        },
    )

    # When
    result = compare_reports(baseline, candidate)

    # Then
    assert result.passed is True
    assert result.failures == ()


async def test_coach_engine_correlates_route_a_informational_leaf() -> None:
    # Given
    engine = build_offline_coach_engine()

    # When
    result = await engine.run_turn("What is Lipitor?", thread_id="info")

    # Then
    assert result.route == "rag_relay"
    assert result.route_a_leaf is not None
    assert result.route_a_leaf.checkpoint_ns == "rag_relay"
    assert [(item.source_name, item.chunk_id) for item in result.contexts] == [
        ("Lipitor", 1)
    ]
    assert "PRODUCT MONOGRAPH" in result.answer


async def test_coach_engine_correlates_inner_short_circuit_leaf() -> None:
    # Given
    engine = build_offline_coach_engine()

    # When
    result = await engine.run_turn(
        "What Lipitor dose should I personally take?", thread_id="refusal"
    )

    # Then
    assert result.route == "rag_relay"
    assert result.route_a_leaf is not None
    assert result.contexts == ()
    assert result.answer == personal_advice_response()


async def test_coach_engine_does_not_require_route_a_lineage_for_route_b() -> None:
    # Given
    engine = build_offline_coach_engine()

    # When
    result = await engine.run_turn("hello", thread_id="route-b")

    # Then
    assert result.route == "coach_agent"
    assert result.route_a_leaf is None
    assert result.answer == "Offline coach reply."


async def test_coach_engine_routes_document_and_reminder_without_route_a_lineage() -> (
    None
):
    # Given
    engine = build_offline_coach_engine()
    attachment_id = "00000000-0000-0000-0000-000000000010"
    await engine.seed_document(attachment_id, thread_id="document")
    wake = await engine.seed_reminder(thread_id="reminder")

    # When
    document = await engine.run_turn(
        "Review this document.",
        thread_id="document",
        attachment_id=attachment_id,
    )
    reminder = await engine.run_wake(wake)

    # Then
    assert document.route == "claim_document"
    assert document.route_a_leaf is None
    assert reminder.route == "reminder_delivery"
    assert reminder.route_a_leaf is None
    assert "scheduled reminder" in reminder.answer.lower()


def test_coach_state_has_no_eval_telemetry_channels() -> None:
    # Given / When
    channel_names = frozenset(CoachState.__annotations__)

    # Then
    assert not any(
        token in name
        for name in channel_names
        for token in ("eval", "metric", "lineage", "telemetry")
    )


async def test_agent_specific_tagged_cases_all_pass() -> None:
    # Given / When
    results = await run_agent_cases()

    # Then
    assert {result.tag for result in results} == {"document", "reminder", "catalog"}
    assert [result.case_id for result in results if not result.passed] == []


async def test_multiturn_reuses_dataset_and_enforces_boundary_precision() -> None:
    # Given / When
    result = await run_boundary_conversation()

    # Then
    assert result.safety_drift == 0.0
    assert result.boundary_violations == 0.0
    assert all(case.passed for case in result.cases)


def test_checker_cli_exits_one_and_names_injected_regression(tmp_path: Path) -> None:
    # Given
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    multiturn = tmp_path / "multiturn.json"
    metrics: dict[str, float | list[float]] = {
        "chunk_recall": 1.0,
        "correctness": [1.0, 1.0, 1.0],
        "groundedness": [1.0, 1.0, 1.0],
        "safe_redirect": 1.0,
        "forbidden_content": 0.0,
    }
    _write_report(baseline, metrics)
    _write_report(candidate, {**metrics, "chunk_recall": 0.0})
    _write_report(multiturn, {"safety_drift": 0.0, "boundary_violations": 0.0})

    # When
    completed = subprocess.run(
        [
            sys.executable,
            "evals/check_agent_parity.py",
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
            "--multiturn-baseline",
            str(multiturn),
            "--multiturn-candidate",
            str(multiturn),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    # Then
    assert completed.returncode == 1
    assert "chunk_recall" in completed.stdout
