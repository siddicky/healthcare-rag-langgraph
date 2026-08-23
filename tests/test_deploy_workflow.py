from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Final, TypedDict

import yaml
from pydantic import TypeAdapter

WorkflowStep = TypedDict(
    "WorkflowStep",
    {"name": str, "run": str, "if": str},
    total=False,
)
WorkflowPermissions = TypedDict(
    "WorkflowPermissions",
    {"actions": str, "contents": str},
    total=False,
)
DeployJob = TypedDict(
    "DeployJob",
    {"permissions": WorkflowPermissions, "steps": list[WorkflowStep]},
)
WorkflowJobs = TypedDict("WorkflowJobs", {"deploy-prod": DeployJob})
Workflow = TypedDict("Workflow", {"jobs": WorkflowJobs})
WORKFLOW_ADAPTER: Final = TypeAdapter(Workflow)


def _workflow() -> Workflow:
    return WORKFLOW_ADAPTER.validate_json(
        json.dumps(yaml.safe_load(Path(".github/workflows/deploy.yml").read_text()))
    )


def _deploy_step(name: str) -> WorkflowStep:
    workflow = _workflow()
    steps = workflow["jobs"]["deploy-prod"]["steps"]
    return next(step for step in steps if step.get("name") == name)


def _run_environment_audit(
    tmp_path: Path,
    *,
    environment_json: str,
    policy_json: str,
    audit_pat: str,
    gh_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "gh-calls.log"
    fake_gh = fake_bin / "gh"
    _ = fake_gh.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$GH_CALLS"
case "$*" in
  *deployment-branch-policies*)
    if [ "$GH_FAILURE" = "1" ]; then
      printf 'Resource not accessible by integration\n' >&2
      exit 1
    fi
    printf '%s\n' "$POLICY_API_JSON"
    ;;
  *environments/production*)
    if [ "$GH_FAILURE" = "1" ]; then
      printf 'Resource not accessible by integration\n' >&2
      exit 1
    fi
    printf '%s\n' "$ENVIRONMENT_JSON"
    ;;
  *)
    printf 'unexpected gh invocation: %s\n' "$*" >&2
    exit 2
    ;;
esac
"""
    )
    _ = fake_gh.chmod(0o755)
    environment = os.environ | {
        "AUDIT_PAT": audit_pat,
        "ENVIRONMENT_JSON": environment_json,
        "GH_CALLS": str(calls),
        "GH_FAILURE": "1" if gh_failure else "0",
        "GH_TOKEN": "github-token-value",
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "POLICY_API_JSON": policy_json,
        "REPO": "example/healthcare-rag",
    }
    audit_script = _deploy_step(
        "Verify production environment protection (fail-closed)"
    ).get("run")
    assert audit_script is not None
    return subprocess.run(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            audit_script,
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )


def test_environment_audit_reads_custom_policy_endpoint_before_passing(
    tmp_path: Path,
) -> None:
    result = _run_environment_audit(
        tmp_path,
        environment_json=(
            '{"protection_rules":[{"type":"branch_policy"}],'
            '"deployment_branch_policy":{"protected_branches":false,'
            '"custom_branch_policies":true}}'
        ),
        policy_json=(
            '{"total_count":1,"branch_policies":'
            '[{"id":42,"name":"v*.*.*","type":"tag"}]}'
        ),
        audit_pat="audit-token-value",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Environment protection verification PASSED" in result.stdout
    assert "deployment-branch-policies" in (
        tmp_path / "gh-calls.log"
    ).read_text()


def test_environment_audit_grants_github_token_actions_read() -> None:
    permissions = _workflow()["jobs"]["deploy-prod"]["permissions"]

    assert permissions.get("actions") == "read"


def test_environment_audit_fails_closed_without_leaking_tokens(
    tmp_path: Path,
) -> None:
    result = _run_environment_audit(
        tmp_path,
        environment_json='{"message":"Resource not accessible by integration"}',
        policy_json="{}",
        audit_pat="audit-token-value",
        gh_failure=True,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "ENVIRONMENT_AUDIT_PAT" in output
    assert "audit-token-value" not in output
    assert "github-token-value" not in output


def test_environment_audit_rejects_non_release_deployment_policy(
    tmp_path: Path,
) -> None:
    result = _run_environment_audit(
        tmp_path,
        environment_json=(
            '{"protection_rules":[{"type":"branch_policy"}],'
            '"deployment_branch_policy":{"protected_branches":false,'
            '"custom_branch_policies":true}}'
        ),
        policy_json=(
            '{"total_count":1,"branch_policies":'
            '[{"id":42,"name":"main","type":"branch"}]}'
        ),
        audit_pat="audit-token-value",
    )

    assert result.returncode != 0
    assert "v*.*.*" in result.stdout + result.stderr


def test_smoke_result_check_does_not_run_when_smoke_was_skipped() -> None:
    step = _deploy_step("Fail job if smoke failed (no auto-rollback)")
    condition = step.get("if")

    assert condition == "${{ always() && steps.smoke.outcome != 'skipped' }}"
