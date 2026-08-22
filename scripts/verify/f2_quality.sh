#!/usr/bin/env bash
set -uo pipefail

failures=0
run_stage() {
  local name="$1"
  shift
  echo "==> ${name}"
  if "$@"; then
    echo "PASS: ${name}"
  else
    echo "FAIL: ${name}"
    failures=1
  fi
}

run_stage "make test" make test
# Ruff is scoped to this plan's own contributions; the rest of the repo predates
# ruff adoption in this project and its legacy debt is out of this plan's mandate
# (mirrors the protected-dirs principle: no retroactive grading of untouched code).
# Under evals/, only the todo-16 agent-parity files are linted for the same reason.
run_stage "ruff" uv run ruff check \
  healthcare_rag/agent \
  evals/agent_cases.py \
  evals/agent_chunks.py \
  evals/agent_parity.py \
  evals/agent_report.py \
  evals/check_agent_parity.py \
  evals/coach_engine.py \
  evals/coach_fixtures.py \
  evals/offline_agent_fakes.py \
  tests/agent \
  tests/test_agent_eval.py \
  tests/test_forget_member.py \
  tests/test_catalog_data_ref_fixture.py \
  scripts/coach_smoke.py \
  scripts/deployed_smoke.py \
  scripts/provision_feedback_project.py \
  scripts/forget_member.py \
  scripts/forget_member_api.py \
  scripts/__init__.py \
  scripts/verify \
  frontend/e2e/server.py
run_stage "basedpyright agent" bash -c '
  output="$(uv run basedpyright healthcare_rag/agent 2>&1)"
  printf "%s\n" "${output}"
  case "${output}" in
    *$'"'"'\n0 errors,'"'"'*) exit 0 ;;
    *) exit 1 ;;
  esac
'
run_stage "frontend build/type-check" bash -c 'cd frontend && bun run build'
run_stage "frontend vitest" bash -c 'cd frontend && bun run test'

exit "${failures}"
