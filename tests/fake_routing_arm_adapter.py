from __future__ import annotations

import os
from pathlib import Path

from evals.routing_gate_runner import ArmRunRequest, ArmRunResult
from tests.routing_gate_cases import manifest, report
from tests.routing_gate_fixtures import full, query_stage1, safety_metrics


def run_arm(request: ArmRunRequest) -> ArmRunResult:
    output_dir = Path(os.environ["HC_RAG_ROUTING_FAKE_OUTPUT_DIR"])
    provenance = manifest(request.arm).model_copy(
        update={
            "experiment_name": request.report_name,
            "experiment_url": f"https://smith.langchain.com/{request.report_name}",
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
        report=report(provenance, output_dir),
        query_stage1=query_stage1(),
        safety_residual=safety_metrics(),
        safety_full_shell=safety_metrics(),
        stage2=None if request.stage == "1" else stage2,
    )
