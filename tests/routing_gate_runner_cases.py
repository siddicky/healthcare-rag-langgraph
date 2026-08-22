from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from evals.routing_gate import main
from evals.routing_gate_models import ArmName
from evals.routing_gate_runner import ArmRunRequest, ArmRunResult
from tests.routing_gate_cases import manifest, report
from tests.routing_gate_fixtures import full, query_stage1, safety_metrics


class FakeRunner:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.calls: list[ArmRunRequest] = []

    def run_arm(self, request: ArmRunRequest) -> ArmRunResult:
        self.calls.append(request)
        arm_manifest = manifest(request.arm).model_copy(
            update={
                "experiment_name": request.report_name,
                "experiment_url": f"https://smith.langchain.com/{request.report_name}",
                "repetitions": request.repetitions,
                "concurrency": request.concurrency,
            }
        )
        if request.lane == "query":
            behavior = 0.83 if request.arm == "tool+llm" else 0.80
            chat = 0.83 if request.arm == "tool+llm" else 0.80
            stage2 = full(behavior_match=behavior, chit_chat_quality=chat)
        else:
            macro_f1 = 0.83 if request.arm == "current+semantic_router" else 0.80
            stage2 = full(safety_macro_f1=macro_f1)
        return ArmRunResult(
            report=report(arm_manifest, self.output_dir),
            query_stage1=query_stage1(),
            safety_residual=safety_metrics(),
            safety_full_shell=safety_metrics(),
            stage2=None if request.stage == "1" else stage2,
        )


@dataclass(frozen=True, slots=True)
class RejectingRunner:
    base: FakeRunner

    def run_arm(self, request: ArmRunRequest) -> ArmRunResult:
        result = self.base.run_arm(request)
        if request.arm == "tool+llm" and request.stage == "1":
            return result.model_copy(
                update={"query_stage1": query_stage1(forbidden_direct_count=1)}
            )
        return result


@dataclass(frozen=True, slots=True)
class MissingStage2Runner:
    base: FakeRunner

    def run_arm(self, request: ArmRunRequest) -> ArmRunResult:
        return self.base.run_arm(request).model_copy(update={"stage2": None})


@dataclass(frozen=True, slots=True)
class ContaminatedRunner:
    base: FakeRunner

    def run_arm(self, request: ArmRunRequest) -> ArmRunResult:
        result = self.base.run_arm(request)
        if request.stage == "2":
            provenance = result.report.provenance.model_copy(
                update={"git_sha": "2" * 40}
            )
            return result.model_copy(
                update={
                    "report": result.report.model_copy(
                        update={"provenance": provenance}
                    )
                }
            )
        return result


@dataclass(frozen=True, slots=True)
class SettingsMismatchRunner:
    base: FakeRunner

    def run_arm(self, request: ArmRunRequest) -> ArmRunResult:
        result = self.base.run_arm(request)
        provenance = result.report.provenance.model_copy(
            update={"repetitions": 2, "concurrency": 1}
        )
        return result.model_copy(
            update={
                "report": result.report.model_copy(update={"provenance": provenance})
            }
        )


@pytest.mark.parametrize(
    ("lane", "expected"),
    [
        ("query", ("current+llm", "deterministic+llm", "tool+llm")),
        ("safety", ("current+llm", "current+semantic_router")),
    ],
)
def assert_non_smoke_cli_runs_exact_arms_and_writes_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    lane: str,
    expected: tuple[ArmName, ...],
) -> None:
    runner = FakeRunner(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "routing_gate",
            "--lane",
            lane,
            "--stage",
            "all",
            "--repetitions",
            "2",
            "--concurrency",
            "1",
            "--report-name",
            "real-fixture",
            "--json",
        ],
    )
    exit_code = main(runner)
    captured = capsys.readouterr()
    assert exit_code == 0
    assert tuple(call.arm for call in runner.calls) == (*expected, *expected)
    assert tuple(call.stage for call in runner.calls) == (
        *("1" for _ in expected),
        *("2" for _ in expected),
    )
    assert all(call.report_name.startswith("real-fixture-") for call in runner.calls)
    assert json.loads(captured.out)["verdict"] == "ADOPT"
    assert (tmp_path / "real-fixture.json").exists()
    assert (tmp_path / "real-fixture.md").exists()


def assert_non_smoke_stage1_reject_suppresses_stage2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = FakeRunner(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["routing_gate", "--lane", "query", "--report-name", "reject", "--json"],
    )
    assert main(RejectingRunner(base)) == 2
    assert len(base.calls) == 3
    assert all(call.stage == "1" for call in base.calls)


def assert_real_subprocess_protocol_runs_with_fake_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    monkeypatch.setenv(
        "HC_RAG_ROUTING_ARM_ADAPTER", "tests.fake_routing_arm_adapter:run_arm"
    )
    monkeypatch.setenv("HC_RAG_ROUTING_FAKE_OUTPUT_DIR", str(tmp_path))
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.routing_gate",
            "--lane",
            lane,
            "--stage",
            "all",
            "--repetitions",
            "2",
            "--concurrency",
            "1",
            "--report-name",
            f"subprocess-{lane}",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["verdict"] == "ADOPT"
    assert (tmp_path / f"subprocess-{lane}.json").exists()
    assert (tmp_path / f"subprocess-{lane}.md").exists()


@pytest.mark.parametrize("failure", ["missing", "contaminated"])
def assert_cross_phase_failure_is_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
    failure: str,
) -> None:
    base = FakeRunner(tmp_path)
    runner = (
        MissingStage2Runner(base) if failure == "missing" else ContaminatedRunner(base)
    )
    monkeypatch.setattr(
        "sys.argv",
        ["routing_gate", "--lane", lane, "--report-name", "cross-phase", "--json"],
    )
    assert main(runner) == 1
    assert not tuple(tmp_path.iterdir())


def assert_requested_settings_mismatch_is_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "routing_gate",
            "--lane",
            lane,
            "--repetitions",
            "7",
            "--concurrency",
            "9",
            "--report-name",
            "settings",
            "--json",
        ],
    )
    assert main(SettingsMismatchRunner(FakeRunner(tmp_path))) == 1
    assert not tuple(tmp_path.iterdir())
