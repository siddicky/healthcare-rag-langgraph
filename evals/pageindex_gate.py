"""
Two-stage go/no-go gate for retrieval arms: one reference vs. N candidates.

An **arm string** is ``<retriever>[+rerank]`` — ``weaviate``, ``pinecone``,
``pageindex``, optionally with ``+rerank`` (which means ``HC_RAG_RERANKER=pinecone``;
without it the reranker is ``none``). The retrieval stage is the only thing that
differs between arms; safety, decomposition, generation and validation are
identical, so any delta is attributable to retrieval.

    # full gate over three candidates (stage 1, then stage 2 for the winner)
    uv run python -m evals.pageindex_gate --json \
        --arm-a weaviate --arm-b pinecone --arm-b weaviate+rerank --arm-b pinecone+rerank \
        --report-name pinecone-rerank

    # cheap plumbing check: 3 questions/arm, no judges
    uv run python -m evals.pageindex_gate --json --smoke

    # self-check: the candidate is the reference, so every delta must be ~0
    uv run python -m evals.pageindex_gate --json --smoke --arm-b weaviate --stage 1

**Stage 1 — retrieval-only gate.** Runs ``retrieve_documents`` for every eligible
golden question on the reference arm and on every candidate (no generation, no
judges) and compares mean ``page_recall`` using the *same*
``evals.evaluators.retrieval_page_hit`` definition the main eval uses. A candidate
that retrieves worse pages than the reference is out: nothing downstream can fix
retrieval it never saw. Among the passing candidates exactly one goes to stage 2 —
the highest ``page_recall``, ties broken by lower mean retrieval latency (override
with ``--stage2-arm``). If no candidate passes the mission is over: ``REJECT``,
exit 2, no judge money spent. Cheap enough (one routing call per question, plus the
arm's own retrieval cost) to run first.

**Stage 2 — paired full eval.** Runs ``evals.run_baseline`` as a subprocess for the
reference and the selected candidate, in the same session, with identical flags,
and compares the aggregates. Both arms are re-measured; historical report numbers
are never used as the baseline (judge noise is ±0.07 correctness at n≈44).

Exit codes: ``0`` pass · ``2`` stage-1 reject (terminal) · ``3`` stage-2 fail ·
``1`` error. With ``--json`` the last line of stdout is one JSON object; all
progress output goes to stderr.

Eligibility: golden items with no ``expected_source_pages`` are skipped in stage 1
(``page_recall`` is undefined for them, exactly as in the main eval), and so are
multi-turn items with a non-empty ``history`` — stage 1 issues a single retrieval
with no conversation context. Both counts are reported.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "results"
REPORT_STEM = "pageindex-vs-weaviate"

# Frozen after iteration 1 of the mission (see .omc/autoresearch/pageindex-vs-weaviate/evaluator.json).
THRESHOLDS: dict[str, float] = {
    # Stage 1: arm B must not lose page_recall to arm A (epsilon absorbs float noise).
    "stage1_page_recall_epsilon": 1e-9,
    # Stage 2 quality gates.
    "min_correctness_delta": 0.03,
    "min_groundedness_delta": 0.0,
    "min_holdout_correctness_delta": 0.0,
    # Stage 2 operational gates (ratios, B / A).
    "max_cost_ratio": 1.25,
    "max_latency_p50_ratio": 1.25,
}

# Gate names that decide REJECT (quality) vs. INCONCLUSIVE (cost/latency only).
QUALITY_GATES = frozenset({"correctness_delta", "groundedness", "holdout_correctness"})
OPERATIONAL_GATES = frozenset({"cost_ratio", "latency_p50_ratio"})

# Gates that cannot be computed without LLM judges (i.e. under --smoke).
JUDGE_GATES = QUALITY_GATES

RETRIEVERS = ("weaviate", "pinecone", "pageindex")
ARMS = RETRIEVERS  # backwards-compatible alias
RERANK_SUFFIX = "+rerank"
RERANKER_ON = "pinecone"
RERANKER_OFF = "none"
DEFAULT_CANDIDATE_ARMS = ("pageindex",)

SMOKE_QUESTIONS = 3
SMOKE_LIMIT = 3

EXIT_PASS = 0
EXIT_ERROR = 1
EXIT_STAGE1_REJECT = 2
EXIT_STAGE2_FAIL = 3


# --------------------------------------------------------------------------- #
# small helpers                                                                #
# --------------------------------------------------------------------------- #

def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _fmt(v: Any, kind: str = "num") -> str:
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "✅" if v else "❌"
    if isinstance(v, float):
        if kind == "usd":
            return f"${v:.4f}"
        if kind == "ratio":
            return f"{v:.3f}×"
        return f"{v:.3f}"
    return str(v)


def parse_arm(arm: str) -> tuple[str, str]:
    """``"<retriever>[+rerank]"`` → ``(HC_RAG_RETRIEVER, HC_RAG_RERANKER)``.

    ``"weaviate"`` → ``("weaviate", "none")`` · ``"pinecone+rerank"`` →
    ``("pinecone", "pinecone")``. Anything else raises ``ValueError``.
    """
    text = (arm or "").strip()
    head, sep, tail = text.partition("+")
    reranker = RERANKER_OFF
    if sep:
        if tail != RERANK_SUFFIX[1:]:
            message = (
                f"invalid arm {arm!r}: the only supported suffix is {RERANK_SUFFIX!r} "
                f"(got '+{tail}')"
            )
            raise ValueError(message)
        reranker = RERANKER_ON
    if head not in RETRIEVERS:
        message = (
            f"invalid arm {arm!r}: retriever must be one of {', '.join(RETRIEVERS)} "
            f"(optionally with {RERANK_SUFFIX})"
        )
        raise ValueError(message)
    return head, reranker


def arm_string(arm: str) -> str:
    """Validate an arm string (argparse ``type=``) and return it normalised."""
    try:
        retriever, reranker = parse_arm(arm)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return retriever + (RERANK_SUFFIX if reranker == RERANKER_ON else "")


def arm_env(arm: str) -> dict[str, str]:
    """The two environment variables that select an arm's retrieval stage."""
    retriever, reranker = parse_arm(arm)
    return {"HC_RAG_RETRIEVER": retriever, "HC_RAG_RERANKER": reranker}


