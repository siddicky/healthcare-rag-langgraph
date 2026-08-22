#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# ─── How to run ───
# uv run python scripts/verify/f1_plan_compliance.py --plan <plan> --evidence-dir <dir>
# ──────────────────

from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final

from pydantic import BaseModel, ConfigDict

ROOT: Final = Path(__file__).parents[2]


@final
class CLIArgs(BaseModel):
    model_config = ConfigDict(frozen=True)
    plan: Path
    evidence_dir: Path


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    verify: Callable[[], tuple[bool, str]]


def _contains(path: str, pattern: str) -> tuple[bool, str]:
    text = (ROOT / path).read_text()
    return bool(re.search(pattern, text, re.MULTILINE)), path


def _evidence(plan: Path, evidence_dir: Path) -> tuple[bool, str]:
    todo_ids = sorted(
        {
            int(match.group(1))
            for match in re.finditer(
                r"^- \[[ x]\] (\d+)\.", plan.read_text(), re.MULTILINE
            )
        }
    )
    missing = [
        todo_id
        for todo_id in todo_ids
        if not (evidence_dir / f"task-{todo_id}-coach-agent-platform.txt").is_file()
        or not (evidence_dir / f"task-{todo_id}-coach-agent-platform.txt").read_text().strip()
    ]
    return not missing and todo_ids == list(range(1, 20)), f"todos={todo_ids}; missing={missing}"


def _catalog_refs() -> tuple[bool, str]:
    schemas = (ROOT / "frontend/src/catalog/schemas.ts").read_text()
    fact_lines = [line.strip() for line in schemas.splitlines() if "DataRefSchema" in line][1:]
    literals_absent = bool(fact_lines) and all("z.string" not in line for line in fact_lines)
    data_ref = (ROOT / "frontend/src/catalog/dataRef.ts").read_text()
    grammar = all(field in data_ref for field in ("turn_scope_id", "block_id", "pointer"))
    return literals_absent and grammar, f"fact_props={len(fact_lines)}; grammar={grammar}"


def _fixture_parity() -> tuple[bool, str]:
    commands = (
        ["uv", "run", "python", "-m", "pytest", "tests/test_catalog_data_ref_fixture.py", "-q"],
        ["bun", "run", "test", "src/catalog/__tests__/dataRef.fixture.test.ts"],
    )
    for command, cwd in ((commands[0], ROOT), (commands[1], ROOT / "frontend")):
        result = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            return False, result.stdout + result.stderr
    return True, "shared fixture accepted row-for-row by Pydantic and Zod"


def _upload_memory_only() -> tuple[bool, str]:
    forbidden = re.compile(r"\bopen\s*\(|Path\([^\n]*\)\.(?:write|open)|UploadFile|write_(?:bytes|text)")
    offenders = [
        path.name
        for path in (ROOT / "healthcare_rag/agent").glob("*.py")
        if path.name in {"uploads.py", "documents.py"} and forbidden.search(path.read_text())
    ]
    return not offenders, f"disk-write offenders={offenders}"


def _cron_import_scope() -> tuple[bool, str]:
    allowed = {"cron_client.py", "reminders.py"}
    offenders: list[str] = []
    for path in (ROOT / "healthcare_rag/agent").glob("*.py"):
        if path.name not in allowed and re.search(r"(?:from|import).*cron_client", path.read_text()):
            offenders.append(path.name)
    return not offenders, f"cron-client import offenders={offenders}"


def _member_cron_routes() -> tuple[bool, str]:
    offenders = [
        path.name
        for path in (
            ROOT / "healthcare_rag/agent/http_app.py",
            ROOT / "healthcare_rag/agent/perimeter.py",
        )
        if re.search(r"/runs/crons|runs/crons", path.read_text())
    ]
    return not offenders, f"member cron route offenders={offenders}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit coach-platform plan invariants")
    _ = parser.add_argument("--plan", type=Path, required=True)
    _ = parser.add_argument("--evidence-dir", type=Path, required=True)
    args = CLIArgs.model_validate(vars(parser.parse_args()))
    checks = (
        Check("all 19 todo evidence receipts", lambda: _evidence(args.plan, args.evidence_dir)),
        Check("catalog fact props are __ref-only", _catalog_refs),
        Check("Pydantic/Zod ref fixture parity", _fixture_parity),
        Check(
            "shared ToolCallLimitMiddleware",
            lambda: _contains(
                "healthcare_rag/agent/coach_agent.py",
                r"ToolCallLimitMiddleware[\s\S]*?run_limit\s*=\s*1[\s\S]*?\bcompose_ui\b",
            ),
        ),
        Check("uploads have no persistent byte-write path", _upload_memory_only),
        Check(
            "static-copy allow-list exists",
            lambda: ((ROOT / "healthcare_rag/agent/static_copy_allowlist.py").is_file(), "static_copy_allowlist.py"),
        ),
        Check("cron client imports are internal-only", _cron_import_scope),
        Check("member perimeter exposes no cron route", _member_cron_routes),
    )
    failed = False
    for check in checks:
        passed, detail = check.verify()
        print(f"{'PASS' if passed else 'FAIL'}: {check.name} — {detail}")
        failed = failed or not passed
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
