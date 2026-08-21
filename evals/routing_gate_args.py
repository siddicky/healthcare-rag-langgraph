from __future__ import annotations

import argparse
from pathlib import Path
from typing import Literal, TypeAlias

Lane: TypeAlias = Literal["query", "safety"]


class GateArgs(argparse.Namespace):
    lane: Lane
    stage: Literal["1", "all"]
    repetitions: int
    concurrency: int
    report_name: str | None
    fixture: Path | None
    smoke: bool
    json: bool

    def __init__(self) -> None:
        super().__init__()
        self.lane, self.stage = "query", "all"
        self.repetitions, self.concurrency = 2, 1
        self.report_name, self.fixture = None, None
        self.smoke, self.json = False, False