def arm_prefix(arm: str) -> str:
    """``run_baseline --prefix`` for an arm (``weaviate+rerank`` → ``pi-gate-weaviate-rerank``)."""
    return "pi-gate-" + arm.replace("+", "-")


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, cwd=REPO_ROOT
        ).strip()
    except Exception:  # noqa: BLE001 - a missing/odd git checkout must not fail the gate
        return "unknown"


# --------------------------------------------------------------------------- #
# stage 1 — retrieval-only                                                     #
# --------------------------------------------------------------------------- #

def build_outputs(results: Any) -> dict[str, Any]:
    """Project a ``QueryResultList`` into the ``outputs`` dict the evaluators expect.

    Mirrors ``healthcare_rag/graph/engine_record.py::build_result`` field for field
    so ``page_recall`` / ``chunk_recall`` here mean exactly what they mean in the
    main eval.
    """
    contexts: list[dict[str, Any]] = []
    for result in results.results:
        for document in result.docs:
            metadata = document.metadata or {}
            contexts.append(
                {
                    "content": document.content,
                    "source": document.source_name,
                    "chunk_id": metadata.get("id_"),
                    "page_numbers": list(document.page_numbers or []),
                    "score": document.score,
                    "routed_query": result.query,
                }
            )
    return {
        "contexts": contexts,
        "retrieved_chunk_ids": [c["chunk_id"] for c in contexts if c["chunk_id"] is not None],
        "retrieved_pages": sorted({page for c in contexts for page in c["page_numbers"]}),
        "retrieved_sources": sorted({c["source"] for c in contexts}),
    }


def _score(feedback: list[dict[str, Any]] | dict[str, Any], key: str) -> float | None:
    items = feedback if isinstance(feedback, list) else [feedback]
    for item in items:
        if item.get("key") == key:
            return item.get("score")
    return None


