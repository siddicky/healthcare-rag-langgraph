"""
Turn a finished LangSmith experiment into a local JSON + Markdown report.

Aggregates:
  * per-metric mean/rate overall and per category
  * latency p50 / p95, time-to-first-answer
  * tokens & estimated cost (local) and LangSmith-computed cost (source of truth)
  * per-stage token/cost breakdown pulled from the LangSmith run tree
"""

from __future__ import annotations

import json
import statistics
import time
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from langsmith import Client

RESULTS_DIR = Path(__file__).parent / "results"

STAGE_NAMES = {
    "clarify_query",
    "decompose_query",
    "retrieve_documents",
    "evaluate_retrieval",
    "extract_conversation_context",
    "generate_answer",
    "validate_answer",
    "generate_follow_ups",
    "safety_gate",
}

# Metrics where a "rate" (mean of 0/1) is the natural aggregate.
RATE_METRICS = {
    "answered", "pipeline_error", "forbidden_content", "false_premise_corrected", "numeric_advice_leak",
    "behavior_match_heuristic", "chunk_hit_any", "right_collection_routed",
    "used_refined_branch", "hallucinated", "behavior_match", "safe_redirect",
    "correct_but_ungrounded", "heuristic_agrees_with_judge",
}
# Renamed keys: old name -> current name (older experiments keep the old key in LangSmith).
KEY_ALIASES = {"must_not_mention_violation": "forbidden_content", "total_tokens": "total_ktokens"}
# Metrics that are averaged
MEAN_METRICS = {
    "must_mention_recall", "chunk_recall", "page_recall", "page_precision",
    "correctness", "groundedness", "n_branches", "llm_calls", "total_ktokens",
    "prompt_ktokens", "completion_ktokens", "est_cost_usd", "latency_s",
    "time_to_first_answer_s",
}
# Safety first (a healthcare app is judged on what it refuses before what it answers).
HEADLINE = [
    "behavior_match", "safe_redirect", "numeric_advice_leak", "forbidden_content", "false_premise_corrected",
    "correctness", "groundedness", "hallucinated", "correct_but_ungrounded", "must_mention_recall",
    "chunk_recall", "page_recall", "right_collection_routed", "answered", "pipeline_error",
    "heuristic_agrees_with_judge", "latency_s", "time_to_first_answer_s", "total_ktokens", "est_cost_usd",
    "llm_calls", "n_branches",
]


def normalise_feedback(fb: dict) -> dict:
    """Apply KEY_ALIASES and add derived metrics that combine two judges' opinions."""
    out = {}
    for k, v in fb.items():
        nk = KEY_ALIASES.get(k, k)
        if k == "total_tokens" and v is not None:
            v = v / 1000.0
        out.setdefault(nk, v)
    c, h = out.get("correctness"), out.get("hallucinated")
    out["correct_but_ungrounded"] = (int(c >= 0.8 and h == 1) if (c is not None and h is not None) else None)
    bm, bh = out.get("behavior_match"), out.get("behavior_match_heuristic")
    out["heuristic_agrees_with_judge"] = (int(bm == bh) if (bm is not None and bh is not None) else None)
    return out


def _pct(xs: list[float], p: float) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def _fmt(v: Any, key: str = "") -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        if key.endswith("usd"):
            return f"${v:.4f}"
        if key in RATE_METRICS or key in {"correctness", "groundedness", "must_mention_recall", "chunk_recall", "page_recall", "page_precision"}:
            return f"{v:.2f}"
        return f"{v:.2f}"
    return str(v)


def aggregate(rows: list[dict]) -> dict[str, Any]:
    """rows: [{example_id, category, drug, expected_behavior, outputs, feedback:{key:score}}]"""
    for r in rows:
        r["feedback"] = normalise_feedback(r.get("feedback") or {})

    def agg_group(group: list[dict]) -> dict[str, Any]:
        out: dict[str, Any] = {"n": len(group)}
        keys = sorted({k for r in group for k in r["feedback"]})
        for k in keys:
            vals = [r["feedback"][k] for r in group if r["feedback"].get(k) is not None]
            if not vals:
                out[k] = None
                continue
            out[k] = statistics.fmean(vals)
            if k == "latency_s":
                out["latency_p50_s"] = _pct(vals, 0.5)
                out["latency_p95_s"] = _pct(vals, 0.95)
                out["latency_max_s"] = max(vals)
            if k == "est_cost_usd":
                out["est_cost_total_usd"] = sum(vals)
            if k == "total_ktokens":
                out["total_ktokens_sum"] = sum(vals)
        return out

    by_cat: dict[str, list[dict]] = defaultdict(list)
    by_split: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
        by_split[r.get("split") or "core"].append(r)
    return {
        "overall": agg_group(rows),
        "by_category": {c: agg_group(g) for c, g in sorted(by_cat.items())},
        "by_split": {c: agg_group(g) for c, g in sorted(by_split.items())},
    }


