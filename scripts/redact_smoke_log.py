"""Redact a deployed-smoke log before it is uploaded as a workflow artifact.

The smoke runs against production with real bearer tokens, so its raw output
never leaves the runner. This strips credential-shaped text and truncates long
bodies to `status + length`, which is all the artifact is for.

Lives here rather than inline in `.github/workflows/deploy.yml` because both the
tag deploy and the rollback job redact the same way, and because a heredoc
inside YAML cannot be tested. Standard library only: it runs on the runner's
`python3`, not through uv.

    python3 scripts/redact_smoke_log.py --in raw.log --out redacted.log
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Final

# Header values are dropped wholesale — the name stays so the log still shows
# which headers were sent.
_HEADER_PATTERNS: Final = (
    (re.compile(r"(?i)(authorization:\s*)\S.*"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(x-api-key:\s*)\S.*"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(x-internal-token:\s*)\S.*"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(x-internal-owner:\s*)\S.*"), r"\1[REDACTED]"),
)

# Credential shapes that can appear outside a header (echoed env, error bodies).
_VALUE_PATTERNS: Final = (
    (re.compile(r"lsv2_[A-Za-z0-9_\-]+"), "[REDACTED_API_KEY]"),
    (re.compile(r"sbp_[A-Za-z0-9_\-]+"), "[REDACTED_SUPABASE_KEY]"),
    (
        re.compile(r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
        "[REDACTED_JWT]",
    ),
    (re.compile(r"(LANGGRAPH_U[12]_TOKEN=)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(COACH_INTERNAL_TOKEN=)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(OPENAI_API_KEY=)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(SUPABASE_SERVICE_KEY=)\S+"), r"\1[REDACTED]"),
)

LINE_LIMIT: Final = 500
LINE_KEEP: Final = 300


def redact(text: str) -> str:
    """Strip credentials from `text` and truncate over-long lines."""
    for pattern, replacement in _HEADER_PATTERNS + _VALUE_PATTERNS:
        text = pattern.sub(replacement, text)
    lines = [
        line if len(line) <= LINE_LIMIT else f"{line[:LINE_KEEP]} [TRUNCATED len={len(line)}]"
        for line in text.splitlines()
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--in", dest="src", required=True, type=Path)
    _ = parser.add_argument("--out", dest="dst", required=True, type=Path)
    args = parser.parse_args()

    src: Path = args.src
    dst: Path = args.dst
    raw = src.read_text(errors="replace") if src.exists() else "no smoke log produced"
    redacted = redact(raw)
    _ = dst.write_text(redacted)
    print(f"Redacted log: {len(redacted)} chars, {len(redacted.splitlines())} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
