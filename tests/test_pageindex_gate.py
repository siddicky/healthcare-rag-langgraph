"""Pure-function tests for the retriever A/B gate. No network, no LLM."""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from evals.evaluators import retrieval_page_hit
from evals.pageindex_gate import (
    EXIT_ERROR,
    EXIT_PASS,
    EXIT_STAGE1_REJECT,
    EXIT_STAGE2_FAIL,
    THRESHOLDS,
    arm_env,
    arm_prefix,
    build_outputs,
    build_stage2_gates,
    eligible_items,
    evaluate_stage1,
    evaluate_stage2,
    main,
    parse_args,
    parse_arm,
    persist,
    render_report,
    run_baseline_for_arm,
    select_candidate,
    stage2_metrics,
)
from healthcare_rag.models.retrieval import QueryDocument, QueryResult, QueryResultList

# --------------------------------------------------------------------------- #
# fixtures / builders                                                          #
# --------------------------------------------------------------------------- #

def _metrics(
    correctness: float | None = 0.90,
    groundedness: float | None = 0.95,
    holdout_correctness: float | None = 0.88,
    cost: float | None = 0.030,
    p50: float | None = 16.0,
) -> dict[str, Any]:
    return {
        "n": 86,
        "correctness": correctness,
        "groundedness": groundedness,
        "est_cost_usd": cost,
        "latency_p50_s": p50,
        "core": {"n": 45, "correctness": correctness},
        "holdout": {"n": 41, "correctness": holdout_correctness},
    }