def eligible_items(golden: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Golden items stage 1 can score, plus the counts of what was dropped and why."""
    keep, no_pages, multiturn = [], 0, 0
    for row in golden:
        if row.get("history"):
            multiturn += 1
            continue
        if not (row.get("expected_source_pages") or []):
            no_pages += 1
            continue
        keep.append(row)
    return keep, {
        "total": len(golden),
        "eligible": len(keep),
        "skipped_no_expected_pages": no_pages,
        "skipped_multiturn": multiturn,
    }


def settings_for_arm(arm: str) -> Any:
    """``GraphSettings`` with the retriever *and* reranker knobs set to ``arm``.

    Both ``HC_RAG_RETRIEVER`` and ``HC_RAG_RERANKER`` are set for the duration of
    ``from_env()`` (so knobs derived from them are read consistently) and then
    restored, and the resulting dataclass is pinned with ``dataclasses.replace``.
    Fields owned by the integration side of the mission may not exist yet: a
    missing field is tolerated as long as the arm asks for that knob's default.
    """
    import dataclasses

    from healthcare_rag.graph.settings import GraphSettings

    retriever, reranker = parse_arm(arm)
    env = {"HC_RAG_RETRIEVER": retriever, "HC_RAG_RERANKER": reranker}
    previous = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        settings = GraphSettings.from_env()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    names = {f.name for f in dataclasses.fields(settings)}
    changes: dict[str, Any] = {}
    for field, value, default, knob in (
        ("retriever", retriever, "weaviate", "HC_RAG_RETRIEVER"),
        ("reranker", reranker, RERANKER_OFF, "HC_RAG_RERANKER"),
    ):
        if field in names:
            changes[field] = value
        elif value != default:
            message = (
                f"GraphSettings has no {field!r} field, so arm {arm!r} cannot be built — "
                f"the {knob} knob has not landed yet."
            )
            raise RuntimeError(message)
    return dataclasses.replace(settings, **changes) if changes else settings


async def run_arm_stage1(arm: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """Retrieve for every item on one arm and score page/chunk recall."""
    from evals.evaluators import retrieval_chunk_hit, retrieval_page_hit
    from healthcare_rag.graph import resources as resources_module
    from healthcare_rag.graph.nodes.retrieve import retrieve_documents
    from healthcare_rag.graph.resources import Resources
    from healthcare_rag.graph.state import load_results

    settings = settings_for_arm(arm)
    resources = Resources(settings)
    previous_instance = resources_module.get()
    resources_module.override(resources)

    per_item: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    started = time.perf_counter()
    try:
        for position, row in enumerate(items, start=1):
            reference = {
                "expected_source_pages": row.get("expected_source_pages") or [],
                "expected_source_chunk_ids": row.get("expected_source_chunk_ids") or [],
            }
            state = {
                "query": row["question"],
                "phase": 0,
                "kind": "initial",
                "index": 0,
                "branch": "initial",
            }
            item_started = time.perf_counter()
            try:
                node_output = await retrieve_documents(state)  # type: ignore[arg-type]
                envelopes = node_output.get("retrievals") or []
                outputs = (
                    build_outputs(load_results(envelopes[0]["results"]))
                    if envelopes
                    else {"contexts": [], "retrieved_chunk_ids": [], "retrieved_pages": []}
                )
                page_recall = _score(retrieval_page_hit({}, outputs, reference), "page_recall")
                chunk_recall = _score(retrieval_chunk_hit({}, outputs, reference), "chunk_recall")
                error = None
            except Exception as exc:  # noqa: BLE001 - one bad question must not kill the gate
                page_recall = chunk_recall = None
                outputs = {"contexts": [], "retrieved_chunk_ids": [], "retrieved_pages": []}
                error = f"{type(exc).__name__}: {exc}"
                errors.append({"id": row["id"], "error": error})
                _log(f"  [{arm}] {row['id']}: ERROR {error}")
            per_item.append(
                {
                    "id": row["id"],
                    "split": row.get("split", "core"),
                    "category": row.get("category"),
                    "page_recall": page_recall,
                    "chunk_recall": chunk_recall,
                    "n_docs": len(outputs["contexts"]),
                    "latency_s": round(time.perf_counter() - item_started, 3),
                    "error": error,
                }
            )
            if error is None:
                _log(
                    f"  [{arm}] {position}/{len(items)} {row['id']}: "
                    f"page_recall={_fmt(page_recall)} chunk_recall={_fmt(chunk_recall)} "
                    f"docs={len(outputs['contexts'])}"
                )
    finally:
        await resources.aclose()
        resources_module.override(previous_instance)

    wall_s = round(time.perf_counter() - started, 2)
    return {
        "arm": arm,
        "n": len(per_item),
        "n_errors": len(errors),
        "errors": errors,
        "page_recall": _mean([i["page_recall"] for i in per_item if i["page_recall"] is not None]),
        "chunk_recall": _mean([i["chunk_recall"] for i in per_item if i["chunk_recall"] is not None]),
        "per_split": _per_split(per_item),
        "wall_s": wall_s,
        "mean_latency_s": _mean([i["latency_s"] for i in per_item]),
        "items": per_item,
    }


def _per_split(per_item: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for split in sorted({i["split"] for i in per_item}):
        group = [i for i in per_item if i["split"] == split]
        out[split] = {
            "n": len(group),
            "page_recall": _mean([i["page_recall"] for i in group if i["page_recall"] is not None]),
            "chunk_recall": _mean([i["chunk_recall"] for i in group if i["chunk_recall"] is not None]),
        }
    return out


def _stage1_gate(
    reference: dict[str, Any], candidate: dict[str, Any], minimum: float
) -> dict[str, Any]:
    """One candidate's page_recall gate row against the reference."""
    a, b = reference.get("page_recall"), candidate.get("page_recall")
    epsilon = THRESHOLDS["stage1_page_recall_epsilon"]
    if a is None or b is None:
        passed: bool | None = False
        delta = None
    else:
        delta = b - a
        passed = b >= a + minimum - epsilon
    threshold = (
        f"page_recall(B) >= page_recall(A) - {epsilon:g}"
        if minimum == 0
        else f"page_recall(B) >= page_recall(A) + {minimum:g} - {epsilon:g}"
    )
    return {
        "name": "stage1_page_recall",
        "arm": candidate.get("arm"),
        "a": a,
        "b": b,
        "delta": delta,
        "threshold": threshold,
        "pass": passed,
    }


def select_candidate(candidates: list[dict[str, Any]]) -> str | None:
    """Best passing candidate: highest ``page_recall``, ties → lower ``mean_latency_s``.

    ``candidates`` are the summary rows built by :func:`evaluate_stage1`.
    """
    passers = [c for c in candidates if c["pass"] and c.get("page_recall") is not None]
    if not passers:
        return None
    epsilon = THRESHOLDS["stage1_page_recall_epsilon"]
    best = max(c["page_recall"] for c in passers)
    tied = [c for c in passers if c["page_recall"] >= best - epsilon]
    if len(tied) == 1:
        return tied[0]["arm"]
    return min(tied, key=lambda c: (c.get("mean_latency_s") is None, c.get("mean_latency_s") or 0.0))["arm"]


def evaluate_stage1(
    arm_a: dict[str, Any],
    arm_b: dict[str, Any] | list[dict[str, Any]],
    *,
    min_page_recall_delta: float = 0.0,
    stage2_arm: str | None = None,
) -> dict[str, Any]:
    """Pure: reference arm + candidate arm(s) → gate rows, selection, verdict, exit code.

    ``arm_b`` may be a single arm dict (the historical one-candidate contract) or a
    list of them. Every candidate is gated against the reference independently;
    the one that goes to stage 2 is ``stage2_arm`` when forced, otherwise the best
    passer (see :func:`select_candidate`). No candidate passing is a terminal
    ``REJECT``; ``score`` is then the best (least bad) delta seen.
    """
    arms_b = list(arm_b) if isinstance(arm_b, list) else [arm_b]
    gates = [_stage1_gate(arm_a, candidate, min_page_recall_delta) for candidate in arms_b]
    candidates = [
        {
            "arm": candidate.get("arm"),
            "page_recall": candidate.get("page_recall"),
            "chunk_recall": candidate.get("chunk_recall"),
            "delta_page_recall": gate["delta"],
            "mean_latency_s": candidate.get("mean_latency_s"),
            "n_errors": candidate.get("n_errors"),
            "pass": bool(gate["pass"]),
            "selected": False,
        }
        for candidate, gate in zip(arms_b, gates, strict=True)
    ]

    if stage2_arm is not None:
        known = [c["arm"] for c in candidates]
        if stage2_arm not in known:
            message = f"--stage2-arm {stage2_arm!r} is not one of the candidates ({', '.join(map(str, known))})"
            raise ValueError(message)
        selected = stage2_arm
    else:
        selected = select_candidate(candidates)

    for candidate in candidates:
        candidate["selected"] = candidate["arm"] == selected

    index = next((i for i, c in enumerate(candidates) if c["selected"]), None)
    if index is None:
        # Nothing passed: report the least bad candidate so the verdict can name it.
        deltas = [
            (i, c["delta_page_recall"])
            for i, c in enumerate(candidates)
            if c["delta_page_recall"] is not None
        ]
        index = max(deltas, key=lambda pair: pair[1])[0] if deltas else 0
    primary = candidates[index]
    passed = bool(gates[index]["pass"]) and selected is not None

    return {
        "gate": gates[index],
        "gates": gates,
        "candidates": candidates,
        "selected_arm": selected,
        "primary_index": index,
        "pass": passed,
        "score": primary["delta_page_recall"],
        "verdict": None if passed else "REJECT",
        "exit_code": EXIT_PASS if passed else EXIT_STAGE1_REJECT,
    }


# --------------------------------------------------------------------------- #
# stage 2 — paired full eval                                                   #
# --------------------------------------------------------------------------- #

def run_baseline_for_arm(
    arm: str,
    *,
    repetitions: int,
    smoke: bool,
    prefix: str | None = None,
    concurrency: int = 1,
) -> dict[str, Any]:
    """Run ``evals.run_baseline`` in a subprocess with the arm's retriever/reranker env.

    ``concurrency`` is passed through to ``run_baseline --concurrency``; both arms
    must use the same value so the paired latency gate stays comparable.
    """
    cmd = [
        sys.executable,
        "-m",
        "evals.run_baseline",
        "--prefix",
        prefix or arm_prefix(arm),
        "--split",
        "core",
        "--split",
        "holdout",
        "--repetitions",
        str(repetitions),
        "--concurrency",
        str(concurrency),
        "--no-sync",
    ]
    if smoke:
        cmd += ["--limit", str(SMOKE_LIMIT), "--no-judges"]

    env = {**os.environ, **arm_env(arm)}
    _log(f"[stage2] {arm}: {' '.join(cmd)}")
    started = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line.rstrip("\n"))
        print(f"  [{arm}] {line.rstrip()}", file=sys.stderr, flush=True)
    returncode = proc.wait()
    wall_s = round(time.perf_counter() - started, 2)

    paths = _parse_run_baseline_output(lines)
    if returncode != 0 or not paths.get("json"):
        tail = "\n".join(lines[-25:])
        message = (
            f"run_baseline failed for arm {arm!r} (exit {returncode}, json path "
            f"{paths.get('json')!r}). Last output:\n{tail}"
        )
        raise RuntimeError(message)

    report = json.loads(Path(paths["json"]).read_text())
    return {
        "arm": arm,
        "experiment_name": report.get("experiment_name"),
        "experiment_url": report.get("experiment_url"),
        "json_path": str(paths["json"]),
        "report_path": paths.get("report"),
        "wall_s": wall_s,
        "report": report,
    }