def fetch_langsmith_stats(client: Client, experiment_name: str, retries: int = 6, wait_s: float = 5.0) -> dict[str, Any]:
    """Pull LangSmith-side aggregates (cost source of truth) + per-stage breakdown."""
    stats: dict[str, Any] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for attempt in range(retries):
            proj = client.read_project(project_name=experiment_name, include_stats=True)
            if getattr(proj, "run_count", 0):
                break
            time.sleep(wait_s)
        stats["project"] = {
            "run_count": getattr(proj, "run_count", None),
            "total_tokens": getattr(proj, "total_tokens", None),
            "prompt_tokens": getattr(proj, "prompt_tokens", None),
            "completion_tokens": getattr(proj, "completion_tokens", None),
            "total_cost": float(proj.total_cost) if getattr(proj, "total_cost", None) is not None else None,
            "latency_p50": getattr(proj, "latency_p50", None).total_seconds() if getattr(proj, "latency_p50", None) else None,
            "latency_p99": getattr(proj, "latency_p99", None).total_seconds() if getattr(proj, "latency_p99", None) else None,
            "error_rate": getattr(proj, "error_rate", None),
            "url": getattr(proj, "url", None),
        }
        # Per-stage breakdown from the run tree.
        runs = list(client.list_runs(project_name=experiment_name))
        by_id = {r.id: r for r in runs}
        stage_tot: dict[str, dict[str, float]] = defaultdict(lambda: {"calls": 0, "tokens": 0, "cost": 0.0})
        for r in runs:
            if r.run_type != "llm":
                continue
            stage = "unattributed"
            cur = r
            while cur is not None:
                if cur.name in STAGE_NAMES:
                    stage = cur.name
                    break
                cur = by_id.get(cur.parent_run_id) if cur.parent_run_id else None
            s = stage_tot[stage]
            s["calls"] += 1
            s["tokens"] += r.total_tokens or 0
            s["cost"] += float(r.total_cost or 0.0)
        # Roots (one per example) — count only the traced pipeline roots, not evaluator runs.
        roots = [r for r in runs if r.parent_run_id is None]
        stats["root_runs"] = len(roots)
        stats["by_stage"] = dict(sorted(stage_tot.items(), key=lambda kv: -kv[1]["cost"]))
        n_roots = max(len(roots), 1)
        stats["by_stage_per_query"] = {
            k: {"calls": v["calls"] / n_roots, "tokens": v["tokens"] / n_roots, "cost": v["cost"] / n_roots}
            for k, v in stats["by_stage"].items()
        }
    return stats


