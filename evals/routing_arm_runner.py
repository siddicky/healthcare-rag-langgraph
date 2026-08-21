from __future__ import annotations

import argparse
import importlib
import os
import sys
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict
from typing_extensions import override

from evals.routing_gate_runner import ArmRunRequest, ArmRunResult, RunnerError


class ArmAdapter(Protocol):
    def __call__(self, request: ArmRunRequest) -> ArmRunResult: ...


class ArmFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: str = "ERROR"
    detail: str


@dataclass(frozen=True, slots=True)
class ArmAdapterError(Exception):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


class ArmArgs(argparse.Namespace):
    lane: str
    arm: str
    stage: str
    repetitions: int
    concurrency: int
    report_name: str
    json: bool

    def __init__(self) -> None:
        super().__init__()
        self.lane, self.arm, self.stage = "query", "current+llm", "1"
        self.repetitions, self.concurrency = 2, 1
        self.report_name, self.json = "routing-arm", False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one fresh routing evaluation arm")
    _ = parser.add_argument("--lane", choices=("query", "safety"), required=True)
    _ = parser.add_argument(
        "--arm",
        choices=(
            "current+llm",
            "deterministic+llm",
            "tool+llm",
            "current+semantic_router",
        ),
        required=True,
    )
    _ = parser.add_argument("--stage", choices=("1", "2"), required=True)
    _ = parser.add_argument("--repetitions", type=int, required=True)
    _ = parser.add_argument("--concurrency", type=int, required=True)
    _ = parser.add_argument("--report-name", required=True)
    _ = parser.add_argument("--json", action="store_true")
    return parser


def _load_adapter() -> ArmAdapter:
    reference = os.getenv(
        "HC_RAG_ROUTING_ARM_ADAPTER", "evals.routing_arm_runtime:run_arm"
    )
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ArmAdapterError("invalid HC_RAG_ROUTING_ARM_ADAPTER module:function")
    try:
        module = importlib.import_module(module_name)
        adapter = getattr(module, attribute)
    except (ModuleNotFoundError, AttributeError) as exc:
        raise ArmAdapterError(
            f"routing arm adapter unavailable: {reference}; complete the arm implementation task"
        ) from exc
    if not callable(adapter):
        raise ArmAdapterError(f"routing arm adapter is not callable: {reference}")

    def invoke(request: ArmRunRequest) -> ArmRunResult:
        return ArmRunResult.model_validate(adapter(request))

    return invoke


def _request(args: ArmArgs) -> ArmRunRequest:
    return ArmRunRequest.model_validate(
        {
            "lane": args.lane,
            "arm": args.arm,
            "stage": args.stage,
            "repetitions": args.repetitions,
            "concurrency": args.concurrency,
            "report_name": args.report_name,
        }
    )


def main(adapter: ArmAdapter | None = None) -> int:
    args = _parser().parse_args(namespace=ArmArgs())
    print(
        f"[routing-arm] lane={args.lane} arm={args.arm} stage={args.stage}",
        file=sys.stderr,
        flush=True,
    )
    try:
        result = (adapter or _load_adapter())(_request(args))
    except (ArmAdapterError, RunnerError) as exc:
        print(ArmFailure(detail=str(exc)).model_dump_json())
        return 1
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