def _gate(gates: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(g for g in gates if g["name"] == name)


def _arm(
    page_recall: float | None,
    chunk_recall: float | None = 0.8,
    n: int = 71,
    name: str = "x",
    latency: float | None = 1.0,
) -> dict[str, Any]:
    return {
        "arm": name,
        "n": n,
        "n_errors": 0,
        "page_recall": page_recall,
        "chunk_recall": chunk_recall,
        "per_split": {"core": {"n": 36, "page_recall": page_recall, "chunk_recall": chunk_recall}},
        "wall_s": 1.0,
        "mean_latency_s": latency,
        "items": [],
    }


# --------------------------------------------------------------------------- #
# stage 2 verdicts                                                             #
# --------------------------------------------------------------------------- #

def test_stage2_adopt_when_every_gate_holds() -> None:
    a = _metrics(correctness=0.85, groundedness=0.94, holdout_correctness=0.83, cost=0.030, p50=16.0)
    b = _metrics(correctness=0.90, groundedness=0.95, holdout_correctness=0.86, cost=0.035, p50=19.0)

    decision = evaluate_stage2(a, b)

    assert decision["verdict"] == "ADOPT"
    assert decision["pass"] is True
    assert decision["exit_code"] == EXIT_PASS
    assert decision["failed_gates"] == []
    assert decision["score"] == pytest.approx(0.05)
    assert all(g["pass"] is True for g in decision["gates"])


def test_stage2_correctness_delta_exactly_at_threshold_passes() -> None:
    a = _metrics(correctness=0.80)
    b = _metrics(correctness=0.80 + THRESHOLDS["min_correctness_delta"])

    assert _gate(evaluate_stage2(a, b)["gates"], "correctness_delta")["pass"] is True


def test_stage2_reject_when_correctness_gain_too_small() -> None:
    a = _metrics(correctness=0.90)
    b = _metrics(correctness=0.91)  # +0.01 < +0.03

    decision = evaluate_stage2(a, b)

    assert decision["verdict"] == "REJECT"
    assert decision["pass"] is False
    assert decision["exit_code"] == EXIT_STAGE2_FAIL
    assert decision["failed_gates"] == ["correctness_delta"]
    assert decision["score"] == pytest.approx(0.01)


def test_stage2_reject_when_holdout_correctness_regresses() -> None:
    """Overall correctness wins but the holdout split loses → quality REJECT, not INCONCLUSIVE."""
    a = _metrics(correctness=0.80, holdout_correctness=0.85)
    b = _metrics(correctness=0.90, holdout_correctness=0.80)

    decision = evaluate_stage2(a, b)

    assert decision["verdict"] == "REJECT"
    assert decision["failed_gates"] == ["holdout_correctness"]


def test_stage2_reject_when_groundedness_regresses() -> None:
    a = _metrics(correctness=0.80, groundedness=0.95)
    b = _metrics(correctness=0.90, groundedness=0.90)

    assert evaluate_stage2(a, b)["failed_gates"] == ["groundedness"]


def test_stage2_inconclusive_when_only_cost_fails() -> None:
    a = _metrics(correctness=0.80, cost=0.030)
    b = _metrics(correctness=0.90, cost=0.050)  # 1.67× > 1.25×

    decision = evaluate_stage2(a, b)

    assert decision["verdict"] == "INCONCLUSIVE"
    assert decision["pass"] is False
    assert decision["exit_code"] == EXIT_STAGE2_FAIL
    assert decision["failed_gates"] == ["cost_ratio"]
    assert _gate(decision["gates"], "cost_ratio")["ratio"] == pytest.approx(0.050 / 0.030)


def test_stage2_inconclusive_when_only_latency_fails() -> None:
    a = _metrics(correctness=0.80, p50=16.0)
    b = _metrics(correctness=0.90, p50=25.0)  # 1.5625× > 1.25×

    decision = evaluate_stage2(a, b)

    assert decision["verdict"] == "INCONCLUSIVE"
    assert decision["failed_gates"] == ["latency_p50_ratio"]


def test_stage2_cost_ratio_exactly_at_budget_passes() -> None:
    a = _metrics(cost=0.040)
    b = _metrics(cost=0.040 * THRESHOLDS["max_cost_ratio"])

    assert _gate(build_stage2_gates(a, b), "cost_ratio")["pass"] is True


def test_stage2_quality_failure_outranks_cost_failure() -> None:
    a = _metrics(correctness=0.90, cost=0.030)
    b = _metrics(correctness=0.90, cost=0.090)

    decision = evaluate_stage2(a, b)

    assert decision["verdict"] == "REJECT"
    assert set(decision["failed_gates"]) == {"correctness_delta", "cost_ratio"}


def test_stage2_missing_metric_is_inconclusive_not_adopt() -> None:
    a = _metrics(correctness=0.80)
    b = _metrics(correctness=None)  # judges failed to score arm B

    decision = evaluate_stage2(a, b)

    assert decision["verdict"] == "INCONCLUSIVE"
    assert decision["pass"] is False
    assert "correctness_delta" in decision["undecided_gates"]
    assert decision["score"] is None


def test_stage2_smoke_skips_judge_gates_and_keeps_ops_gates() -> None:
    a = _metrics(correctness=None, groundedness=None, holdout_correctness=None, cost=0.030, p50=16.0)
    b = _metrics(correctness=None, groundedness=None, holdout_correctness=None, cost=0.031, p50=16.5)

    decision = evaluate_stage2(a, b, smoke=True)

    assert decision["verdict"] == "SMOKE"
    assert decision["pass"] is True
    assert decision["exit_code"] == EXIT_PASS
    for name in ("correctness_delta", "groundedness", "holdout_correctness"):
        gate = _gate(decision["gates"], name)
        assert gate["pass"] is None
        assert gate["skipped"]
    assert _gate(decision["gates"], "cost_ratio")["pass"] is True


def test_stage2_smoke_still_fails_on_blown_cost() -> None:
    decision = evaluate_stage2(_metrics(cost=0.01), _metrics(cost=0.99), smoke=True)

    assert decision["verdict"] == "SMOKE"
    assert decision["pass"] is False
    assert decision["exit_code"] == EXIT_STAGE2_FAIL


# --------------------------------------------------------------------------- #
# stage 1                                                                      #
# --------------------------------------------------------------------------- #

def test_stage1_rejects_when_candidate_retrieves_worse_pages() -> None:
    decision = evaluate_stage1(_arm(0.82), _arm(0.71))

    assert decision["verdict"] == "REJECT"
    assert decision["pass"] is False
    assert decision["exit_code"] == EXIT_STAGE1_REJECT
    assert decision["score"] == pytest.approx(-0.11)
    assert decision["gate"]["pass"] is False


def test_stage1_passes_on_tie_self_check() -> None:
    """Both arms identical (the --arm-b weaviate self-check) → Δ 0, pass."""
    decision = evaluate_stage1(_arm(0.8214), _arm(0.8214))

    assert decision["pass"] is True
    assert decision["verdict"] is None
    assert decision["exit_code"] == EXIT_PASS
    assert decision["score"] == pytest.approx(0.0)


def test_stage1_passes_when_candidate_is_better() -> None:
    assert evaluate_stage1(_arm(0.70), _arm(0.90))["pass"] is True


def test_stage1_missing_mean_is_a_reject() -> None:
    decision = evaluate_stage1(_arm(0.80), _arm(None))

    assert decision["pass"] is False
    assert decision["exit_code"] == EXIT_STAGE1_REJECT
    assert decision["score"] is None


def test_eligible_items_drops_pageless_and_multiturn() -> None:
    golden = [
        {"id": "a", "expected_source_pages": [1], "history": []},
        {"id": "b", "expected_source_pages": [], "history": []},
        {"id": "c", "expected_source_pages": [2], "history": [{"question": "q", "answer": "a"}]},
        {"id": "d", "expected_source_pages": [3]},
    ]

    keep, counts = eligible_items(golden)

    assert [row["id"] for row in keep] == ["a", "d"]
    assert counts == {
        "total": 4,
        "eligible": 2,
        "skipped_no_expected_pages": 1,
        "skipped_multiturn": 1,
    }


def test_eligible_items_on_the_real_golden_dataset() -> None:
    from evals.dataset import load_golden

    keep, counts = eligible_items(load_golden())

    assert counts["total"] == 86
    assert counts["eligible"] == len(keep) > 0
    assert counts["eligible"] + counts["skipped_no_expected_pages"] + counts["skipped_multiturn"] == 86
    assert all(row.get("expected_source_pages") for row in keep)


# --------------------------------------------------------------------------- #
# outputs builder ≡ the main eval's definition                                 #
# --------------------------------------------------------------------------- #

def _results() -> QueryResultList:
    return QueryResultList(
        results=[
            QueryResult(
                source="Lipitor",
                query="dose",
                docs=[
                    QueryDocument(
                        content="c1",
                        score=0.9,
                        doc_id="uuid-1",
                        source_name="Lipitor",
                        metadata={"id_": 40, "doc_source": "lipitor.pdf"},
                        page_numbers=[19, 20],
                    ),
                    QueryDocument(
                        content="c2",
                        score=0.5,
                        doc_id="uuid-2",
                        source_name="Lipitor",
                        metadata={"id_": 41},
                        page_numbers=[33],
                    ),
                ],
            )
        ]
    )


def test_build_outputs_mirrors_engine_record_shape() -> None:
    outputs = build_outputs(_results())

    assert outputs["retrieved_chunk_ids"] == [40, 41]
    assert outputs["retrieved_pages"] == [19, 20, 33]
    assert outputs["retrieved_sources"] == ["Lipitor"]
    assert [c["page_numbers"] for c in outputs["contexts"]] == [[19, 20], [33]]


def test_build_outputs_page_recall_matches_retrieval_page_hit() -> None:
    outputs = build_outputs(_results())
    reference = {"expected_source_pages": [19, 47], "expected_source_chunk_ids": [40, 111]}

    scores = {s["key"]: s["score"] for s in retrieval_page_hit({}, outputs, reference)}

    # page 19 hit, page 47 missed → 0.5; one of two chunks touches an expected page.
    assert scores["page_recall"] == pytest.approx(0.5)
    assert scores["page_precision"] == pytest.approx(0.5)


def test_build_outputs_handles_empty_retrieval() -> None:
    outputs = build_outputs(QueryResultList(results=[]))
    reference = {"expected_source_pages": [19], "expected_source_chunk_ids": [40]}

    scores = {s["key"]: s["score"] for s in retrieval_page_hit({}, outputs, reference)}

    assert outputs == {
        "contexts": [],
        "retrieved_chunk_ids": [],
        "retrieved_pages": [],
        "retrieved_sources": [],
    }
    assert scores["page_recall"] == 0.0


def test_build_outputs_tolerates_missing_metadata_and_pages() -> None:
    results = QueryResultList(
        results=[
            QueryResult(
                source="Metformin",
                query="q",
                docs=[QueryDocument(content="c", score=1.0, doc_id="u", source_name="Metformin")],
            )
        ]
    )

    outputs = build_outputs(results)

    assert outputs["retrieved_chunk_ids"] == []
    assert outputs["retrieved_pages"] == []


# --------------------------------------------------------------------------- #
# metric extraction / report / CLI contract                                    #
# --------------------------------------------------------------------------- #

def test_stage2_metrics_pulls_overall_and_holdout() -> None:
    report = {
        "aggregate": {
            "overall": {"n": 86, "correctness": 0.9, "groundedness": 0.95, "est_cost_usd": 0.03,
                        "latency_p50_s": 16.0},
            "by_split": {"core": {"n": 45, "correctness": 0.92},
                         "holdout": {"n": 41, "correctness": 0.88}},
        }
    }

    metrics = stage2_metrics(report)

    assert metrics["correctness"] == 0.9
    assert metrics["holdout"] == {"n": 41, "correctness": 0.88}
    assert metrics["core"]["n"] == 45


def test_stage2_metrics_survives_an_empty_report() -> None:
    metrics = stage2_metrics({})

    assert metrics["correctness"] is None
    assert metrics["holdout"] == {"n": None, "correctness": None}


def _result(tmp_path: Path) -> dict[str, Any]:
    arm_a, arm_b = _arm(0.82), _arm(0.82)
    arm_a["arm"], arm_b["arm"] = "weaviate", "pageindex"
    decision = evaluate_stage1(arm_a, arm_b)
    return {
        "pass": True,
        "score": decision["score"],
        "stage": 1,
        "verdict": "SMOKE",
        "smoke": True,
        "arms": {"a": {"name": "weaviate"}, "b": {"name": "pageindex"}},
        "stage1": {
            "a": arm_a,
            "b": arm_b,
            "eligibility": {"total": 86, "eligible": 3, "skipped_no_expected_pages": 8,
                            "skipped_multiturn": 7},
            "gate": decision["gate"],
            "delta_page_recall": decision["score"],
            "wall_s": 2.0,
        },
        "stage2": None,
        "gates": [decision["gate"]],
        "thresholds": dict(THRESHOLDS),
        "git_sha": "deadbee",
        "started_at": "2026-08-20T00:00:00+00:00",
        "finished_at": "2026-08-20T00:01:00+00:00",
        "wall_s": 60.0,
        "exit_code": EXIT_PASS,
    }


def test_persist_writes_json_and_markdown_with_required_fields(tmp_path: Path) -> None:
    result = _result(tmp_path)
    run_dir = tmp_path / "run"

    written = persist(result, tmp_path / "out", run_dir)

    payload = json.loads(Path(written["json"]).read_text())
    assert payload["pass"] is True
    assert payload["verdict"] == "SMOKE"
    assert payload["score"] == 0.0
    assert payload["stage"] == 1
    assert payload["thresholds"]["min_correctness_delta"] == THRESHOLDS["min_correctness_delta"]
    assert json.loads(written["slim"])["pass"] is True
    # per-item detail is split out of the main JSON but still persisted
    assert "items" not in payload["stage1"]["a"]
    assert (run_dir / "pageindex-vs-weaviate.json").exists()
    assert (run_dir / "pageindex-vs-weaviate.md").exists()
    assert "Stage 1 — retrieval-only gate" in Path(written["md"]).read_text()


def test_render_report_contains_the_gate_table(tmp_path: Path) -> None:
    text = render_report(_result(tmp_path))

    assert "stage1_page_recall" in text
    assert "Verdict: SMOKE" in text
    assert "| `weaviate` |" in text


def test_main_prints_one_json_object_with_pass_as_last_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    async def fake_run_gate(args: Any) -> dict[str, Any]:
        return _result(tmp_path)

    monkeypatch.setattr("evals.pageindex_gate.run_gate", fake_run_gate)
    monkeypatch.setattr(
        "sys.argv",
        ["pageindex_gate", "--json", "--smoke", "--stage", "1", "--out-dir", str(tmp_path / "o")],
    )

    code = main()

    out = capsys.readouterr().out.strip().splitlines()
    payload = json.loads(out[-1])
    assert code == EXIT_PASS
    assert payload["pass"] is True
    assert set(payload) >= {"pass", "score", "stage", "verdict", "smoke", "arms", "gates",
                            "thresholds", "started_at", "finished_at", "wall_s", "report_path"}


def test_main_reports_exceptions_as_json_and_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    async def boom(args: Any) -> dict[str, Any]:
        raise RuntimeError("weaviate is down")

    monkeypatch.setattr("evals.pageindex_gate.run_gate", boom)
    monkeypatch.setattr(
        "sys.argv", ["pageindex_gate", "--json", "--out-dir", str(tmp_path / "o")]
    )

    code = main()

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == EXIT_ERROR
    assert payload["pass"] is False
    assert "weaviate is down" in payload["error"]


def test_main_propagates_the_stage1_reject_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_run_gate(args: Any) -> dict[str, Any]:
        result = _result(tmp_path)
        result.update(**{"pass": False, "verdict": "REJECT", "smoke": False,
                         "exit_code": EXIT_STAGE1_REJECT})
        return result

    monkeypatch.setattr("evals.pageindex_gate.run_gate", fake_run_gate)
    monkeypatch.setattr(
        "sys.argv", ["pageindex_gate", "--json", "--out-dir", str(tmp_path / "o")]
    )

    assert main() == EXIT_STAGE1_REJECT


def test_settings_for_arm_selects_the_retriever_without_leaking_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evals.pageindex_gate import settings_for_arm

    monkeypatch.delenv("HC_RAG_RETRIEVER", raising=False)

    assert settings_for_arm("pageindex").retriever == "pageindex"
    assert settings_for_arm("weaviate").retriever == "weaviate"
    assert "HC_RAG_RETRIEVER" not in __import__("os").environ


# --------------------------------------------------------------------------- #
# arm strings                                                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("arm", "expected"),
    [
        ("weaviate", ("weaviate", "none")),
        ("pinecone", ("pinecone", "none")),
        ("pageindex", ("pageindex", "none")),
        ("pinecone+rerank", ("pinecone", "pinecone")),
        ("weaviate+rerank", ("weaviate", "pinecone")),
        ("  weaviate+rerank  ", ("weaviate", "pinecone")),
    ],
)
def test_parse_arm_maps_the_grammar_to_both_env_knobs(arm: str, expected: tuple[str, str]) -> None:
    assert parse_arm(arm) == expected