def write_report(
    experiment_name: str,
    experiment_url: Optional[str],
    rows: list[dict],
    metadata: dict[str, Any],
    ls_stats: Optional[dict[str, Any]] = None,
) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    agg = aggregate(rows)
    payload = {
        "experiment_name": experiment_name,
        "experiment_url": experiment_url,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
        "aggregate": agg,
        "langsmith": ls_stats,
        "rows": rows,
    }
    json_path = RESULTS_DIR / f"{experiment_name}.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str))

    o = agg["overall"]
    lines: list[str] = []
    lines.append(f"# Eval report — `{experiment_name}`\n")
    lines.append(f"Generated {payload['generated_at']}  ")
    if experiment_url:
        lines.append(f"LangSmith experiment: {experiment_url}  ")
    lines.append(f"Examples: **{o['n']}**  ")
    for k, v in metadata.items():
        lines.append(f"{k}: `{v}`  ")
    if ls_stats and ls_stats.get("root_runs") is not None and ls_stats["root_runs"] != o["n"]:
        lines.append(f"\n> ⚠️ LangSmith has {ls_stats['root_runs']} root runs but {o['n']} examples were run locally — "
                     "some run ingests failed; local rows are authoritative for outputs/latency/cost.  ")
    lines.append("")

    lines.append("## Headline (overall)\n")
    lines.append("| metric | value | note |")
    lines.append("|---|---|---|")
    notes = {
        "numeric_advice_leak": "refuse cases: answer contains a specific dose/threshold number (deterministic; lower is better)",
        "correct_but_ungrounded": "rate of correct answers with ≥1 unsupported claim — right answer, not from the retrieved text",
        "heuristic_agrees_with_judge": "behaviour regex heuristic vs LLM judge agreement (monitors heuristic drift)",
        "correctness": "LLM judge vs reference (answer cases only), 0–1",
        "groundedness": "share of answer claims supported by retrieved contexts",
        "hallucinated": "rate of answers with ≥1 unsupported claim (lower is better)",
        "behavior_match": "did it answer/refuse/clarify as expected (LLM judge)",
        "safe_redirect": "refuse cases: refused AND redirected safely",
        "must_mention_recall": "required key facts present (answer cases)",
        "forbidden_content": "rate of forbidden content (echoed PII, fabricated numbers) — lower is better; n/a for adversarial",
        "false_premise_corrected": "adversarial cases: answer corrected the false premise (0.5 = declined without correcting)",
        "chunk_recall": "expected chunks retrieved / expected",
        "page_recall": "expected pages retrieved / expected",
        "right_collection_routed": "router hit the right drug collection(s)",
        "answered": "rate of non-empty final answers",
        "pipeline_error": "crash rate (lower is better)",
        "latency_s": f"mean; p50 {_fmt(o.get('latency_p50_s'))}s, p95 {_fmt(o.get('latency_p95_s'))}s, max {_fmt(o.get('latency_max_s'))}s",
        "time_to_first_answer_s": "mean time until the preliminary (unvalidated) answer",
        "total_ktokens": f"mean thousands of tokens per query; total {_fmt(o.get('total_ktokens_sum'))}k",
        "est_cost_usd": f"mean per query (local pricing table); total ${o.get('est_cost_total_usd', 0):.4f}",
        "llm_calls": "mean OpenAI calls per query",
        "n_branches": "mean speculative branches per query",
    }
    for k in HEADLINE:
        if k in o:
            lines.append(f"| {k} | {_fmt(o[k], k)} | {notes.get(k, '')} |")
    lines.append("")

    if ls_stats and ls_stats.get("project"):
        p = ls_stats["project"]
        lines.append("## LangSmith-side aggregates (source of truth for cost)\n")
        lines.append(f"- runs: {p.get('run_count')} · root pipeline runs: {ls_stats.get('root_runs')}")
        lines.append(f"- total tokens: {p.get('total_tokens')} · total cost: ${(p.get('total_cost') or 0):.4f}"
                     f" · per query: ${((p.get('total_cost') or 0) / max(ls_stats.get('root_runs') or 1, 1)):.4f}")
        lines.append(f"- latency p50: {_fmt(p.get('latency_p50'))}s · p99: {_fmt(p.get('latency_p99'))}s · error rate: {p.get('error_rate')}")
        lines.append("")
        if ls_stats.get("by_stage_per_query"):
            lines.append("### Cost by pipeline stage (per query, from LangSmith run tree)\n")
            lines.append("| stage | LLM calls | tokens | cost | share |")
            lines.append("|---|---|---|---|---|")
            tot = sum(v["cost"] for v in ls_stats["by_stage_per_query"].values()) or 1
            for k, v in ls_stats["by_stage_per_query"].items():
                lines.append(f"| {k} | {v['calls']:.2f} | {v['tokens']:.0f} | ${v['cost']:.4f} | {100*v['cost']/tot:.0f}% |")
            lines.append("")

    if len(agg.get("by_split", {})) > 1:
        lines.append("## By split (core vs hold-out)\n")
        scols = ["n", "correctness", "groundedness", "hallucinated", "behavior_match", "safe_redirect",
                 "must_mention_recall", "forbidden_content", "chunk_recall", "answered", "latency_s", "est_cost_usd"]
        lines.append("| split | " + " | ".join(scols) + " |")
        lines.append("|---|" + "---|" * len(scols))
        for sp, a in agg["by_split"].items():
            lines.append(f"| {sp} | " + " | ".join(_fmt(a.get(k), k) for k in scols) + " |")
        lines.append("")

    lines.append("## By category\n")
    cats = agg["by_category"]
    cols = ["n", "behavior_match", "safe_redirect", "numeric_advice_leak", "forbidden_content", "false_premise_corrected",
            "correctness", "groundedness", "hallucinated", "must_mention_recall", "chunk_recall", "page_recall",
            "right_collection_routed", "answered", "latency_s", "est_cost_usd"]
    lines.append("| category | " + " | ".join(cols) + " |")
    lines.append("|---|" + "---|" * len(cols))
    for c, a in cats.items():
        lines.append(f"| {c} | " + " | ".join(_fmt(a.get(k), k) for k in cols) + " |")
    lines.append("")

    lines.append("## Per-example\n")
    lines.append("| id | category | behavior | correct | grounded | halluc. | chunk_recall | latency | cost | answer (truncated) |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: r["example_id"]):
        f = r["feedback"]
        ans = (r["outputs"].get("answer") or "(none)").replace("\n", " ").replace("|", "/")
        lines.append(
            f"| {r['example_id']} | {r['category']} | {_fmt(f.get('behavior_match'))} | {_fmt(f.get('correctness'))} | "
            f"{_fmt(f.get('groundedness'))} | {_fmt(f.get('hallucinated'))} | {_fmt(f.get('chunk_recall'))} | "
            f"{_fmt(f.get('latency_s'))}s | {_fmt(f.get('est_cost_usd'), 'est_cost_usd')} | {ans[:140]} |"
        )
    lines.append("")
    md_path = RESULTS_DIR / f"{experiment_name}.md"
    md_path.write_text("\n".join(lines))
    return json_path, md_path
