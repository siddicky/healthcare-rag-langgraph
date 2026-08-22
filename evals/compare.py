"""
Side-by-side comparison of experiment reports in evals/results/.

    uv run python -m evals.compare baseline-gpt4o-mini-25edbd33 luna-terra-73e65b69 luna-luna-xxxx
    uv run python -m evals.compare --latest 3            # the 3 most recent reports
    uv run python -m evals.compare A B --by-category     # add per-category tables

Prints a Markdown table (metric × experiment) with the delta of every column vs.
the first experiment given, and writes it to evals/results/compare-<names>.md.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .report import HEADLINE, RESULTS_DIR

LOWER_IS_BETTER = {"hallucinated", "forbidden_content", "pipeline_error", "latency_s",
                   "time_to_first_answer_s", "total_ktokens", "est_cost_usd", "llm_calls", "n_branches",
                   "latency_p50_s", "latency_p95_s", "est_cost_total_usd", "total_ktokens_sum"}
EXTRA = ["latency_p50_s", "latency_p95_s", "est_cost_total_usd", "total_ktokens_sum"]


def _load(name: str) -> dict:
    p = RESULTS_DIR / f"{name}.json"
    if not p.exists():
        raise SystemExit(f"no such report: {p}")
    return json.loads(p.read_text())


def _fmt(v, key):
    if v is None:
        return "–"
    if key.endswith("usd"):
        return f"${v:.4f}"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _delta(a, b, key):
    if a is None or b is None:
        return ""
    d = b - a
    if abs(d) < 1e-9:
        return "="
    good = (d < 0) if key in LOWER_IS_BETTER else (d > 0)
    arrow = "▲" if d > 0 else "▼"
    mark = "✅" if good else "❌"
    if key.endswith("usd"):
        return f"{arrow}{abs(d):.4f} {mark}"
    return f"{arrow}{abs(d):.2f} {mark}"


def build_table(reports: list[dict], group: str = "overall", cat: str | None = None) -> list[str]:
    def agg(r):
        return r["aggregate"]["overall"] if group == "overall" else r["aggregate"]["by_category"].get(cat, {})
    names = [r["experiment_name"] for r in reports]
    lines = ["| metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    keys = [k for k in HEADLINE + EXTRA if any(k in agg(r) for r in reports)]
    base = agg(reports[0])
    for k in keys:
        cells = []
        for i, r in enumerate(reports):
            v = agg(r).get(k)
            cell = _fmt(v, k)
            if i > 0:
                d = _delta(base.get(k), v, k)
                if d:
                    cell += f" ({d})"
            cells.append(cell)
        lines.append(f"| {k} | " + " | ".join(cells) + " |")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("experiments", nargs="*", help="experiment names (first one is the reference)")
    ap.add_argument("--latest", type=int, default=None, help="use the N most recent reports instead")
    ap.add_argument("--by-category", action="store_true")
    args = ap.parse_args()

    if args.latest:
        files = sorted(RESULTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        names = [p.stem for p in files if not p.stem.startswith("compare-")][-args.latest:]
    else:
        names = args.experiments
    if len(names) < 2:
        raise SystemExit("need at least two experiments")
    reports = [_load(n) for n in names]

    out: list[str] = [f"# Comparison — reference: `{names[0]}`\n"]
    for r in reports:
        md = r.get("metadata", {})
        out.append(f"- `{r['experiment_name']}` — llm={md.get('llm_model')} validator={md.get('validator_model')} "
                   f"n={r['aggregate']['overall'].get('n')} · {r.get('experiment_url') or ''}")
    out.append("\n## Overall\n")
    out += build_table(reports)
    if args.by_category:
        cats = sorted({c for r in reports for c in r["aggregate"]["by_category"]})
        for c in cats:
            out.append(f"\n## {c}\n")
            out += build_table(reports, group="category", cat=c)
    text = "\n".join(out)
    print(text)
    path = RESULTS_DIR / ("compare-" + "__".join(n.split("-")[0] for n in names) + ".md")
    path.write_text(text + "\n")
    print(f"\nwritten: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
