#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${LANGGRAPH_DEPLOYMENT_URL:-}" ]]; then
  echo "no deployment configured — this stage requires one" >&2
  exit 1
fi
if [[ -z "${DEPLOYED_URL:-}" ]]; then
  echo "no deployed frontend configured — set DEPLOYED_URL" >&2
  exit 1
fi

echo "==> ten-check deployed smoke"
uv run python scripts/deployed_smoke.py --url "${LANGGRAPH_DEPLOYMENT_URL}"

runfile="$(mktemp "${TMPDIR:-/tmp}/coach-f3-run.XXXXXX.json")"
config="frontend/e2e/.tmp/f3-deployed.config.ts"
mkdir -p frontend/e2e/.tmp
cleanup() {
  rm -f "${runfile}" "${config}"
}
trap cleanup EXIT

: "${SUPABASE_URL:?SUPABASE_URL is required}"
: "${NEXT_PUBLIC_SUPABASE_ANON_KEY:?NEXT_PUBLIC_SUPABASE_ANON_KEY is required}"
: "${COACH_E2E_U1_EMAIL:?COACH_E2E_U1_EMAIL is required}"
: "${COACH_E2E_U1_PASSWORD:?COACH_E2E_U1_PASSWORD is required}"
: "${COACH_E2E_U1_USER_ID:?COACH_E2E_U1_USER_ID is required}"
: "${COACH_E2E_U2_EMAIL:?COACH_E2E_U2_EMAIL is required}"
: "${COACH_E2E_U2_PASSWORD:?COACH_E2E_U2_PASSWORD is required}"
: "${COACH_E2E_U2_USER_ID:?COACH_E2E_U2_USER_ID is required}"
: "${LANGGRAPH_U1_TOKEN:?LANGGRAPH_U1_TOKEN is required}"
: "${LANGGRAPH_U2_TOKEN:?LANGGRAPH_U2_TOKEN is required}"
: "${LANGSMITH_API_KEY:?LANGSMITH_API_KEY is required}"
: "${COACH_INTERNAL_TOKEN:?COACH_INTERNAL_TOKEN is required}"

RUNFILE="${runfile}" uv run python - <<'PY'
import json
import os
from pathlib import Path

payload = {
    "ready": True,
    "dep_url": os.environ["SUPABASE_URL"],
    "server_url": os.environ["LANGGRAPH_DEPLOYMENT_URL"],
    "frontend_url": os.environ["DEPLOYED_URL"],
    "u1": {"email": os.environ["COACH_E2E_U1_EMAIL"], "password": os.environ["COACH_E2E_U1_PASSWORD"], "token": os.environ["LANGGRAPH_U1_TOKEN"], "user_id": os.environ["COACH_E2E_U1_USER_ID"]},
    "u2": {"email": os.environ["COACH_E2E_U2_EMAIL"], "password": os.environ["COACH_E2E_U2_PASSWORD"], "token": os.environ["LANGGRAPH_U2_TOKEN"], "user_id": os.environ["COACH_E2E_U2_USER_ID"]},
    "internal": {"api_key": os.environ["LANGSMITH_API_KEY"], "token": os.environ["COACH_INTERNAL_TOKEN"]},
    "anon_key": os.environ["NEXT_PUBLIC_SUPABASE_ANON_KEY"],
}
Path(os.environ["RUNFILE"]).write_text(json.dumps(payload))
PY

cat >"${config}" <<'TS'
import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "..",
  testMatch: "smoke.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 240_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: { headless: true, actionTimeout: 20_000, navigationTimeout: 30_000 },
});
TS

echo "==> deployed Playwright"
COACH_E2E_RUNFILE="${runfile}" bash -c 'cd frontend && bunx playwright test --config e2e/.tmp/f3-deployed.config.ts'
echo "==> agent evals"
make eval-agent
make eval-agent-multiturn
uv run python evals/check_agent_parity.py
echo "PASS: real-environment verification"