@pytest.mark.parametrize("arm", ["foo", "+rerank", "weaviate+foo", "weaviate+", "", "rerank"])
def test_parse_arm_rejects_anything_outside_the_grammar(arm: str) -> None:
    with pytest.raises(ValueError, match="invalid arm"):
        parse_arm(arm)


def test_arm_env_and_prefix() -> None:
    assert arm_env("weaviate") == {"HC_RAG_RETRIEVER": "weaviate", "HC_RAG_RERANKER": "none"}
    assert arm_env("pinecone+rerank") == {
        "HC_RAG_RETRIEVER": "pinecone",
        "HC_RAG_RERANKER": "pinecone",
    }
    assert arm_prefix("weaviate+rerank") == "pi-gate-weaviate-rerank"
    assert arm_prefix("pinecone") == "pi-gate-pinecone"


def _fake_settings(monkeypatch: pytest.MonkeyPatch, *, with_reranker: bool) -> list[dict[str, str]]:
    """Install a fake ``GraphSettings`` and return the list env snapshots land in."""
    seen: list[dict[str, str]] = []

    if with_reranker:

        @dataclasses.dataclass
        class Fake:
            retriever: str = "weaviate"
            reranker: str = "none"

            @classmethod
            def from_env(cls) -> Fake:
                seen.append(
                    {k: os.environ.get(k, "") for k in ("HC_RAG_RETRIEVER", "HC_RAG_RERANKER")}
                )
                return cls(
                    retriever=os.environ.get("HC_RAG_RETRIEVER", "weaviate"),
                    reranker=os.environ.get("HC_RAG_RERANKER", "none"),
                )

    else:

        @dataclasses.dataclass  # type: ignore[no-redef]
        class Fake:
            retriever: str = "weaviate"

            @classmethod
            def from_env(cls) -> Fake:
                seen.append(
                    {k: os.environ.get(k, "") for k in ("HC_RAG_RETRIEVER", "HC_RAG_RERANKER")}
                )
                return cls(retriever=os.environ.get("HC_RAG_RETRIEVER", "weaviate"))

    monkeypatch.setattr("healthcare_rag.graph.settings.GraphSettings", Fake)
    return seen


