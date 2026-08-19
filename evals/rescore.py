"""
Re-score an existing LangSmith experiment without re-running the pipeline, then
rebuild its local report from LangSmith feedback.

Use this when you add or fix an evaluator and want every past experiment to
carry the new metric (so before/after comparisons stay apples-to-apples), or
just to regenerate a report:

    uv run python -m evals.rescore --experiment baseline-gpt4o-mini-25edbd33 \
        --evaluator forbidden_content --evaluator false_premise_judge
    uv run python -m evals.rescore --experiment <name> --judges        # all LLM judges (e.g. after a judge-model swap)
    uv run python -m evals.rescore --experiment luna-terra-abc123 --report-only

Evaluator names are function names in evals/evaluators.py. By default existing
feedback for the selected keys is DELETED first so the new score fully replaces
the old one (pass --append to keep both; LangSmith then averages them).
Local run outputs (evals/results/<experiment>.json) are authoritative; LangSmith
feedback is overlaid on top.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import warnings
from typing import Any

from dotenv import load_dotenv
from langsmith import Client, aevaluate

from . import evaluators as ev
from .dataset import DEFAULT_DATASET_NAME, sync_dataset
from .report import RESULTS_DIR, fetch_langsmith_stats, write_report

load_dotenv()
warnings.filterwarnings("ignore", category=DeprecationWarning)


def _local_rows(experiment_name: str) -> dict[str, dict]:
    """Rows captured locally by run_baseline (authoritative for outputs/latency/usage —
    LangSmith may be missing a run's outputs if its ingest batch was rejected)."""
    p = RESULTS_DIR / f"{experiment_name}.json"
    if not p.exists():
        return {}
    try:
        return {r["example_id"]: r for r in json.loads(p.read_text()).get("rows", []) if r.get("example_id")}
    except Exception:
        return {}


def rows_from_langsmith(client: Client, experiment_name: str, dataset_name: str) -> tuple[list[dict], dict[str, Any]]:
    proj = client.read_project(project_name=experiment_name)
    ds = client.read_dataset(dataset_name=dataset_name)
    examples = {e.id: e for e in client.list_examples(dataset_id=ds.id)}
    local = _local_rows(experiment_name)
    rows: list[dict] = []
    for run in client.list_runs(project_name=experiment_name, is_root=True):
        ex = examples.get(run.reference_example_id)
        md = (ex.metadata if ex else None) or {}
        fb = {}
        for key, st in (run.feedback_stats or {}).items():
            fb[key] = st.get("avg") if isinstance(st, dict) else None
        outputs = run.outputs or {}
        loc = local.get(md.get("example_id") or "")
        if loc:
            # Prefer local outputs; overlay LangSmith feedback on top of local feedback so
            # nothing captured at run time is lost when a run's ingest failed server-side.
            outputs = loc.get("outputs") or outputs
            fb = {**(loc.get("feedback") or {}), **fb}
        rows.append(
            {
                "example_id": md.get("example_id"),
                "category": md.get("category"),
                "split": md.get("split", "core"),
                "drug": md.get("drug"),
                "expected_behavior": md.get("expected_behavior"),
                "question": (ex.inputs or {}).get("question") if ex else (run.inputs or {}).get("question"),
                "outputs": {k: v for k, v in outputs.items() if k != "per_call_usage"},
                "feedback": fb,
                "run_id": str(run.id),
                "error": run.error,
            }
        )
    metadata = dict(proj.extra.get("metadata", {})) if getattr(proj, "extra", None) else {}
    metadata["rescored"] = True
    metadata["judge_model_at_rescore"] = ev.JUDGE_MODEL
    return rows, metadata


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--experiment", required=True, help="experiment (project) name")
    ap.add_argument("--dataset", default=DEFAULT_DATASET_NAME)
    ap.add_argument("--evaluator", action="append", default=[], help="evaluator function name (repeatable)")
    ap.add_argument("--report-only", action="store_true", help="skip scoring; just rebuild the report")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--append", action="store_true",
                    help="keep existing feedback for the selected keys and ADD new votes (LangSmith then averages them). "
                         "Default is to replace, so a fixed judge fully supersedes the old one.")
    ap.add_argument("--sync", action="store_true",
                    help="upsert golden_dataset.json to LangSmith first (off by default: experiments pin their "
                         "dataset version, and evaluators should read example.metadata for per-example fields)")
    ap.add_argument("--judges", action="store_true", help="shorthand: all LLM-judge evaluators")
    args = ap.parse_args()
    if args.judges:
        args.evaluator += [f.__name__ for f in ev.JUDGE_EVALUATORS]

    client = Client()
    if args.sync:
        sync_dataset(client, args.dataset)

    if not args.report_only:
        fns = []
        for name in args.evaluator:
            fn = getattr(ev, name, None)
            if fn is None:
                raise SystemExit(f"unknown evaluator: {name}")
            fns.append(fn)
        if not fns:
            raise SystemExit("pass at least one --evaluator, or --report-only")
        if not args.append:
            keys = sorted({k for f in fns for k in ev.EVALUATOR_KEYS.get(f.__name__, [f.__name__])})
            run_ids = [r.id for r in client.list_runs(project_name=args.experiment, is_root=True)]
            n = 0
            for fb in client.list_feedback(run_ids=run_ids, feedback_key=keys):
                client.delete_feedback(fb.id)
                n += 1
            print(f"deleted {n} existing feedback entries for keys {keys}")
        print(f"re-scoring '{args.experiment}' with {[f.__name__ for f in fns]} (judge={ev.JUDGE_MODEL}) …")
        results = await aevaluate(args.experiment, evaluators=fns, max_concurrency=args.concurrency, client=client)
        async for _ in results:  # drain
            pass

    rows, metadata = rows_from_langsmith(client, args.experiment, args.dataset)
    if not rows:
        raise SystemExit("no root runs found for that experiment")
    proj = client.read_project(project_name=args.experiment)
    ls_stats = fetch_langsmith_stats(client, args.experiment)
    json_path, md_path = write_report(args.experiment, proj.url, rows, metadata, ls_stats)
    print(f"report: {md_path}\njson: {json_path}")
    print(md_path.read_text().split("## By category")[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