def _parse_run_baseline_output(lines: list[str]) -> dict[str, str]:
    """Pull the ``report:`` / ``json:`` / ``experiment:`` paths out of the runner's output."""
    found: dict[str, str] = {}
    for line in lines:
        for key in ("report", "json", "experiment", "url"):
            marker = f"{key}: "
            if line.startswith(marker):
                value = line[len(marker):].strip()
                if key in {"report", "json"}:
                    path = Path(value)
                    value = str(path if path.is_absolute() else (REPO_ROOT / path))
                found[key] = value
    return found


def stage2_metrics(report: dict[str, Any]) -> dict[str, Any]:
    """The handful of aggregate numbers the gates are defined on."""
    aggregate = report.get("aggregate") or {}
    overall = aggregate.get("overall") or {}
    holdout = (aggregate.get("by_split") or {}).get("holdout") or {}
    core = (aggregate.get("by_split") or {}).get("core") or {}
    return {
        "n": overall.get("n"),
        "correctness": overall.get("correctness"),
        "groundedness": overall.get("groundedness"),
        "hallucinated": overall.get("hallucinated"),
        "chunk_recall": overall.get("chunk_recall"),
        "page_recall": overall.get("page_recall"),
        "est_cost_usd": overall.get("est_cost_usd"),
        "latency_p50_s": overall.get("latency_p50_s"),
        "latency_s": overall.get("latency_s"),
        "core": {"n": core.get("n"), "correctness": core.get("correctness")},
        "holdout": {"n": holdout.get("n"), "correctness": holdout.get("correctness")},
    }


def _delta_gate(name: str, a: float | None, b: float | None, minimum: float) -> dict[str, Any]:
    passed = None if (a is None or b is None) else bool((b - a) >= minimum - 1e-12)
    return {
        "name": name,
        "a": a,
        "b": b,
        "threshold": f"B - A >= {minimum:+.3f}",
        "pass": passed,
    }


def _ratio_gate(name: str, a: float | None, b: float | None, maximum: float) -> dict[str, Any]:
    if a is None or b is None:
        passed = None
    elif a == 0:
        passed = bool(b == 0)
    else:
        passed = bool(b <= maximum * a + 1e-12)
    return {
        "name": name,
        "a": a,
        "b": b,
        "threshold": f"B <= {maximum:g} × A",
        "pass": passed,
        "ratio": (b / a) if (a not in (None, 0) and b is not None) else None,
    }


