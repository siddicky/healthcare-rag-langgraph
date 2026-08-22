"""Canonical allowlist-aware Git cleanliness check used by eval seals."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_UNTRACKED_ROOTS: Final = frozenset(
    {".omo", ".claude", "dist", "__pycache__", ".pytest_cache"}
)
_REPORT_SUFFIXES: Final = frozenset({".json", ".md", ".log"})


@dataclass(frozen=True, slots=True)
class GitStatusError(Exception):
    cause: OSError | subprocess.CalledProcessError

    def __str__(self) -> str:
        return f"git status failed: {self.cause}"


def _is_exempt_untracked(path: str, root: Path) -> bool:
    normalized = path.removesuffix("/")
    if normalized.endswith(".py"):
        return False
    candidate = root / normalized
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return False
    if normalized.startswith("evals/results/"):
        return candidate.suffix.lower() in _REPORT_SUFFIXES
    parts = normalized.split("/")
    return parts[0] in _UNTRACKED_ROOTS or any(
        part in {"__pycache__", ".pytest_cache"} for part in parts
    )


def is_clean_status(status: str, root: Path | None = None) -> bool:
    """Return whether porcelain status contains only permitted untracked artifacts."""
    checkout = root or Path.cwd()
    for line in status.splitlines():
        if not line:
            continue
        if not line.startswith("?? "):
            return False
        if not _is_exempt_untracked(line[3:], checkout):
            return False
    return True


def check_clean(root: Path | None = None) -> bool:
    """Run Git's porcelain check, raising explicitly when Git cannot be executed."""
    checkout = root or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GitStatusError(exc) from exc
    return is_clean_status(result.stdout, checkout)
