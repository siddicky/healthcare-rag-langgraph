#!/usr/bin/env python3
"""Exit 0 for a sealed checkout, 1 for dirtiness, and 2 for Git errors."""

from __future__ import annotations

import sys

from evals.seal_clean import GitStatusError, check_clean


def main() -> int:
    try:
        return 0 if check_clean() else 1
    except GitStatusError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
