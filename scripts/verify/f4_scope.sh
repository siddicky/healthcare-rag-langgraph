#!/usr/bin/env bash
set -euo pipefail

evidence_dir="${EVIDENCE_DIR:-.omo/evidence}"
base_file="${evidence_dir}/BASE_SHA"
if [[ ! -s "${base_file}" ]]; then
  echo "FAIL: missing BASE_SHA receipt at ${base_file}" >&2
  exit 1
fi
base_sha="$(tr -d '[:space:]' <"${base_file}")"
protected=(healthcare_rag/graph/ healthcare_rag/processors/)

if [[ -n "$(GIT_MASTER=1 git diff "${base_sha}..HEAD" --name-only -- "${protected[@]}")" ]]; then
  echo "FAIL: protected directories changed since BASE_SHA" >&2
  exit 1
fi
if [[ -n "$(GIT_MASTER=1 git diff --name-only -- "${protected[@]}")$(GIT_MASTER=1 git diff --cached --name-only -- "${protected[@]}")" ]]; then
  echo "FAIL: protected directories are dirty in the worktree/index" >&2
  exit 1
fi
echo "PASS: protected directories unchanged from ${base_sha} and clean"

cron_offenders="$(grep -R -l -E '(^|[[:space:]])(from|import).*cron_client' healthcare_rag/agent --include='*.py' | grep -v -E '/(cron_client|reminders)\.py$' || true)"
if [[ -n "${cron_offenders}" ]]; then
  echo "FAIL: cron client imported outside internal-only modules: ${cron_offenders}" >&2
  exit 1
fi
echo "PASS: no member cron-client usage"

if grep -R -n -E 'LANGSMITH_API_KEY|LANGCHAIN_API_KEY' frontend/src >/dev/null; then
  echo "FAIL: LangSmith API key referenced in frontend source" >&2
  exit 1
fi
echo "PASS: no LangSmith key in frontend source"

if grep -R -n -E '(^|[[:space:]])(from|import).*healthcare_rag' frontend/src >/dev/null; then
  echo "FAIL: production frontend imports healthcare_rag" >&2
  exit 1
fi
echo "PASS: frontend source is independent of the Python package"
