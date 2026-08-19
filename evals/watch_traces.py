"""
Watch a LangSmith tracing project and print one line per polling interval
summarising what arrived: run counts by name, errors (with the message), latency
p50/p95, token/cost totals. Designed to be piped into a monitor or a log.

    uv run python -m evals.watch_traces --project evaluators               # by name
    uv run python -m evals.watch_traces --project-id 563903ac-...            # by id
    uv run python -m evals.watch_traces --project healthcare-rag --interval 60 --once
    uv run python -m evals.watch_traces --project evaluators --errors-only

Each tick prints:
  [HH:MM:SS] <project> new=<n> err=<n> p50=<s> p95=<s> tokens=<n> cost=$<x> | by name: judge=12 …
and, for every failed run, an ERROR line with the run name, id and error text.
Also appends a JSONL record per tick to evals/results/trace-watch-<project>.jsonl so
the history survives the terminal.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import warnings
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client

load_dotenv()
warnings.filterwarnings("ignore", category=DeprecationWarning)

RESULTS_DIR = Path(__file__).parent / "results"


def _pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def tick(client: Client, project_name: str, since: datetime, errors_only: bool, log: Path) -> datetime:
    now = datetime.now(timezone.utc)
    runs = list(client.list_runs(project_name=project_name, start_time=since, is_root=True))
    by_name = Counter(r.name for r in runs)
    errs = [r for r in runs if r.error]
    lat = [(r.end_time - r.start_time).total_seconds() for r in runs if r.end_time and r.start_time]
    tokens = sum(r.total_tokens or 0 for r in runs)
    cost = sum(float(r.total_cost or 0) for r in runs)
    stamp = now.strftime("%H:%M:%S")
    if runs and not errors_only:
        names = ", ".join(f"{k}={v}" for k, v in by_name.most_common(6))
        print(
            f"[{stamp}] {project_name}: new={len(runs)} err={len(errs)} "
            f"p50={_pct(lat, .5):.1f}s p95={_pct(lat, .95):.1f}s tokens={tokens} cost=${cost:.4f} | {names}",
            flush=True,
        )
    for r in errs:
        print(f"[{stamp}] ERROR {project_name}: {r.name} run={r.id} :: {str(r.error)[:300]}", flush=True)
    if runs:
        with log.open("a") as f:
            f.write(json.dumps({
                "ts": now.isoformat(), "since": since.isoformat(), "project": project_name,
                "new_runs": len(runs), "errors": len(errs), "by_name": dict(by_name),
                "latency_p50": _pct(lat, .5), "latency_p95": _pct(lat, .95),
                "tokens": tokens, "cost_usd": cost,
                "error_runs": [{"id": str(r.id), "name": r.name, "error": str(r.error)[:500]} for r in errs],
            }) + "\n")
    return now


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="tracing project name")
    ap.add_argument("--project-id", help="tracing project id (alternative to --project)")
    ap.add_argument("--interval", type=int, default=60, help="seconds between polls")
    ap.add_argument("--lookback", type=int, default=600, help="seconds of history to include on the first tick")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--errors-only", action="store_true", help="print only ERROR lines")
    args = ap.parse_args()

    client = Client()
    if args.project_id:
        project_name = client.read_project(project_id=args.project_id).name
    elif args.project:
        project_name = args.project
    else:
        ap.error("--project or --project-id required")
    RESULTS_DIR.mkdir(exist_ok=True)
    log = RESULTS_DIR / f"trace-watch-{project_name}.jsonl"
    since = datetime.now(timezone.utc) - timedelta(seconds=args.lookback)
    print(f"watching LangSmith project '{project_name}' every {args.interval}s (log: {log})", flush=True)
    while True:
        try:
            since = tick(client, project_name, since, args.errors_only, log)
        except Exception as exc:  # keep watching through transient API errors
            print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] WATCH-ERROR {exc}", file=sys.stderr, flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
