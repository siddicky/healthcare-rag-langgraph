"""
Run the multi-turn conversation set through the real pipeline as a LangSmith
experiment and write a local report.

    # full run, serial (cleanest latency numbers — and latency growth is a metric here)
    uv run python -m evals.run_multiturn

    # subset / smoke
    uv run python -m evals.run_multiturn --limit 2 --no-judges
    uv run python -m evals.run_multiturn --kind scripted --category context_carryover
    uv run python -m evals.run_multiturn --conversation-id mt-001
    uv run python -m evals.run_multiturn --prefix after-fix        # name the experiment

    # score a file that is not yet checked in (e.g. a draft dataset)
    uv run python -m evals.run_multiturn --dataset-file /tmp/draft.json --no-sync

Each example is one *conversation*: the target plays every turn against a fresh
``user_id`` so ``rag.conversation_history`` accumulates exactly as it does for a
real user, then the evaluators score the whole trajectory.

Requires: Weaviate up + ingested, OPENAI_API_KEY, LANGSMITH_API_KEY (+
LANGSMITH_TRACING=true for the per-stage cost breakdown).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langsmith import Client, aevaluate

from healthcare_rag.services.models import default_reasoning_effort, disabled_stages

from .engines import build_engine
from .evaluators import JUDGE_MODEL
from .multiturn_dataset import DEFAULT_DATASET_NAME, MULTITURN_PATH, sync_dataset
from .multiturn_evaluators import ALL_EVALUATORS, DETERMINISTIC_EVALUATORS
from .multiturn_harness import SIM_USER_MODEL, make_target
from .multiturn_report import write_report
from .pricing import PRICING_AS_OF
from .report import fetch_langsmith_stats
from .seal_clean import GitStatusError, check_clean

load_dotenv()
warnings.filterwarnings("ignore", category=DeprecationWarning)
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("MedicalRAG").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
log = logging.getLogger("evals")


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    try:
        return not check_clean()
    except GitStatusError:
        return True


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=DEFAULT_DATASET_NAME, help="LangSmith dataset name")
    ap.add_argument("--dataset-file", default=str(MULTITURN_PATH), help="local conversation file to sync/run")
    ap.add_argument("--prefix", default="multiturn", help="experiment name prefix")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="parallel conversations (1 = cleanest latency; latency growth is a metric here)")
    ap.add_argument("--limit", type=int, default=None, help="only run the first N conversations (after filtering)")
    ap.add_argument("--kind", choices=["scripted", "simulated"], default=None, help="filter by conversation kind")
    ap.add_argument("--category", action="append", default=None, help="filter by category (repeatable)")
    ap.add_argument("--conversation-id", action="append", default=None, help="filter by conversation id (repeatable)")
    ap.add_argument("--split", action="append", default=None, help="filter by split, e.g. core / holdout (repeatable)")
    ap.add_argument("--no-judges", action="store_true", help="skip LLM-as-judge evaluators")
    ap.add_argument("--repetitions", type=int, default=1)
    ap.add_argument("--no-sync", action="store_true", help="don't upsert the conversation file to LangSmith first")
    return ap


async def main() -> int:
    args = _build_parser().parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY missing (put it in .env)", file=sys.stderr)
        return 2
    if not os.getenv("LANGSMITH_API_KEY"):
        print("LANGSMITH_API_KEY missing (put it in .env)", file=sys.stderr)
        return 2
    if os.getenv("LANGSMITH_TRACING", "").lower() not in {"1", "true"}:
        print("note: LANGSMITH_TRACING is not 'true' — per-stage cost breakdown will be empty", file=sys.stderr)

    dataset_file = Path(args.dataset_file)
    if not args.no_sync and not dataset_file.exists():
        print(f"conversation file not found: {dataset_file}\n"
              f"Create evals/multiturn_dataset.json, or pass --dataset-file / --no-sync.", file=sys.stderr)
        return 2

    client = Client()

    if not args.no_sync:
        ds, created, updated = sync_dataset(client, args.dataset, dataset_file)
        print(f"dataset '{ds.name}': created={created} updated={updated}")
    else:
        ds = client.read_dataset(dataset_name=args.dataset)

    examples = list(client.list_examples(dataset_id=ds.id))

    def keep(e: Any) -> bool:
        md = e.metadata or {}
        if args.kind and md.get("kind") != args.kind:
            return False
        if args.category and md.get("category") not in args.category:
            return False
        if args.conversation_id and md.get("example_id") not in args.conversation_id:
            return False
        if args.split and md.get("split", "core") not in args.split:
            return False
        return True

    examples = sorted((e for e in examples if keep(e)), key=lambda e: (e.metadata or {}).get("example_id", ""))
    if args.limit:
        examples = examples[: args.limit]
    if not examples:
        print("no conversations selected", file=sys.stderr)
        return 1

    n_turns_total = sum((e.metadata or {}).get("n_turns") or 0 for e in examples)
    print(f"running {len(examples)} conversations (~{n_turns_total} turns), "
          f"concurrency={args.concurrency}, judges={'off' if args.no_judges else JUDGE_MODEL}")

    engine = await build_engine()
    target = make_target(engine)
    evaluators = DETERMINISTIC_EVALUATORS if args.no_judges else ALL_EVALUATORS

    has_simulated = any((e.metadata or {}).get("kind") == "simulated" for e in examples)
    metadata = {
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "judge_model": None if args.no_judges else JUDGE_MODEL,
        "sim_user_model": SIM_USER_MODEL if has_simulated else None,
        "reasoning_effort": default_reasoning_effort(),
        "disabled_stages": sorted(disabled_stages()) or None,
        "concurrency": args.concurrency,
        "pricing_as_of": PRICING_AS_OF,
        "n_conversations": len(examples),
        "n_turns_planned": n_turns_total,
        "kind": args.kind,
        "split": args.split,
        "categories": args.category,
        **engine.describe(),
    }

    try:
        results = await aevaluate(
            target,
            data=examples,
            evaluators=evaluators,
            experiment_prefix=args.prefix,
            description="Healthcare RAG multi-turn conversation evaluation (see evals/README.md)",
            metadata=metadata,
            max_concurrency=args.concurrency,
            num_repetitions=args.repetitions,
            client=client,
        )
        experiment_name = results.experiment_name
        rows: list[dict[str, Any]] = []
        async for r in results:
            ex, run = r["example"], r["run"]
            md = ex.metadata or {}
            feedback = {
                er.key: er.score
                for er in r["evaluation_results"].get("results", [])
            }
            outputs = run.outputs or {}
            rows.append(
                {
                    "example_id": md.get("example_id"),
                    "category": md.get("category"),
                    "kind": md.get("kind"),
                    "split": md.get("split", "core"),
                    "title": md.get("title"),
                    "n_turns_expected": md.get("n_turns"),
                    "outputs": _slim(outputs),
                    "feedback": feedback,
                    "run_id": str(run.id),
                    "error": run.error,
                }
            )
    finally:
        await engine.aclose()

    try:
        experiment_url = client.read_project(project_name=experiment_name).url
    except Exception:
        experiment_url = None

    print("fetching LangSmith-side stats (cost/tokens/per-stage)…")
    try:
        ls_stats = fetch_langsmith_stats(client, experiment_name)
    except Exception as exc:
        log.warning("could not fetch LangSmith stats: %s", exc)
        ls_stats = None

    json_path, md_path = write_report(experiment_name, experiment_url, rows, metadata, ls_stats)
    print(f"\nexperiment: {experiment_name}\nurl: {experiment_url}\nreport: {md_path}\njson: {json_path}\n")
    print(md_path.read_text().split("## By category")[0])
    return 0


def _slim(outputs: dict[str, Any]) -> dict[str, Any]:
    """Drop the retrieved chunk *text* from the local report payload.

    Contexts are several KB per turn and a conversation has many turns; the chunk
    ids, pages and sources are kept, and the full text stays in the LangSmith run.
    """
    slim = dict(outputs)
    turns = []
    for t in slim.get("turns") or []:
        t = dict(t)
        t.pop("contexts", None)
        turns.append(t)
    if turns:
        slim["turns"] = turns
    return slim


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
