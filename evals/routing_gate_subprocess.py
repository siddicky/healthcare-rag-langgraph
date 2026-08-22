from __future__ import annotations

import os
import shlex
import subprocess
import sys
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from evals.routing_gate_runner import ArmRunRequest, ArmRunResult, RunnerError


class ChildFailure(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    status: Literal["ERROR"]
    detail: str


class SubprocessRoutingGateRunner:
    def __init__(self, command: tuple[str, ...] | None = None) -> None:
        configured = os.getenv("HC_RAG_ROUTING_RUNNER_COMMAND", "").strip()
        self._command = command or (
            tuple(shlex.split(configured))
            if configured
            else (sys.executable, "-m", "evals.routing_arm_runner")
        )

    def run_arm(self, request: ArmRunRequest) -> ArmRunResult:
        command = (
            *self._command,
            "--lane",
            request.lane,
            "--arm",
            request.arm,
            "--stage",
            request.stage,
            "--repetitions",
            str(request.repetitions),
            "--concurrency",
            str(request.concurrency),
            "--report-name",
            request.report_name,
            "--json",
        )
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=1800,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RunnerError(f"routing arm runner failed: {exc}") from exc
        if completed.returncode != 0:
            lines = completed.stdout.splitlines()
            if lines:
                try:
                    failure = ChildFailure.model_validate_json(lines[-1])
                except ValidationError:
                    failure = None
                if failure is not None:
                    raise RunnerError(failure.detail)
            raise RunnerError(
                f"routing arm runner exited {completed.returncode}: {completed.stderr.strip()}"
            )
        lines = completed.stdout.splitlines()
        if not lines:
            raise RunnerError("routing arm runner produced no JSON result")
        try:
            return ArmRunResult.model_validate_json(lines[-1])
        except ValidationError as exc:
            raise RunnerError(f"malformed routing arm result: {exc}") from exc
