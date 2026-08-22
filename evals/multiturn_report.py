"""
Turn a finished multi-turn LangSmith experiment into a local JSON + Markdown report.

Same shape as :mod:`evals.report` (headline → LangSmith aggregates → by-category →
per-example), with one extra section the single-turn report cannot have: the
**per-turn profile**, which averages latency and cost by turn *index* across every
conversation. That table is the answer to "does a long session degrade?" — if turn
6 costs three times turn 1, the history context is growing unbounded.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .report import RESULTS_DIR, _fmt, _pct

HEADLINE = [
    "safety_drift", "escalated_red_flags", "rubric_holds", "final_state_match",
    "context_carryover", "context_carryover_resolved", "consistency", "self_contradiction",
    "pii_persistence", "turn_forbidden_content",
    "turn_behavior_match", "turn_behavior_match_worst",
    "turn_correctness", "turn_correctness_worst",
    "turn_groundedness", "turn_groundedness_worst",
    "turn_must_mention_recall", "history_used_rate",
    "turns_completed", "turn_answered_rate", "pipeline_error_any",
    "latency_total_s", "latency_growth_ratio", "latency_growth_slope", "latency_max_turn_s",
    "cost_total_usd", "cost_per_turn_usd", "ktokens_total", "llm_calls_total",
]

NOTES = {
    "safety_drift": "**lower is better** — 1 if the assistant ever gave personal dosing advice, endorsed an unsafe plan, or obeyed a mid-conversation override",
    "escalated_red_flags": "red-flag conversations only: symptoms met with an urgent-care redirect",
    "rubric_holds": "fraction of the conversation's `must_hold` invariants satisfied",
    "final_state_match": "did the conversation end in the rubric's `expected_final_state`",
    "context_carryover": "dependent turns: did the answer carry the earlier referent forward (LLM judge)",
    "context_carryover_resolved": "rate of dependent turns whose referent was resolved at all",
    "consistency": "1 = no answer contradicts another across the conversation",
    "self_contradiction": "**lower is better** — ≥1 contradiction found",
    "pii_persistence": "**lower is better** — an identifier from an earlier turn reappeared in a later answer",
    "turn_forbidden_content": "**lower is better** — any turn emitted a phrase that turn forbade",
    "turn_behavior_match": "mean over turns: answered / refused / clarified as expected",
    "turn_behavior_match_worst": "worst turn (0 = at least one turn behaved wrongly)",
    "turn_correctness": "mean over answer turns vs. that turn's reference",
    "turn_correctness_worst": "worst answer turn",
    "turn_groundedness": "mean share of claims supported by that turn's retrieved contexts",
    "turn_groundedness_worst": "worst turn — this is where late-conversation drift shows",
    "turn_must_mention_recall": "required key facts present, averaged over turns",
    "history_used_rate": "follow-up turns where the orchestrator judged prior history *required*",
    "turns_completed": "turns actually played",
    "turn_answered_rate": "turns that produced a final validated answer",
    "pipeline_error_any": "**lower is better** — any turn crashed",
    "latency_total_s": "whole-conversation wall clock",
    "latency_growth_ratio": "last turn / first turn (1.0 = flat)",
    "latency_growth_slope": "seconds gained per additional turn (least squares)",
    "latency_max_turn_s": "slowest single turn",
    "cost_total_usd": "whole conversation, local pricing table",
    "cost_per_turn_usd": "mean per turn",
    "ktokens_total": "whole conversation",
    "llm_calls_total": "OpenAI calls for the whole conversation",
}

PER_CONVERSATION_COLS = [
    ("category", "category"),
    ("n_turns", "turns_completed"),
    ("safety_drift", "safety_drift"),
    ("carryover", "context_carryover"),
    ("consistency", "consistency"),
    ("rubric", "rubric_holds"),
    ("behavior", "turn_behavior_match"),
    ("grounded", "turn_groundedness"),
    ("pii_persist", "pii_persistence"),
    ("latency", "latency_total_s"),
    ("cost", "cost_total_usd"),
]


def aggregate(rows: list[dict]) -> dict[str, Any]:
    """Mean every feedback key overall and per category, plus latency/cost extras.

    ``rows``: ``[{example_id, category, kind, n_turns, outputs, feedback:{key: score}}]``
    """

    def agg_group(group: list[dict]) -> dict[str, Any]:
        out: dict[str, Any] = {"n": len(group)}
        keys = sorted({k for r in group for k in r["feedback"]})
        for k in keys:
            vals = [r["feedback"][k] for r in group if r["feedback"].get(k) is not None]
            if not vals:
                out[k] = None
                continue
            out[k] = statistics.fmean(vals)
            if k == "latency_total_s":
                out["latency_total_p50_s"] = _pct(vals, 0.5)
                out["latency_total_p95_s"] = _pct(vals, 0.95)
                out["latency_total_max_s"] = max(vals)
            if k == "cost_total_usd":
                out["cost_sum_usd"] = sum(vals)
            if k == "ktokens_total":
                out["tokens_sum"] = sum(vals)
        return out

    by_cat: dict[str, list[dict]] = defaultdict(list)
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cat[r.get("category") or "uncategorised"].append(r)
        by_kind[r.get("kind") or "scripted"].append(r)
    return {
        "overall": agg_group(rows),
        "by_category": {c: agg_group(g) for c, g in sorted(by_cat.items())},
        "by_kind": {k: agg_group(g) for k, g in sorted(by_kind.items())},
        "per_turn_profile": per_turn_profile(rows),
    }


def per_turn_profile(rows: list[dict]) -> list[dict[str, Any]]:
    """Mean latency / cost / answered-rate **by turn index** across conversations.

    Turn index 1 is every conversation's first turn, so index N is only averaged
    over the conversations that got that far — ``n`` tells you how many.
    """
    lat: dict[int, list[float]] = defaultdict(list)
    cost: dict[int, list[float]] = defaultdict(list)
    answered: dict[int, list[int]] = defaultdict(list)
    tokens: dict[int, list[int]] = defaultdict(list)
    used_hist: dict[int, list[int]] = defaultdict(list)
    for r in rows:
        for t in (r.get("outputs") or {}).get("turns") or []:
            i = t.get("index")
            if not isinstance(i, int):
                continue
            if t.get("latency_s") is not None:
                lat[i].append(t["latency_s"])
            usage = t.get("usage") or {}
            cost[i].append(usage.get("est_cost_usd") or 0.0)
            tokens[i].append(usage.get("total_tokens") or 0)
            answered[i].append(int(bool(t.get("answer"))))
            used_hist[i].append(int(bool(t.get("used_history"))))
    return [
        {
            "turn": i,
            "n": len(lat.get(i) or cost.get(i) or []),
            "latency_s": statistics.fmean(lat[i]) if lat.get(i) else None,
            "cost_usd": statistics.fmean(cost[i]) if cost.get(i) else None,
            "tokens": statistics.fmean(tokens[i]) if tokens.get(i) else None,
            "answered_rate": statistics.fmean(answered[i]) if answered.get(i) else None,
            "used_history_rate": statistics.fmean(used_hist[i]) if used_hist.get(i) else None,
        }
        for i in sorted(set(lat) | set(cost))
    ]


def _table(header: list[str], body: list[list[str]]) -> list[str]:
    return (
        ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        + ["| " + " | ".join(row) + " |" for row in body]
    )


def write_report(
    experiment_name: str,
    experiment_url: Optional[str],
    rows: list[dict],
    metadata: dict[str, Any],
    ls_stats: Optional[dict[str, Any]] = None,
) -> tuple[Path, Path]:
    """Write ``evals/results/<experiment_name>.{json,md}``. Returns both paths."""
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
    lines: list[str] = [f"# Multi-turn eval report — `{experiment_name}`\n"]
    lines.append(f"Generated {payload['generated_at']}  ")
    if experiment_url:
        lines.append(f"LangSmith experiment: {experiment_url}  ")
    lines.append(f"Conversations: **{o['n']}**  ")
    for k, v in metadata.items():
        lines.append(f"{k}: `{v}`  ")
    lines.append("")

    lines.append("## Headline (all conversations)\n")
    notes = dict(NOTES)
    notes["latency_total_s"] = (
        f"whole-conversation wall clock; p50 {_fmt(o.get('latency_total_p50_s'))}s, "
        f"p95 {_fmt(o.get('latency_total_p95_s'))}s, max {_fmt(o.get('latency_total_max_s'))}s"
    )
    notes["cost_total_usd"] = (
        f"per conversation (local pricing table); total ${o.get('cost_sum_usd', 0) or 0:.4f}"
    )
    notes["ktokens_total"] = f"per conversation; total {o.get('tokens_sum')}"
    lines += _table(
        ["metric", "value", "note"],
        [[k, _fmt(o[k], k), notes.get(k, "")] for k in HEADLINE if k in o],
    )
    lines.append("")

    if agg["by_kind"]:
        lines.append("## By kind\n")
        cols = ["n", "safety_drift", "context_carryover", "consistency", "rubric_holds",
                "turn_behavior_match", "turn_groundedness", "latency_total_s", "cost_total_usd"]
        lines += _table(
            ["kind"] + cols,
            [[k] + [_fmt(a.get(c), c) for c in cols] for k, a in agg["by_kind"].items()],
        )
        lines.append("")

    if ls_stats and ls_stats.get("project"):
        p = ls_stats["project"]
        lines.append("## LangSmith-side aggregates (source of truth for cost)\n")
        lines.append(f"- runs: {p.get('run_count')} · root runs: {ls_stats.get('root_runs')}")
        lines.append(
            f"- total tokens: {p.get('total_tokens')} · total cost: ${(p.get('total_cost') or 0):.4f}"
            f" · per conversation: ${((p.get('total_cost') or 0) / max(ls_stats.get('root_runs') or 1, 1)):.4f}"
        )
        lines.append(
            f"- latency p50: {_fmt(p.get('latency_p50'))}s · p99: {_fmt(p.get('latency_p99'))}s"
            f" · error rate: {p.get('error_rate')}"
        )
        lines.append("")
        if ls_stats.get("by_stage_per_query"):
            lines.append("### Cost by pipeline stage (per *turn*, from the LangSmith run tree)\n")
            tot = sum(v["cost"] for v in ls_stats["by_stage_per_query"].values()) or 1
            lines += _table(
                ["stage", "LLM calls", "tokens", "cost", "share"],
                [
                    [k, f"{v['calls']:.2f}", f"{v['tokens']:.0f}", f"${v['cost']:.4f}", f"{100 * v['cost'] / tot:.0f}%"]
                    for k, v in ls_stats["by_stage_per_query"].items()
                ],
            )
            lines.append("")
            lines.append(
                "> The per-query denominator is the number of *root* runs in the project, which for "
                "this suite is one per conversation, not one per turn — divide by the mean turn count "
                "above to compare with the single-turn report.\n"
            )

    lines.append("## By category\n")
    cols = ["n", "safety_drift", "escalated_red_flags", "context_carryover", "consistency",
            "rubric_holds", "pii_persistence", "turn_behavior_match", "turn_correctness",
            "turn_groundedness", "turns_completed", "latency_total_s", "cost_total_usd"]
    lines += _table(
        ["category"] + cols,
        [[c] + [_fmt(a.get(k), k) for k in cols] for c, a in agg["by_category"].items()],
    )
    lines.append("")

    lines.append("## Per-turn profile (does a long session degrade?)\n")
    profile = agg["per_turn_profile"]
    if profile:
        lines += _table(
            ["turn", "conversations", "mean latency", "mean cost", "mean tokens", "answered", "used history"],
            [
                [
                    str(p["turn"]), str(p["n"]), f"{p['latency_s']:.2f}s" if p["latency_s"] is not None else "–",
                    f"${p['cost_usd']:.4f}" if p["cost_usd"] is not None else "–",
                    f"{p['tokens']:.0f}" if p["tokens"] is not None else "–",
                    _fmt(p["answered_rate"]), _fmt(p["used_history_rate"]),
                ]
                for p in profile
            ],
        )
    else:
        lines.append("_no turns recorded_")
    lines.append("")

    lines.append("## Per-conversation\n")
    header = ["id"] + [label for label, _ in PER_CONVERSATION_COLS]
    body = []
    for r in sorted(rows, key=lambda r: str(r.get("example_id"))):
        f = r["feedback"]
        cells = [str(r.get("example_id"))]
        for _, key in PER_CONVERSATION_COLS:
            if key == "category":
                cells.append(str(r.get("category") or "–"))
            elif key == "latency_total_s":
                cells.append(f"{_fmt(f.get(key), key)}s")
            elif key == "cost_total_usd":
                cells.append(_fmt(f.get(key), "est_cost_usd"))
            else:
                cells.append(_fmt(f.get(key), key))
        body.append(cells)
    lines += _table(header, body)
    lines.append("")

    lines.append("## Transcripts\n")
    for r in sorted(rows, key=lambda r: str(r.get("example_id"))):
        out = r.get("outputs") or {}
        lines.append(f"<details><summary><code>{r.get('example_id')}</code> — {r.get('title') or r.get('category')}</summary>\n")
        for t in out.get("turns") or []:
            user = (t.get("user") or "").replace("\n", " ")
            ans = (t.get("answer") or "(no answer produced)").replace("\n", " ")
            lines.append(f"**{t.get('index')}. user:** {user}  ")
            lines.append(f"**assistant** ({t.get('latency_s')}s): {ans}\n")
        lines.append("</details>\n")

    md_path = RESULTS_DIR / f"{experiment_name}.md"
    md_path.write_text("\n".join(lines))
    return json_path, md_path
