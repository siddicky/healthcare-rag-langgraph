"""
Run the golden dataset through the real pipeline as a LangSmith experiment and
write a local report.

    # full run, serial (cleanest latency numbers)
    uv run python -m evals.run_baseline

    # faster, noisier latency
    uv run python -m evals.run_baseline --concurrency 4

    # subset / smoke
    uv run python -m evals.run_baseline --limit 5 --category factual_single
    uv run python -m evals.run_baseline --no-judges          # deterministic metrics only
    uv run python -m evals.run_baseline --prefix after-fix   # name the experiment

Requires: Weaviate up + ingested, OPENAI_API_KEY, LANGSMITH_API_KEY (+ LANGSMITH_TRACING=true
to get the per-stage cost breakdown; the experiment itself is always uploaded).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import warnings
from typing import Any

from dotenv import load_dotenv
from langsmith import Client, aevaluate

from .dataset import DEFAULT_DATASET_NAME, load_golden, sync_dataset
from .evaluators import ALL_EVALUATORS, DETERMINISTIC_EVALUATORS, JUDGE_MODEL, judge_usage_summary
from .engines import build_engine
from .harness import make_target
from .pricing import PRICING_AS_OF
from healthcare_rag.services.models import disabled_stages, default_reasoning_effort
from .report import fetch_langsmith_stats, write_report
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


def _chunk_hashes() -> dict[str, str]:
    """Hash the chunk files so chunk_recall drops can be attributed to re-ingestion."""
    out = {}
    for name in ("data/chunks_lipitor.json", "data/chunks_metformin.json"):
        try:
            out[name] = hashlib.sha256(open(name, "rb").read()).hexdigest()[:12]
        except OSError:
            out[name] = "missing"
    return out


def _parse_gates(specs: list[tuple[str, str]]) -> dict[str, tuple[str, float]]:
    """--fail-under safe_redirect=0.8  /  --fail-over hallucinated=0.2 → {key: (op, threshold)}"""
    gates = {}
    for spec, op in specs:
        k, v = spec.split("=", 1)
        gates[k.strip()] = (op, float(v))
    return gates


def _git_dirty() -> bool:
    try:
        return not check_clean()
    except GitStatusError:
        return True


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=DEFAULT_DATASET_NAME)
    ap.add_argument("--prefix", default="baseline", help="experiment name prefix")
    ap.add_argument("--concurrency", type=int, default=1, help="parallel examples (1 = cleanest latency)")
    ap.add_argument("--limit", type=int, default=None, help="only run the first N examples (after filtering)")
    ap.add_argument("--category", action="append", default=None, help="filter by category (repeatable)")
    ap.add_argument("--example-id", action="append", default=None, help="filter by golden example id (repeatable)")
    ap.add_argument("--split", action="append", default=None, help="filter by split, e.g. core / holdout (repeatable)")
    ap.add_argument("--no-judges", action="store_true", help="skip LLM-as-judge evaluators")
    ap.add_argument("--repetitions", type=int, default=1)
    ap.add_argument("--no-sync", action="store_true", help="don't upsert golden_dataset.json to LangSmith first")
    ap.add_argument("--fail-under", action="append", default=[], metavar="KEY=MIN",
                    help="exit 1 if the overall mean of KEY is below MIN (CI gate; repeatable), e.g. safe_redirect=0.8")
    ap.add_argument("--fail-over", action="append", default=[], metavar="KEY=MAX",
                    help="exit 1 if the overall mean of KEY is above MAX, e.g. hallucinated=0.2 or est_cost_usd=0.05")
    args = ap.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY missing (put it in .env)", file=sys.stderr)
        return 2
    if not os.getenv("LANGSMITH_API_KEY"):
        print("LANGSMITH_API_KEY missing (put it in .env)", file=sys.stderr)
        return 2
    if not os.getenv("LANGSMITH_TRACING", "").lower() in {"1", "true"}:
        print("note: LANGSMITH_TRACING is not 'true' — per-stage cost breakdown will be empty", file=sys.stderr)

    client = Client()

    if not args.no_sync:
        ds, created, updated = sync_dataset(client, args.dataset)
        print(f"dataset '{ds.name}': created={created} updated={updated}")
    else:
        ds = client.read_dataset(dataset_name=args.dataset)

    # Select examples (filter on metadata we control).
    golden = {r["id"]: r for r in load_golden()}
    examples = list(client.list_examples(dataset_id=ds.id))
    def keep(e: Any) -> bool:
        md = e.metadata or {}
        if args.category and md.get("category") not in args.category:
            return False
        if args.example_id and md.get("example_id") not in args.example_id:
            return False
        if args.split and md.get("split", "core") not in args.split:
            return False
        return True
    examples = sorted((e for e in examples if keep(e)), key=lambda e: (e.metadata or {}).get("example_id", ""))
    if args.limit:
        examples = examples[: args.limit]
    if not examples:
        print("no examples selected", file=sys.stderr)
        return 1
    print(f"running {len(examples)} examples, concurrency={args.concurrency}, judges={'off' if args.no_judges else JUDGE_MODEL}")

    engine = await build_engine()
    target = make_target(engine)
    evaluators = DETERMINISTIC_EVALUATORS if args.no_judges else ALL_EVALUATORS

    metadata = {
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "judge_model": None if args.no_judges else JUDGE_MODEL,
        "reasoning_effort": default_reasoning_effort(),
        "disabled_stages": sorted(disabled_stages()) or None,
        "concurrency": args.concurrency,
        "pricing_as_of": PRICING_AS_OF,
        "n_examples": len(examples),
        "split": args.split,
        "categories": args.category,
        "chunk_file_hashes": _chunk_hashes(),
        **engine.describe(),
    }

    try:
        results = await aevaluate(
            target,
            data=examples,
            evaluators=evaluators,
            experiment_prefix=args.prefix,
            description="Healthcare RAG golden-set evaluation (see evals/README.md)",
            metadata=metadata,
            max_concurrency=args.concurrency,
            num_repetitions=args.repetitions,
            client=client,
        )
        experiment_name = results.experiment_name
        rows: list[dict[str, Any]] = []
        async for r in results:
            ex = r["example"]
            run = r["run"]
            md = ex.metadata or {}
            feedback = {}
            for er in r["evaluation_results"].get("results", []):
                feedback[er.key] = er.score
            outputs = run.outputs or {}
            rows.append(
                {
                    "example_id": md.get("example_id"),
                    "category": md.get("category"),
                    "split": md.get("split", "core"),
                    "drug": md.get("drug"),
                    "expected_behavior": md.get("expected_behavior"),
                    "question": (ex.inputs or {}).get("question"),
                    "outputs": {k: v for k, v in outputs.items() if k not in {"per_call_usage"}},
                    "feedback": feedback,
                    "run_id": str(run.id),
                    "error": run.error,
                }
            )
    finally:
        await engine.aclose()

    # Experiment URL
    try:
        proj = client.read_project(project_name=experiment_name)
        experiment_url = proj.url
    except Exception:
        experiment_url = None

    print("fetching LangSmith-side stats (cost/tokens/per-stage)…")
    try:
        ls_stats = fetch_langsmith_stats(client, experiment_name)
    except Exception as exc:
        log.warning("could not fetch LangSmith stats: %s", exc)
        ls_stats = None

    if not args.no_judges:
        metadata["judge_usage"] = judge_usage_summary()
    if ls_stats and ls_stats.get("root_runs") is not None and ls_stats["root_runs"] != len(rows):
        print(f"WARNING: LangSmith shows {ls_stats['root_runs']} root runs but {len(rows)} examples ran locally — "
              "some run ingests were rejected; check the log for 'Failed to send'. Local rows are authoritative.", file=sys.stderr)

    json_path, md_path = write_report(experiment_name, experiment_url, rows, metadata, ls_stats)
    print(f"\nexperiment: {experiment_name}\nurl: {experiment_url}\nreport: {md_path}\njson: {json_path}\n")
    # Print the headline table to stdout for quick reading.
    print(md_path.read_text().split("## By category")[0])

    # CI gates
    gates = _parse_gates([(g, "under") for g in args.fail_under] + [(g, "over") for g in args.fail_over])
    if gates:
        overall = json.loads(json_path.read_text())["aggregate"]["overall"]
        failed = []
        for k, (op, thr) in gates.items():
            v = overall.get(k)
            if v is None:
                failed.append(f"{k}: no value")
            elif op == "under" and v < thr:
                failed.append(f"{k}={v:.3f} < {thr}")
            elif op == "over" and v > thr:
                failed.append(f"{k}={v:.3f} > {thr}")
        if failed:
            print("GATE FAILED: " + "; ".join(failed), file=sys.stderr)
            return 1
        print("all gates passed: " + ", ".join(f"{k} {op} {thr}" for k, (op, thr) in gates.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
