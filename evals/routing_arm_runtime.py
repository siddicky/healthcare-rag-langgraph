from __future__ import annotations

from evals.routing_dataset import load_bundle
from evals.routing_dataset_models import Split
from evals.routing_gate_runner import ArmRunRequest, ArmRunResult, RunnerError


def run_arm(request: ArmRunRequest) -> ArmRunResult:
    bundle = load_bundle()
    measurement_rows = tuple(
        row for row in bundle.rows if row.split is not Split.CALIBRATION
    )
    if not measurement_rows:
        raise RunnerError("routing measurement has no non-calibration rows")
    raise RunnerError(
        f"routing arm runtime unavailable for {request.arm} stage {request.stage}; "
        "complete the query/safety arm implementation task before paid evaluation"
    )
