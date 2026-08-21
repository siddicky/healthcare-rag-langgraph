#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["langsmith>=0.4,<1"]
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Export LANGSMITH_API_KEY for the target workspace.
# 3. Run: uv run python scripts/provision_feedback_project.py
# ──────────────────

from __future__ import annotations

import sys
from typing import Final
from uuid import UUID

from langsmith import Client
from langsmith.utils import LangSmithNotFoundError

PROJECT_NAME: Final = "nymble-coach-feedback"


def provision(client: Client) -> UUID:
    """Create the dedicated feedback project once and verify its stable UUID."""
    try:
        project = client.read_project(project_name=PROJECT_NAME)
    except LangSmithNotFoundError:
        project = client.create_project(PROJECT_NAME)
    project_id = UUID(str(project.id))
    verified = client.read_project(project_id=str(project_id))
    if verified.name != PROJECT_NAME or UUID(str(verified.id)) != project_id:
        raise RuntimeError("LangSmith feedback project verification failed")
    return project_id


def main() -> int:
    project_id = provision(Client())
    print(f"LANGSMITH_FEEDBACK_PROJECT_ID={project_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