def test_settings_for_arm_sets_both_env_vars_while_building(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evals.pageindex_gate import settings_for_arm

    monkeypatch.delenv("HC_RAG_RETRIEVER", raising=False)
    monkeypatch.setenv("HC_RAG_RERANKER", "leftover")
    seen = _fake_settings(monkeypatch, with_reranker=True)

    settings = settings_for_arm("pinecone+rerank")

    assert seen == [{"HC_RAG_RETRIEVER": "pinecone", "HC_RAG_RERANKER": "pinecone"}]
    assert (settings.retriever, settings.reranker) == ("pinecone", "pinecone")
    # the caller's environment is restored exactly, including the absent variable
    assert "HC_RAG_RETRIEVER" not in os.environ
    assert os.environ["HC_RAG_RERANKER"] == "leftover"


def test_settings_for_arm_without_rerank_defaults_the_knob_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evals.pageindex_gate import settings_for_arm

    seen = _fake_settings(monkeypatch, with_reranker=True)

    settings = settings_for_arm("weaviate")

    assert seen[0]["HC_RAG_RERANKER"] == "none"
    assert (settings.retriever, settings.reranker) == ("weaviate", "none")


def test_settings_for_arm_tolerates_a_missing_reranker_field_when_unused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before the reranker knob lands, plain arms still build; ``+rerank`` says why not."""
    from evals.pageindex_gate import settings_for_arm

    _fake_settings(monkeypatch, with_reranker=False)

    assert settings_for_arm("pinecone").retriever == "pinecone"
    with pytest.raises(RuntimeError, match="HC_RAG_RERANKER"):
        settings_for_arm("pinecone+rerank")


# --------------------------------------------------------------------------- #
# multi-candidate stage 1                                                      #
# --------------------------------------------------------------------------- #

def _candidates() -> list[dict[str, Any]]:
    return [
        _arm(0.82, name="pinecone", latency=2.0),
        _arm(0.90, name="pinecone+rerank", latency=5.0),
        _arm(0.70, name="pageindex", latency=0.5),
    ]


def test_stage1_gates_every_candidate_and_selects_the_best_passer() -> None:
    decision = evaluate_stage1(_arm(0.80, name="weaviate"), _candidates())

    assert decision["selected_arm"] == "pinecone+rerank"
    assert decision["pass"] is True
    assert decision["exit_code"] == EXIT_PASS
    assert decision["score"] == pytest.approx(0.10)
    assert [c["arm"] for c in decision["candidates"]] == ["pinecone", "pinecone+rerank", "pageindex"]
    assert [c["pass"] for c in decision["candidates"]] == [True, True, False]
    assert [c["selected"] for c in decision["candidates"]] == [False, True, False]
    assert [round(c["delta_page_recall"], 3) for c in decision["candidates"]] == [0.02, 0.10, -0.10]
    assert [g["arm"] for g in decision["gates"]] == ["pinecone", "pinecone+rerank", "pageindex"]
    assert decision["gate"]["arm"] == "pinecone+rerank"


def test_stage1_tie_on_page_recall_is_broken_by_latency() -> None:
    candidates = [
        _arm(0.85, name="pinecone", latency=3.0),
        _arm(0.85, name="weaviate+rerank", latency=1.2),
    ]

    decision = evaluate_stage1(_arm(0.80, name="weaviate"), candidates)

    assert decision["selected_arm"] == "weaviate+rerank"
    assert select_candidate(decision["candidates"]) == "weaviate+rerank"


def test_stage1_rejects_when_no_candidate_passes_and_scores_the_least_bad() -> None:
    candidates = [_arm(0.75, name="pinecone"), _arm(0.60, name="pageindex")]

    decision = evaluate_stage1(_arm(0.80, name="weaviate"), candidates)

    assert decision["selected_arm"] is None
    assert decision["pass"] is False
    assert decision["verdict"] == "REJECT"
    assert decision["exit_code"] == EXIT_STAGE1_REJECT
    assert decision["score"] == pytest.approx(-0.05)  # best (least bad) delta
    assert decision["gate"]["arm"] == "pinecone"


def test_stage1_stage2_arm_overrides_the_selection_rule() -> None:
    decision = evaluate_stage1(
        _arm(0.80, name="weaviate"), _candidates(), stage2_arm="pinecone"
    )

    assert decision["selected_arm"] == "pinecone"
    assert decision["pass"] is True
    assert decision["score"] == pytest.approx(0.02)
    assert [c["selected"] for c in decision["candidates"]] == [True, False, False]


def test_stage1_stage2_arm_must_name_a_candidate() -> None:
    with pytest.raises(ValueError, match="not one of the candidates"):
        evaluate_stage1(_arm(0.80), _candidates(), stage2_arm="weaviate")


def test_stage1_min_page_recall_delta_raises_the_bar() -> None:
    candidates = [_arm(0.81, name="pinecone")]
    reference = _arm(0.80, name="weaviate")

    assert evaluate_stage1(reference, candidates)["pass"] is True
    strict = evaluate_stage1(reference, candidates, min_page_recall_delta=0.05)
    assert strict["pass"] is False
    assert "+ 0.05" in strict["gate"]["threshold"]


def test_stage1_single_candidate_dict_keeps_the_old_contract() -> None:
    decision = evaluate_stage1(_arm(0.80, name="weaviate"), _arm(0.90, name="pageindex"))

    assert decision["pass"] is True
    assert decision["selected_arm"] == "pageindex"
    assert len(decision["candidates"]) == 1
    assert decision["gate"]["threshold"].startswith("page_recall(B) >= page_recall(A) -")


# --------------------------------------------------------------------------- #
# multi-arm CLI / persistence contract                                         #
# --------------------------------------------------------------------------- #

def test_arm_b_is_repeatable_and_defaults_to_pageindex() -> None:
    assert parse_args([]).arm_b == ["pageindex"]
    assert parse_args([]).report_name == "pageindex-vs-weaviate"
    assert parse_args([]).stage2_arm is None
    assert parse_args([]).min_page_recall_delta == 0.0

    args = parse_args(
        ["--arm-b", "pinecone", "--arm-b", "weaviate+rerank", "--report-name", "pinecone-rerank"]
    )

    assert args.arm_b == ["pinecone", "weaviate+rerank"]
    assert args.arm_a == "weaviate"
    assert args.report_name == "pinecone-rerank"


@pytest.mark.parametrize("bad", ["foo", "+rerank", "weaviate+foo"])
def test_parser_rejects_arms_outside_the_grammar(bad: str) -> None:
    with pytest.raises(SystemExit):
        parse_args(["--arm-b", bad])


def _multiarm_result() -> dict[str, Any]:
    reference = _arm(0.80, name="weaviate")
    candidates = [_arm(0.90, name="pinecone+rerank", latency=5.0), _arm(0.70, name="weaviate")]
    for index, run in enumerate([reference, *candidates]):
        run["items"] = [{"id": f"q{index}", "page_recall": run["page_recall"]}]
    decision = evaluate_stage1(reference, candidates)
    return {
        "pass": decision["pass"],
        "score": decision["score"],
        "stage": 1,
        "verdict": "INCONCLUSIVE",
        "smoke": False,
        "arms": {
            "a": {"name": "weaviate"},
            "b": {"name": decision["selected_arm"]},
            "candidates": ["pinecone+rerank", "weaviate"],
        },
        "report_name": "pinecone-rerank",
        "stage1": {
            "a": reference,
            "b": candidates[0],
            "arms": [reference, *candidates],
            "candidates": decision["candidates"],
            "selected_arm": decision["selected_arm"],
            "eligibility": {"total": 86, "eligible": 71, "skipped_no_expected_pages": 8,
                            "skipped_multiturn": 7},
            "gate": decision["gate"],
            "gates": decision["gates"],
            "delta_page_recall": decision["score"],
            "wall_s": 3.0,
        },
        "stage2": None,
        "gates": decision["gates"],
        "thresholds": dict(THRESHOLDS),
        "git_sha": "deadbee",
        "started_at": "2026-08-20T00:00:00+00:00",
        "finished_at": "2026-08-20T00:01:00+00:00",
        "wall_s": 60.0,
        "exit_code": EXIT_PASS,
    }


def test_persist_multiarm_keeps_the_json_contract_and_uses_the_report_name(
    tmp_path: Path,
) -> None:
    result = _multiarm_result()

    written = persist(result, tmp_path / "out", None)

    assert Path(written["md"]).name == "pinecone-rerank.md"
    payload = json.loads(Path(written["json"]).read_text())
    assert set(payload) >= {"pass", "score", "verdict", "arms", "gates", "thresholds"}
    assert payload["pass"] is True
    assert payload["score"] == pytest.approx(0.10)
    assert payload["arms"]["a"]["name"] == "weaviate"
    assert payload["arms"]["b"]["name"] == "pinecone+rerank"
    assert payload["arms"]["candidates"] == ["pinecone+rerank", "weaviate"]
    assert payload["stage1"]["selected_arm"] == "pinecone+rerank"
    assert [c["arm"] for c in payload["stage1"]["candidates"]] == ["pinecone+rerank", "weaviate"]
    # per-item detail is split out of the main JSON, one entry per arm string
    assert all("items" not in run for run in payload["stage1"]["arms"])
    items = json.loads(Path(payload["stage1_items_path"]).read_text())
    assert list(items) == ["weaviate", "pinecone+rerank", "weaviate#2"]
    assert items["weaviate#2"][0]["page_recall"] == 0.70


def test_persist_report_name_argument_overrides_the_result_field(tmp_path: Path) -> None:
    written = persist(_multiarm_result(), tmp_path / "out", None, "explicit-name")

    assert Path(written["md"]).name == "explicit-name.md"
    assert Path(written["json"]).name == "explicit-name.json"


def test_render_report_lists_every_candidate_and_names_the_selected_arm() -> None:
    text = render_report(_multiarm_result())

    assert "### Candidates" in text
    assert "| `pinecone+rerank` |" in text
    assert "selected for stage 2: `pinecone+rerank`" in text
    assert "stage1_page_recall (`pinecone+rerank`)" in text


def test_run_baseline_for_arm_carries_both_env_vars_and_the_derived_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = tmp_path / "run.json"
    report.write_text(json.dumps({"experiment_name": "exp", "aggregate": {}}))
    captured: dict[str, Any] = {}

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = iter([f"json: {report}\n", f"report: {tmp_path / 'run.md'}\n"])

        def wait(self) -> int:
            return 0

    def fake_popen(cmd: list[str], **kwargs: Any) -> FakeProc:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    out = run_baseline_for_arm("weaviate+rerank", repetitions=2, smoke=True)

    assert captured["env"]["HC_RAG_RETRIEVER"] == "weaviate"
    assert captured["env"]["HC_RAG_RERANKER"] == "pinecone"
    assert captured["cmd"][captured["cmd"].index("--prefix") + 1] == "pi-gate-weaviate-rerank"
    assert out["arm"] == "weaviate+rerank"
    assert out["experiment_name"] == "exp"