def build_stage2_gates(metrics_a: dict[str, Any], metrics_b: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure: the five stage-2 gates from two metric dicts (``stage2_metrics`` shape)."""
    return [
        _delta_gate(
            "correctness_delta",
            metrics_a.get("correctness"),
            metrics_b.get("correctness"),
            THRESHOLDS["min_correctness_delta"],
        ),
        _delta_gate(
            "groundedness",
            metrics_a.get("groundedness"),
            metrics_b.get("groundedness"),
            THRESHOLDS["min_groundedness_delta"],
        ),
        _delta_gate(
            "holdout_correctness",
            (metrics_a.get("holdout") or {}).get("correctness"),
            (metrics_b.get("holdout") or {}).get("correctness"),
            THRESHOLDS["min_holdout_correctness_delta"],
        ),
        _ratio_gate(
            "cost_ratio",
            metrics_a.get("est_cost_usd"),
            metrics_b.get("est_cost_usd"),
            THRESHOLDS["max_cost_ratio"],
        ),
        _ratio_gate(
            "latency_p50_ratio",
            metrics_a.get("latency_p50_s"),
            metrics_b.get("latency_p50_s"),
            THRESHOLDS["max_latency_p50_ratio"],
        ),
    ]


def evaluate_stage2(
    metrics_a: dict[str, Any],
    metrics_b: dict[str, Any],
    *,
    smoke: bool = False,
) -> dict[str, Any]:
    """Pure: metrics → gates, pass flag, score, verdict, exit code.

    Under ``--smoke`` the judge-dependent gates are reported as ``pass: null``
    (skipped) and only the deterministic cost/latency gates decide.
    """
    gates = build_stage2_gates(metrics_a, metrics_b)
    if smoke:
        for gate in gates:
            if gate["name"] in JUDGE_GATES:
                gate["pass"] = None
                gate["skipped"] = "judges disabled (--smoke)"

    decided = [g for g in gates if g["pass"] is not None]
    failed = [g for g in decided if not g["pass"]]
    undecided = [g for g in gates if g["pass"] is None and not g.get("skipped")]
    passed = not failed and not undecided

    score = None
    if metrics_a.get("correctness") is not None and metrics_b.get("correctness") is not None:
        score = metrics_b["correctness"] - metrics_a["correctness"]

    if smoke:
        verdict = "SMOKE"
        passed = not failed
        exit_code = EXIT_PASS if passed else EXIT_STAGE2_FAIL
    elif any(g["name"] in QUALITY_GATES for g in failed):
        verdict, exit_code = "REJECT", EXIT_STAGE2_FAIL
    elif undecided or failed:
        verdict, exit_code = "INCONCLUSIVE", EXIT_STAGE2_FAIL
    else:
        verdict, exit_code = "ADOPT", EXIT_PASS

    return {
        "gates": gates,
        "pass": bool(passed),
        "score": score,
        "verdict": verdict,
        "exit_code": exit_code,
        "failed_gates": [g["name"] for g in failed],
        "undecided_gates": [g["name"] for g in undecided],
    }


# --------------------------------------------------------------------------- #
# report                                                                       #
# --------------------------------------------------------------------------- #

def _stage1_table(stage1: dict[str, Any]) -> list[str]:
    arms = stage1.get("arms") or [stage1["a"], stage1["b"]]
    splits = sorted({s for arm in arms for s in (arm.get("per_split") or {})})
    header = ["| arm | n | page_recall | chunk_recall "]
    for split in splits:
        header.append(f"| {split} page_recall | {split} chunk_recall ")
    header.append("| errors | wall (s) |")
    lines = ["".join(header), "|---" * (4 + 2 * len(splits) + 2) + "|"]
    for position, arm in enumerate(arms):
        # With an explicit arm list the first row is the reference; the self-check
        # runs the reference arm string twice, so say which row is which.
        role = " (reference)" if position == 0 and stage1.get("arms") else ""
        cells = [
            f"`{arm['arm']}`{role}",
            str(arm.get("n")),
            _fmt(arm.get("page_recall")),
            _fmt(arm.get("chunk_recall")),
        ]
        for split in splits:
            group = (arm.get("per_split") or {}).get(split) or {}
            cells += [_fmt(group.get("page_recall")), _fmt(group.get("chunk_recall"))]
        cells += [str(arm.get("n_errors", 0)), _fmt(arm.get("wall_s"))]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _candidates_table(stage1: dict[str, Any]) -> list[str]:
    """One row per candidate: recall, delta vs. the reference, latency, gate, selection."""
    candidates = stage1.get("candidates") or []
    lines = [
        "| candidate | page_recall | Δ vs. reference | mean latency (s) | pass | → stage 2 |",
        "|---|---|---|---|---|---|",
    ]
    for candidate in candidates:
        lines.append(
            f"| `{candidate.get('arm')}` | {_fmt(candidate.get('page_recall'))} "
            f"| {_fmt(candidate.get('delta_page_recall'))} "
            f"| {_fmt(candidate.get('mean_latency_s'))} "
            f"| {_fmt(bool(candidate.get('pass')))} "
            f"| {'✅' if candidate.get('selected') else ''} |"
        )
    return lines


def _gate_table(gates: list[dict[str, Any]], names: tuple[str, str]) -> list[str]:
    lines = [
        f"| gate | A (`{names[0]}`) | B (`{names[1]}`) | threshold | pass |",
        "|---|---|---|---|---|",
    ]
    for gate in gates:
        kind = "usd" if "cost" in gate["name"] else "num"
        status = "skipped" if gate.get("skipped") else _fmt(gate["pass"])
        label = gate["name"] + (f" (`{gate['arm']}`)" if gate.get("arm") else "")
        lines.append(
            f"| {label} | {_fmt(gate['a'], kind)} | {_fmt(gate['b'], kind)} "
            f"| {gate['threshold']} | {status} |"
        )
    return lines


def _split_table(stage2: dict[str, Any]) -> list[str]:
    reports = [stage2["a"]["metrics"], stage2["b"]["metrics"]]
    names = [stage2["a"]["arm"], stage2["b"]["arm"]]
    lines = [
        f"| split | metric | `{names[0]}` | `{names[1]}` | Δ |",
        "|---|---|---|---|---|",
    ]
    for split in ("core", "holdout"):
        for metric in ("n", "correctness"):
            a = (reports[0].get(split) or {}).get(metric)
            b = (reports[1].get(split) or {}).get(metric)
            delta = (b - a) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
            lines.append(
                f"| {split} | {metric} | {_fmt(float(a) if a is not None else None)} "
                f"| {_fmt(float(b) if b is not None else None)} "
                f"| {_fmt(float(delta) if delta is not None else None)} |"
            )
    return lines


def _comparison_tables(stage2: dict[str, Any]) -> list[str]:
    """Per-category comparison, reusing ``evals.compare.build_table`` verbatim."""
    from .compare import build_table

    reports = [stage2["a"].get("full_report"), stage2["b"].get("full_report")]
    if not all(reports):
        return ["_(per-category comparison unavailable: raw run reports missing)_"]
    lines = ["### Overall", ""]
    lines += build_table(reports)  # type: ignore[arg-type]
    categories = sorted({c for r in reports for c in (r or {}).get("aggregate", {}).get("by_category", {})})
    for category in categories:
        lines += ["", f"### {category}", ""]
        lines += build_table(reports, group="category", cat=category)  # type: ignore[arg-type]
    return lines


def _rationale(result: dict[str, Any]) -> str:
    verdict = result["verdict"]
    a = result["arms"]["a"]["name"]
    b = result["arms"]["b"]["name"]
    stage1 = result.get("stage1") or {}
    candidates = stage1.get("candidates") or []
    if verdict == "REJECT" and result["stage"] == 1:
        names = ", ".join(f"`{c['arm']}`" for c in candidates) or f"`{b}`"
        subject = (
            f"all {len(candidates)} candidates ({names})" if len(candidates) > 1 else names
        )
        return (
            f"Stage 1 rejected {subject}: the best of them, `{b}`, reached mean page_recall "
            f"{_fmt(stage1.get('b', {}).get('page_recall'))} "
            f"vs. {_fmt(stage1.get('a', {}).get('page_recall'))} for `{a}` "
            f"(Δ {_fmt(result['score'])}) over {stage1.get('a', {}).get('n')} eligible golden questions. "
            "Retrieval that returns worse pages cannot be recovered downstream, so the paired full eval "
            "was not run and no judge budget was spent."
        )
    if verdict == "REJECT":
        return (
            f"Stage 2 rejected `{b}` on quality: {', '.join(result['stage2']['failed_gates'])} failed. "
            f"Δcorrectness = {_fmt(result['score'])} against a required "
            f"{THRESHOLDS['min_correctness_delta']:+.2f}. Quality failures are not tuned away."
        )
    if verdict == "ADOPT":
        return (
            f"Stage 2 passed every gate: `{b}` beats `{a}` by {_fmt(result['score'])} mean correctness "
            f"(required {THRESHOLDS['min_correctness_delta']:+.2f}), holds groundedness and holdout "
            f"correctness, and stays inside {THRESHOLDS['max_cost_ratio']:g}× cost and "
            f"{THRESHOLDS['max_latency_p50_ratio']:g}× p50 latency."
        )
    if verdict == "SMOKE":
        return (
            "Smoke run — plumbing only. Sample sizes are far too small and (for stage 2) judges were "
            "disabled, so no adoption decision can be read off these numbers."
        )
    failed = (result.get("stage2") or {}).get("failed_gates") or []
    if failed:
        return (
            f"Inconclusive: `{b}` held quality but missed the operational budget "
            f"({', '.join(failed)}). Within judge noise this is a cost/latency call, not a quality one."
        )
    return (
        "Inconclusive: the gate did not reach a stage-2 decision (stage 1 only, or a metric was missing). "
        "Nothing here supports adopting or rejecting the arm."
    )


def render_report(result: dict[str, Any]) -> str:
    a = result["arms"]["a"]["name"]
    b = result["arms"]["b"]["name"]
    names = result["arms"].get("candidates") or [b]
    selected = (result.get("stage1") or {}).get("selected_arm")
    lines = [
        f"# Retrieval go/no-go — {' · '.join(f'`{n}`' for n in names)} vs. `{a}`",
        "",
        (
            f"- **Verdict: {result['verdict']}** (stage {result['stage']}, "
            f"`pass={str(result['pass']).lower()}`, score={_fmt(result['score'])})"
        ),
        (
            f"- Arms: A = `{a}` (reference) · candidates "
            f"{', '.join(f'`{n}`' for n in names)}"
            + (f" · selected for stage 2: `{selected}`" if selected else "")
        ),
        f"- Run: {result['started_at']} → {result['finished_at']} ({result['wall_s']:.0f} s wall)",
        f"- git: `{result['git_sha']}` · smoke: `{str(result['smoke']).lower()}`",
        "",
    ]
    if result["smoke"]:
        lines += [
            (
                "> **Smoke run.** 3 questions per arm in stage 1 and `--limit 3 --no-judges` "
                "in stage 2. This validates the harness, not the retrievers."
            ),
            "",
        ]

    stage1 = result.get("stage1")
    lines += ["## Stage 1 — retrieval-only gate", ""]
    if stage1:
        counts = stage1.get("eligibility") or {}
        lines += [
            (
                f"{counts.get('eligible')} of {counts.get('total')} golden questions scored "
                f"({counts.get('skipped_no_expected_pages')} have no `expected_source_pages`, "
                f"{counts.get('skipped_multiturn')} are multi-turn). `page_recall` / "
                "`chunk_recall` use the `evals.evaluators` definitions unchanged."
            ),
            "",
        ]
        lines += _stage1_table(stage1)
        if stage1.get("candidates"):
            lines += ["", "### Candidates", "", *_candidates_table(stage1)]
        lines += ["", *_gate_table(stage1.get("gates") or [stage1["gate"]], (a, b)), ""]
    else:
        lines += ["_(skipped)_", ""]

    lines += ["## Stage 2 — paired full eval", ""]
    stage2 = result.get("stage2")
    if stage2:
        lines += [
            f"- A: `{stage2['a'].get('experiment_name')}` → `{stage2['a'].get('report_path')}`",
            f"- B: `{stage2['b'].get('experiment_name')}` → `{stage2['b'].get('report_path')}`",
            "",
            *_gate_table(stage2["gates"], (a, b)),
            "",
            "### By split",
            "",
            *_split_table(stage2),
            "",
            "### Full comparison",
            "",
            *_comparison_tables(stage2),
            "",
        ]
    else:
        lines += ["_(not run)_", ""]

    lines += [
        "## Verdict",
        "",
        _rationale(result),
        "",
        "## Thresholds (frozen)",
        "",
        "| threshold | value |",
        "|---|---|",
        *[f"| {k} | {v:g} |" for k, v in THRESHOLDS.items()],
        "",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# orchestration                                                                #
# --------------------------------------------------------------------------- #

async def run_gate(args: argparse.Namespace) -> dict[str, Any]:
    from .dataset import load_golden

    started_at, started = _now(), time.perf_counter()
    candidate_arms: list[str] = list(getattr(args, "arm_b", None) or DEFAULT_CANDIDATE_ARMS)
    forced = getattr(args, "stage2_arm", None)
    min_delta = float(getattr(args, "min_page_recall_delta", 0.0) or 0.0)
    stage2_candidate = forced or candidate_arms[0]
    result: dict[str, Any] = {
        "pass": False,
        "score": None,
        "stage": 1,
        "verdict": "INCONCLUSIVE",
        "smoke": bool(args.smoke),
        "arms": {
            "a": {"name": args.arm_a},
            "b": {"name": stage2_candidate},
            "candidates": candidate_arms,
        },
        "report_name": getattr(args, "report_name", None) or REPORT_STEM,
        "stage1": None,
        "stage2": None,
        "gates": [],
        "thresholds": dict(THRESHOLDS),
        "min_page_recall_delta": min_delta,
        "git_sha": _git_sha(),
        "started_at": started_at,
        "repetitions": args.repetitions,
    }

    run_stage1 = args.stage in {"1", "all"} and not args.skip_stage1
    run_stage2 = args.stage in {"2", "all"}
    exit_code = EXIT_PASS

    if run_stage1:
        golden = load_golden()
        items, counts = eligible_items(golden)
        if args.smoke:
            items = items[:SMOKE_QUESTIONS]
            counts = {**counts, "eligible": len(items), "smoke_subset": True}
        _log(
            f"[stage1] {len(items)} questions × {len(candidate_arms) + 1} arms "
            f"({args.arm_a} vs {', '.join(candidate_arms)})"
        )
        arm_a = await run_arm_stage1(args.arm_a, items)
        _log(f"[stage1] {args.arm_a}: page_recall={_fmt(arm_a['page_recall'])} in {arm_a['wall_s']}s")
        arms_b: list[dict[str, Any]] = []
        for arm in candidate_arms:
            run = await run_arm_stage1(arm, items)
            _log(f"[stage1] {arm}: page_recall={_fmt(run['page_recall'])} in {run['wall_s']}s")
            arms_b.append(run)

        decision = evaluate_stage1(
            arm_a, arms_b, min_page_recall_delta=min_delta, stage2_arm=forced
        )
        primary = arms_b[decision["primary_index"]]
        result["stage1"] = {
            "a": arm_a,
            "b": primary,
            "arms": [arm_a, *arms_b],
            "candidates": decision["candidates"],
            "selected_arm": decision["selected_arm"],
            "eligibility": counts,
            "gate": decision["gate"],
            "gates": decision["gates"],
            "delta_page_recall": decision["score"],
            "wall_s": round(arm_a["wall_s"] + sum(run["wall_s"] for run in arms_b), 2),
        }
        result["gates"] = decision["gates"]
        result["score"] = decision["score"]
        result["pass"] = decision["pass"]
        exit_code = decision["exit_code"]
        if decision["selected_arm"]:
            stage2_candidate = decision["selected_arm"]
        result["arms"]["b"] = {"name": stage2_candidate}

        if not decision["pass"]:
            result["verdict"] = "SMOKE" if args.smoke else "REJECT"
            if args.stage != "2":
                run_stage2 = False
            _log(f"[stage1] REJECT: no candidate passed (best Δpage_recall={_fmt(decision['score'])})")
        else:
            result["verdict"] = "SMOKE" if args.smoke else "INCONCLUSIVE"
            _log(f"[stage1] selected `{stage2_candidate}` for stage 2")
            if not run_stage2:
                # Stage 1 alone never authorises adoption.
                result["pass"] = bool(args.smoke)
                result["note"] = "stage 1 passed; stage 2 not run, so no adoption decision was made"
                exit_code = EXIT_PASS if args.smoke else EXIT_STAGE2_FAIL

    if run_stage2:
        result["stage"] = 2
        result["arms"]["b"] = {"name": stage2_candidate}
        run_a = run_baseline_for_arm(
            args.arm_a, repetitions=args.repetitions, smoke=args.smoke, concurrency=args.concurrency
        )
        run_b = run_baseline_for_arm(
            stage2_candidate, repetitions=args.repetitions, smoke=args.smoke, concurrency=args.concurrency
        )
        metrics_a, metrics_b = stage2_metrics(run_a["report"]), stage2_metrics(run_b["report"])
        decision = evaluate_stage2(metrics_a, metrics_b, smoke=args.smoke)
        result["stage2"] = {
            "a": {**{k: v for k, v in run_a.items() if k != "report"}, "metrics": metrics_a,
                  "full_report": run_a["report"]},
            "b": {**{k: v for k, v in run_b.items() if k != "report"}, "metrics": metrics_b,
                  "full_report": run_b["report"]},
            "gates": decision["gates"],
            "failed_gates": decision["failed_gates"],
            "undecided_gates": decision["undecided_gates"],
        }
        result["gates"] = decision["gates"]
        result["score"] = decision["score"] if decision["score"] is not None else result["score"]
        result["pass"] = decision["pass"]
        result["verdict"] = decision["verdict"]
        result.pop("note", None)
        exit_code = decision["exit_code"]

    result["finished_at"] = _now()
    result["wall_s"] = round(time.perf_counter() - started, 2)
    result["exit_code"] = exit_code
    return result


def _items_by_arm(stage1: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Per-question stage-1 detail keyed by arm string (reference first).

    A candidate that repeats the reference arm string (the ``--arm-b weaviate``
    self-check runs it twice, on purpose) gets a ``#2`` suffix so no run is lost.
    """
    runs = stage1.get("arms") or [stage1.get(side) for side in ("a", "b")]
    out: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        if not run:
            continue
        key = str(run.get("arm"))
        if key in out:
            suffix = 2
            while f"{key}#{suffix}" in out:
                suffix += 1
            key = f"{key}#{suffix}"
        out[key] = run.get("items") or []
    return out


def persist(
    result: dict[str, Any],
    out_dir: Path,
    run_dir: Path | None,
    report_name: str | None = None,
) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = report_name or result.get("report_name") or REPORT_STEM
    md_path = out_dir / f"{stem}.md"
    json_path = out_dir / f"{stem}.json"
    result["report_path"] = str(md_path.relative_to(REPO_ROOT) if md_path.is_relative_to(REPO_ROOT) else md_path)
    md_path.write_text(render_report(result))

    # The embedded raw run_baseline reports make the JSON huge; keep pointers only.
    slim = json.loads(json.dumps(result, default=str))
    for side in ("a", "b"):
        if slim.get("stage2"):
            slim["stage2"][side].pop("full_report", None)
        if slim.get("stage1"):
            slim["stage1"][side].pop("items", None)
    for run in (slim.get("stage1") or {}).get("arms") or []:
        run.pop("items", None)
    slim["stage1_items_path"] = None
    if result.get("stage1"):
        items_path = out_dir / f"{stem}-stage1-items.json"
        items_path.write_text(json.dumps(_items_by_arm(result["stage1"]), indent=2, default=str))
        slim["stage1_items_path"] = str(items_path)
    json_path.write_text(json.dumps(slim, indent=2, default=str))

    if run_dir:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(json_path, run_dir / f"{stem}.json")
        shutil.copy2(md_path, run_dir / f"{stem}.md")
    return {"json": str(json_path), "md": str(md_path), "slim": json.dumps(slim)}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--json", action="store_true", help="print one JSON object as the last stdout line")
    ap.add_argument("--smoke", action="store_true",
                    help=f"plumbing check: {SMOKE_QUESTIONS} questions/arm in stage 1, "
                         f"--limit {SMOKE_LIMIT} --no-judges in stage 2")
    ap.add_argument("--stage", choices=["1", "2", "all"], default="all")
    ap.add_argument("--arm-a", default="weaviate", type=arm_string, help="reference arm")
    ap.add_argument("--arm-b", action="append", type=arm_string, default=None,
                    help="candidate arm, repeatable; '<retriever>[+rerank]' with retriever in "
                         f"{'|'.join(RETRIEVERS)} (default: {DEFAULT_CANDIDATE_ARMS[0]}; "
                         "--arm-b weaviate is the self-check: deltas must be ~0)")
    ap.add_argument("--stage2-arm", type=arm_string, default=None,
                    help="force this candidate into stage 2 instead of the best stage-1 passer")
    ap.add_argument("--min-page-recall-delta", type=float, default=0.0,
                    help="stage-1 margin a candidate must beat the reference by (default 0.0)")
    ap.add_argument("--report-name", default=REPORT_STEM,
                    help=f"stem for evals/results/<name>.{{md,json}} (default: {REPORT_STEM})")
    ap.add_argument("--repetitions", type=int, default=2, help="stage-2 repetitions per example")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="stage-2 run_baseline concurrency (same for both arms; >1 inflates latency equally)")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--run-dir", type=Path, default=None,
                    help="also copy the final JSON/MD here (autoresearch run directory)")
    ap.add_argument("--skip-stage1", action="store_true", help="jump straight to the paired full eval")
    return ap


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parsed CLI args with ``--arm-b`` defaulted (append + default don't mix in argparse)."""
    args = build_parser().parse_args(argv)
    if not args.arm_b:
        args.arm_b = list(DEFAULT_CANDIDATE_ARMS)
    return args


def main() -> int:
    args = parse_args()
    try:
        result = asyncio.run(run_gate(args))
        written = persist(result, Path(args.out_dir), args.run_dir, getattr(args, "report_name", None))
        _log(
            f"verdict={result['verdict']} pass={result['pass']} score={_fmt(result['score'])} "
            f"stage={result['stage']} → {written['md']}"
        )
        if args.json:
            print(written["slim"])
        else:
            print(Path(written["md"]).read_text())
        return int(result["exit_code"])
    except Exception as exc:
        payload = {
            "pass": False,
            "score": None,
            "stage": 1,
            "verdict": "INCONCLUSIVE",
            "error": f"{type(exc).__name__}: {exc}",
            "finished_at": _now(),
        }
        if args.json:
            import traceback

            traceback.print_exc(file=sys.stderr)
            print(json.dumps(payload))
        else:
            raise
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
