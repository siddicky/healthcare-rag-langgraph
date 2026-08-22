# Deploy Runbook — hc-rag-server-prod (Fly.io, prod-only)

> **Scope:** production only — no staging app, no second environment.  
> **Source of truth for secrets:** GitHub Environment `production`. The workflow syncs them to Fly; there are never two manual stores.  
> **Deploy primitive:** immutable image digest (`fly deploy --image ghcr.io/<repo>@sha256:<digest> --config deploy/fly.prod.toml`). No `[build]` in the fly.toml.  
> **Pre-read:** this is the single followable runbook. If a step says "run X", paste exactly that X.

---

## 0. Compliance Gate — first prod deploy requires recorded sign-off

**No production deploy of real member data runs until the user records an explicit compliance sign-off on the healthcare data posture.**

What must be recorded before the first `deploy-prod` approval:

1. The operator (`you`) has reviewed `docs/safety.md` — the PHI posture, the safety gate, the identifier-scrubbing guarantees and the known limits of the deployed surface.
2. Release checks run under **synthetic test accounts only** (`LANGGRAPH_U1_TOKEN` / `LANGGRAPH_U2_TOKEN`) with `LANGSMITH_TRACING=false` and redacted smoke logs (see §5 Smoke). Real member credentials are never used in the smoke.
3. The sign-off is a dated entry in **`.omo/evidence/task-12-oss-agent-server-tag-deploys.md`** (canonical evidence for this build; also mirrored to `.omo/notepads/oss-agent-server-tag-deploys/decisions.md` for the deployment log) stating the date, the reviewer, and the version tag approved for first prod, plus the GitHub Environment `production` protection rule being in place.

The `.github/workflows/deploy.yml` pipeline is gated on the `production` environment's **required reviewer** (you). Self-approval is acknowledged for a solo repo but the click is still required. Do not bypass it. The compliance gate is a **pre-condition of the first approval**, not a post-deploy checklist.

---

## 1. Bootstrap — one-time setup (apps ×2, volume, secret seeding BEFORE the first pipeline deploy)

> Do this once per Fly organization. After this, every deploy comes from the tag pipeline (§3). The first pipeline deploy must never boot secret-less, so **seed secrets in §1.3 before you push the first tag**.

### 1.1 Prerequisites

```bash
# 1. Install flyctl and authenticate (once per machine)
brew install flyctl        # or: curl -L https://fly.io/install.sh | sh
fly auth login

# 2. Pick the Fly org (if you have more than one)
fly orgs list
export FLY_ORG=<your-org-slug>

# 3. Clone the repo and install the venv (needed for smoke)
git clone <repo-url> healthcare-rag-langgraph
cd healthcare-rag-langgraph
make venv                  # uv venv + editable install

# 4. Confirm flyctl is reachable and jq is available (used in later steps)
fly version
jq --version
```

`FLY_API_TOKEN` for the pipeline is **not** a Fly secret — it is a GitHub Environment `production` secret (see §2). Do not create it yet — it is created in §1.2 after the apps exist.

### 1.2 Create the two Fly apps and the Weaviate volume

```bash
# Server app (uses deploy/fly.prod.toml)
fly apps create hc-rag-server-prod --org "$FLY_ORG"

# Weaviate companion app (uses deploy/fly.weaviate-prod.toml)
fly apps create hc-rag-weaviate-prod --org "$FLY_ORG"

# 1 GB volume for Weaviate — the ONLY persistent volume in the system.
# Region must match primary_region = "iad" in both tomls.
fly volumes create weaviate_data \
  --app hc-rag-weaviate-prod \
  --region iad \
  --size 1

# Verify
fly apps list
fly volumes list --app hc-rag-weaviate-prod
```

Deploy Weaviate once (image + env + volume). Either use the companion toml:

```bash
fly deploy \
  --config deploy/fly.weaviate-prod.toml \
  --image cr.weaviate.io/semitechnologies/weaviate:1.30.2 \
  --app hc-rag-weaviate-prod
```

or equivalently with an explicit machine run (the toml's `[env]` and `[[mounts]]` carry the same values):

```bash
fly machines run cr.weaviate.io/semitechnologies/weaviate:1.30.2 \
  --app hc-rag-weaviate-prod \
  --region iad \
  --volume weaviate_data:/var/lib/weaviate \
  --env QUERY_DEFAULTS_LIMIT=25 \
  --env AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED=true \
  --env PERSISTENCE_DATA_PATH=/var/lib/weaviate \
  --env ENABLE_API_BASED_MODULES=true \
  --env CLUSTER_HOSTNAME=node1 \
  --port 8080:8080 \
  --port 50051:50051
```

Wait until Weaviate is ready:

```bash
fly checks list --app hc-rag-weaviate-prod
# or from inside the private network:
fly ssh console --app hc-rag-weaviate-prod --command "wget -qO- http://127.0.0.1:8080/v1/.well-known/ready && echo ready"
```

Server networking: the server app reaches Weaviate at the private DNS name set in `deploy/fly.prod.toml`:

```toml
# deploy/fly.prod.toml [env]
WEAVIATE_HOST = "hc-rag-weaviate-prod.internal"
WEAVIATE_PORT = "8080"
```

No public Weaviate URL is exposed. Scaling or re-creating the volume is a separate migration (not covered here).

**Create the pipeline deploy token now that the apps exist:**

```bash
# App-scoped token (preferred — least privilege). Must run AFTER `fly apps create` above:
fly tokens create deploy -x 8760h -a hc-rag-server-prod
# Alternative if you need org-wide scope (also valid — but prefer the app-scoped token):
# fly tokens create org "$FLY_ORG" -x 8760h
# Store the output as GitHub Environment secret FLY_API_TOKEN (see §1.3 / §2).
```

### 1.3 Seed secrets BEFORE the first pipeline deploy

> **Critical:** the first pipeline deploy must never boot secret-less. Seed Fly secrets **before** you push the first `v*.*.*` tag.

Secrets source of truth is the GitHub Environment `production` (§2). For bootstrap, seed Fly directly from the same values you will store in that environment. **Never commit secret values.** Use placeholder syntax below.

**Enumerate the secrets to seed** (full list from `.env.example` — the required set for the server and its smoke is marked `required = yes`; copy every `yes` row — do not omit any):

| Fly secret / GitHub Environment secret | Required | Value placeholder | Notes |
|---|---|---|---|
| `OPENAI_API_KEY` | yes | `<openai-api-key>` | LLMs + text2vec-openai |
| `SUPABASE_URL` | yes | `<supabase-url>` | e.g. `https://<project>.supabase.co` |
| `SUPABASE_SERVICE_KEY` | yes | `<supabase-service-role-key>` | server-only |
| `NEXT_PUBLIC_SUPABASE_URL` | yes | `<supabase-url>` | same as SUPABASE_URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | yes | `<supabase-anon-key>` | |
| `COACH_INTERNAL_TOKEN` | yes | `<high-entropy-internal-token>` | internal cron/owner ops |
| `COACH_ALLOWED_ORIGINS` | yes | `<allowed-origins>` | e.g. `https://<frontend>` |
| `CORS_ALLOW_ORIGINS` | yes | `<cors-origins>` | keep aligned with COACH_ALLOWED_ORIGINS |
| `LANGSMITH_FEEDBACK_PROJECT_ID` | yes | `<uuid>` | `00000000-...` shape — required by smoke (`COACH_INTERNAL_TOKEN`/`LANGSMITH_FEEDBACK_PROJECT_ID` both required); if feedback project not yet configured, create one and use its UUID |
| `LANGGRAPH_DEPLOYMENT_URL` | yes | `<https://hc-rag-server-prod.fly.dev>` | public prod URL |
| `LANGSMITH_API_KEY` | yes (for smoke) | `<lsv2_...>` | required by `scripts/deployed_smoke.py` even when `LANGSMITH_TRACING=false` |
| `LANGSMITH_PROJECT` | if tracing | `<healthcare-rag>` | optional |
| `LANGGRAPH_U1_TOKEN` | yes (smoke) | `<synthetic-u1-bearer>` | synthetic Supabase user JWT — see §5 for provisioning |
| `LANGGRAPH_U2_TOKEN` | yes (smoke) | `<synthetic-u2-bearer>` | synthetic Supabase user JWT — see §5 |
| `SUPABASE_JWT_SECRET` | if used | `<jwt-secret>` | only if auth needs it |

> **Why `LANGSMITH_FEEDBACK_PROJECT_ID` and `LANGSMITH_API_KEY` are `yes`:** `scripts/deployed_smoke.py` requires `LANGSMITH_API_KEY`, `COACH_INTERNAL_TOKEN`, and `LANGSMITH_FEEDBACK_PROJECT_ID` even in manual runs (see §5). The table above matches the workflow's `EXPECTED_NAMES` fail-closed check — do not treat them as optional.

Seed on Fly (all required names — this example is truncated for display; repeat for every `yes` row above; values never echoed in CI):

```bash
# Full example — replace every <...> with the real value from your vault / 1Password.
# The workflow verifies every name below via `fly secrets list` (names only).
fly secrets set --app hc-rag-server-prod \
  OPENAI_API_KEY="<openai-api-key>" \
  SUPABASE_URL="<supabase-url>" \
  SUPABASE_SERVICE_KEY="<supabase-service-role-key>" \
  NEXT_PUBLIC_SUPABASE_URL="<supabase-url>" \
  NEXT_PUBLIC_SUPABASE_ANON_KEY="<supabase-anon-key>" \
  COACH_INTERNAL_TOKEN="<high-entropy-internal-token>" \
  COACH_ALLOWED_ORIGINS="<allowed-origins>" \
  CORS_ALLOW_ORIGINS="<cors-origins>" \
  LANGSMITH_FEEDBACK_PROJECT_ID="<uuid>" \
  LANGSMITH_API_KEY="<lsv2_...>" \
  LANGGRAPH_DEPLOYMENT_URL="<https://hc-rag-server-prod.fly.dev>" \
  LANGGRAPH_U1_TOKEN="<synthetic-u1-bearer>" \
  LANGGRAPH_U2_TOKEN="<synthetic-u2-bearer>"

# Verify — names only, never values
fly secrets list --app hc-rag-server-prod
# Expected: every `yes` name above appears
```

Then create the **same set** as GitHub Environment `production` secrets (the workflow will sync them on every deploy — see §2):

```bash
# Via gh CLI (one per secret; never commit values) — every `yes` row:
gh secret set OPENAI_API_KEY --env production
gh secret set SUPABASE_URL --env production
gh secret set SUPABASE_SERVICE_KEY --env production
gh secret set NEXT_PUBLIC_SUPABASE_URL --env production
gh secret set NEXT_PUBLIC_SUPABASE_ANON_KEY --env production
gh secret set COACH_INTERNAL_TOKEN --env production
gh secret set COACH_ALLOWED_ORIGINS --env production
gh secret set CORS_ALLOW_ORIGINS --env production
gh secret set LANGSMITH_FEEDBACK_PROJECT_ID --env production
gh secret set LANGSMITH_API_KEY --env production
gh secret set LANGGRAPH_DEPLOYMENT_URL --env production
gh secret set LANGGRAPH_U1_TOKEN --env production
gh secret set LANGGRAPH_U2_TOKEN --env production
# FLY_API_TOKEN is the deploy token for flyctl in CI (created in §1.2)
gh secret set FLY_API_TOKEN --env production
```

Verify the environment protection (required reviewer + tag policy). The workflow also verifies this at runtime:

```bash
gh api repos/<owner>/<repo>/environments/production --jq '.protection_rules'
gh api repos/<owner>/<repo>/environments/production/deployment-branch-policies --jq '.'
gh api repos/<owner>/<repo>/environments/production/deployment_protection_rules --jq '.'
# Expected: required_reviewers includes you; tag pattern v*.*.* enforced
```

You are now ready to push the first tag (§3). The first deploy will boot with secrets already present.

---

## 2. Secrets — source of truth and sync

**Source of truth:** GitHub Environment `production`. The deploy workflow syncs runtime secrets GitHub → Fly on every `deploy-prod` run and then verifies by name (`fly secrets list` — names only, values never echoed or logged).

**Never two manual stores.** After bootstrap (§1.3), do not manually diverge `fly secrets set` from the GitHub Environment. If a secret must change, change it in **GitHub Environment `production` first**, then let the next pipeline deploy sync it (or run a manual sync deploy of the current digest). Direct `fly secrets set` outside the pipeline is only for the initial bootstrap and for emergency rotation when the pipeline cannot run — and must be mirrored back to the GitHub Environment immediately.

What the workflow does on each deploy:

```bash
# Inside deploy-prod (illustrative — the workflow runs this, not you pasting secrets)
# For each secret NAME in the environment, the job runs:
fly secrets set --app hc-rag-server-prod NAME="<value-from-GitHub-Env>" --stage
# After the deploy:
fly secrets list --app hc-rag-server-prod   # names only — workflow asserts every expected NAME appears
```

**Must NOT:** put secret values in `deploy/fly.*.toml`, `docs/deploy.md`, `docker-compose.*.yml`, or any committed file. Only `VARNAME=<placeholder>` syntax.

---

## 3. Deploy — by immutable digest (tag → GHCR → prod approval → Fly)

### 3.1 Release validation (hermetic, local)

```bash
make release TAG=v1.2.3
# Expected: prints the exact git push command and exits 0 WITHOUT pushing.
# Without TAG: exits non-zero and prints usage.
# No tag is pushed by the target — the human pushes (next step).
```

> **Tag contract note:** `make release` locally accepts `vX.Y.Z` and also `vX.Y.Z-<prerelease>` (e.g. `v0.0.1-rc`) to allow hermetic `-rc` verification probes without error. The **production workflow** enforces strict `^v\d+\.\d+\.\d+$` (no suffix) on both `push` and `workflow_dispatch` and will fail a `-rc` tag. Use strict `vX.Y.Z` for any real prod deploy. The Makefile prints `git tag -s $(TAG)` (annotated) while §3.2 shows `git tag v1.2.3` (lightweight) — both create the tag; the pipeline only cares that the tag exists on origin.

### 3.2 Push the tag (human)

```bash
git tag v1.2.3
git push origin v1.2.3
# The workflow triggers on push: tags: ['v*.*.*'] and on workflow_dispatch with a required tag input.
```

### 3.3 Pipeline (what happens after the push)

1. **`build` job:** checks out the tag commit, builds the image with `docker/build-push-action`, pushes to `ghcr.io/<owner>/<repo>` with semver + sha tags, outputs the immutable **image digest** `ghcr.io/<owner>/<repo>@sha256:<digest>`. No deploy yet.
2. **`deploy-prod` job:** `needs: build`, `environment: production`, `concurrency: {group: deploy-production, cancel-in-progress: false}` (env-wide deploy lock — one prod deploy at a time). Uses the pinned `flyctl` with `FLY_API_TOKEN` from the production environment:

   ```bash
   fly deploy \
     --app hc-rag-server-prod \
     --config deploy/fly.prod.toml \
     --image ghcr.io/<owner>/<repo>@sha256:<digest>
   ```

   Then syncs secrets (§2), waits for `/ok`, and runs the live smoke (§5).

3. **Approval gate:** the `production` environment has a required reviewer (you) and a tag policy `v*.*.*`. The run pauses at `deploy-prod` until you click **Approve** in the GitHub Actions UI. The workflow also runs a verification step that fails closed if the protection rules are missing or misconfigured.

### 3.4 Tags and dispatch notes

- Tag format is strict: `^v\d+\.\d+\.\d+$` (e.g. `v1.2.3`). The workflow validates it on both `push` and `workflow_dispatch`.
- `workflow_dispatch` requires an explicit `tag` input that must exist on `origin`, and the job checks out that tag commit — not `main`.
- Mutable tags are never deployed — only the digest from the `build` job.

---

## 4. Ingest — load the checked-in chunks into prod Weaviate (`make ingest-fly`)

`make ingest-fly` is a **documentation-only** helper in this stage — running it prints the exact `fly machines run` command below without requiring `flyctl` to be installed (exit 0, hermetic). The real ingest is the `fly machines run` one-off that follows. A future Makefile iteration will make `make ingest-fly` execute it directly.

```bash
# From the repo root — needs FLY_API_TOKEN and LANGGRAPH_DEPLOYMENT_URL in env,
# or run via the deployed smoke's synthetic credentials.
make ingest-fly
# Output: prints the fly machines run example — copy that command and run it.
```

What to run (paste exactly; `--region iad` pins to the Weaviate region; image uses the GHCR digest from the last green build — not a mutable `:<tag>`):

```bash
# One-off Fly machine (ephemeral, private network, inherits server-app secrets)
# Use the digest from the last green deploy-prod log — not a floating tag.
export REPO="ghcr.io/<owner>/<repo>"
export DIGEST="sha256:<digest-from-last-green-build>"

fly machines run "${REPO}@${DIGEST}" \
  --app hc-rag-server-prod \
  --region iad \
  --rm \
  --env WEAVIATE_HOST=hc-rag-weaviate-prod.internal \
  --env WEAVIATE_PORT=8080 \
  --command "python -m healthcare_rag.storage.vector_store --delete-all --collection Lipitor data/chunks_lipitor.json --collection Metformin data/chunks_metformin.json"

# Notes:
# - Do NOT add --env OPENAI_API_KEY="..." — the one-off inherits Fly secrets
#   (OPENAI_API_KEY, etc.) from hc-rag-server-prod. If secrets haven't been
#   seeded yet (bootstrap not done), seed them first (§1.3).
# - --region iad matches primary_region = "iad" in both tomls.
# - No --volume needed — this machine shares the network, not the volume.
```

Alternative (when the Makefile target becomes live):

```bash
make ingest-fly WEAVIATE_HOST=hc-rag-weaviate-prod.internal WEAVIATE_PORT=8080
```

Verify:

```bash
curl -sf https://hc-rag-server-prod.fly.dev/ok | jq .
# Then run a single smoke check to confirm retrieval:
LANGGRAPH_DEPLOYMENT_URL=https://hc-rag-server-prod.fly.dev \
LANGGRAPH_U1_TOKEN="<synthetic-u1-bearer>" \
LANGGRAPH_U2_TOKEN="<synthetic-u2-bearer>" \
LANGSMITH_API_KEY="<lsv2_...>" \
COACH_INTERNAL_TOKEN="<internal>" \
LANGSMITH_FEEDBACK_PROJECT_ID="<uuid>" \
LANGSMITH_TRACING=false \
uv run python scripts/deployed_smoke.py --url https://hc-rag-server-prod.fly.dev
```

Ingest is idempotent (`--delete-all` drops and recreates the collections). Run it after the first Weaviate bootstrap and after any chunk update.

---

## 5. Smoke — post-deploy live check (synthetic accounts, tracing off, redacted logs)

After every deploy, the pipeline runs the ten-check suite against the prod URL with synthetic accounts and no tracing. The pipeline's `Run deployed smoke` step sets all six required env vars from GitHub Environment `production` secrets — your manual command must do the same:

```bash
LANGGRAPH_DEPLOYMENT_URL=https://hc-rag-server-prod.fly.dev \
LANGGRAPH_U1_TOKEN="<synthetic-u1-bearer>" \
LANGGRAPH_U2_TOKEN="<synthetic-u2-bearer>" \
LANGSMITH_API_KEY="<lsv2_...>" \
COACH_INTERNAL_TOKEN="<internal>" \
LANGSMITH_FEEDBACK_PROJECT_ID="<uuid>" \
LANGSMITH_TRACING=false \
uv run python scripts/deployed_smoke.py --url https://hc-rag-server-prod.fly.dev
# Do NOT pass --allow-insecure-staging for the HTTPS prod URL. That flag is only for http:// staging harnesses.
```

> **Required env contract (`scripts/deployed_smoke.py`):** the smoke reads `LANGGRAPH_DEPLOYMENT_URL`, `LANGGRAPH_U1_TOKEN`, `LANGGRAPH_U2_TOKEN`, `LANGSMITH_API_KEY`, `COACH_INTERNAL_TOKEN`, `LANGSMITH_FEEDBACK_PROJECT_ID` from the environment and fails fast if any is missing (`missing required environment variable: ...`). `LANGSMITH_TRACING` must be `false` in production (synthetic input only). The three values above not in earlier runbook drafts (`LANGSMITH_API_KEY`, `COACH_INTERNAL_TOKEN`, `LANGSMITH_FEEDBACK_PROJECT_ID`) are the missing ones — always include all six.

**Provisioning synthetic accounts** (one-time per environment):

```bash
# Create two Supabase auth users for smoke (one per synthetic principal).
# Via Supabase dashboard: Authentication → Users → Create user (email + password).
# Or via Supabase Admin API (service_role key):
curl -X POST "https://<project>.supabase.co/auth/v1/admin/users" \
  -H "apikey: <supabase-service-role-key>" \
  -H "Authorization: Bearer <supabase-service-role-key>" \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke-u1@<domain>","password":"<high-entropy>","email_confirm":true}'

curl -X POST "https://<project>.supabase.co/auth/v1/admin/users" \
  -H "apikey: <supabase-service-role-key>" \
  -H "Authorization: Bearer <supabase-service-role-key>" \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke-u2@<domain>","password":"<high-entropy>","email_confirm":true}'

# Mint bearer tokens (Supabase GoTrue sign-in) — these are the LANGGRAPH_U1_TOKEN / U2 values:
curl -X POST "https://<project>.supabase.co/auth/v1/token?grant_type=password" \
  -H "apikey: <supabase-anon-key>" \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke-u1@<domain>","password":"<...>"}' | jq -r '.access_token'
# Repeat for u2, then store both as GitHub Environment `production` secrets
# gh secret set LANGGRAPH_U1_TOKEN --env production
# gh secret set LANGGRAPH_U2_TOKEN --env production
```

Manual post-deploy sanity (no credentials needed):

```bash
curl -sf https://hc-rag-server-prod.fly.dev/ok | jq .
curl -sf https://hc-rag-server-prod.fly.dev/info | jq .
# /ok must be 200 {"ok": true} — it is readiness-gated and public.
# Every other route must 401 without a bearer token (proves SERVER_LOCAL_DEV=0 in prod).
curl -s -o /dev/null -w "%{http_code}\n" https://hc-rag-server-prod.fly.dev/threads/search
# Expected: 401
```

Artifact: the workflow uploads a **redacted** smoke log as `deploy-prod-<version>` (7-day retention) — auth headers stripped, response bodies reduced to `status + length`, synthetic accounts only.

Smoke failure does **not** auto-rollback. A red pipeline leaves the bad version running until the rollback runbook (§6) is executed by a human.

---

## 6. Rollback — manual, deterministic (and the mandatory one-time exercise)

### 6.1 Rollback runbook (human, on smoke failure or bad deploy)

> **Policy:** NO auto-rollback on smoke failure. A red pipeline leaves the bad version running. A human runs this.

```bash
# 0. Identify the previous good digest (from the last green deploy-prod log or GHCR)
#    The workflow logs the digest as ghcr.io/<owner>/<repo>@sha256:<digest> — copy the previous one.
export PREV_DIGEST="sha256:<previous-good-digest>"
export REPO="ghcr.io/<owner>/<repo>"   # e.g. ghcr.io/your-org/healthcare-rag-langgraph

# 1. Roll back by digest (immutable — not a mutable tag)
fly deploy \
  --app hc-rag-server-prod \
  --config deploy/fly.prod.toml \
  --image "${REPO}@${PREV_DIGEST}"

# 2. Wait for readiness
until curl -sf https://hc-rag-server-prod.fly.dev/ok | jq -e '.ok == true' >/dev/null; do
  echo "waiting for /ok ..."; sleep 5
done
echo "rollback: /ok is 200"

# 3. Smoke the rolled-back version (all six vars — see §5)
LANGGRAPH_DEPLOYMENT_URL=https://hc-rag-server-prod.fly.dev \
LANGGRAPH_U1_TOKEN="<synthetic-u1-bearer>" \
LANGGRAPH_U2_TOKEN="<synthetic-u2-bearer>" \
LANGSMITH_API_KEY="<lsv2_...>" \
COACH_INTERNAL_TOKEN="<internal>" \
LANGSMITH_FEEDBACK_PROJECT_ID="<uuid>" \
LANGSMITH_TRACING=false \
uv run python scripts/deployed_smoke.py --url https://hc-rag-server-prod.fly.dev

# 4. Record the rollback (evidence)
#    Append to .omo/evidence/task-12-oss-agent-server-tag-deploys.md (canonical) and
#    mirror to .omo/notepads/oss-agent-server-tag-deploys/decisions.md:
#    date, tag rolled back from, digest rolled back to, smoke result, operator.
```

### 6.2 One-time rollback exercise — mandatory after the first prod deploy

> **When:** immediately after the first production deploy goes green and its smoke passes.  
> **Why:** to prove the rollback path works before it is needed under pressure.  
> **Evidence:** paste the full output of every step below into **`.omo/evidence/task-12-oss-agent-server-tag-deploys.md`** (canonical; mirror the completion note to `decisions.md`). The exercise is not complete until the output is recorded.

```bash
# Capture digests: current (just deployed) and previous (the one before it).
# If there is no previous prod digest (first-ever deploy), use the current digest
# as both "current" and "previous" for the purpose of the exercise — the point
# is to prove `fly deploy --image ...@sha256:<digest>` + /ok + smoke roundtrips.
export CURRENT_DIGEST="sha256:<current-digest>"
export PREV_DIGEST="sha256:<previous-digest>"   # or same as CURRENT_DIGEST if none
export REPO="ghcr.io/<owner>/<repo>"

# Step 1 — deploy the previous digest (downgrade)
fly deploy --app hc-rag-server-prod --config deploy/fly.prod.toml --image "${REPO}@${PREV_DIGEST}"
until curl -sf https://hc-rag-server-prod.fly.dev/ok | jq -e '.ok == true' >/dev/null; do sleep 5; done
LANGGRAPH_DEPLOYMENT_URL=https://hc-rag-server-prod.fly.dev \
LANGGRAPH_U1_TOKEN="<synthetic-u1-bearer>" \
LANGGRAPH_U2_TOKEN="<synthetic-u2-bearer>" \
LANGSMITH_API_KEY="<lsv2_...>" \
COACH_INTERNAL_TOKEN="<internal>" \
LANGSMITH_FEEDBACK_PROJECT_ID="<uuid>" \
LANGSMITH_TRACING=false \
uv run python scripts/deployed_smoke.py --url https://hc-rag-server-prod.fly.dev
# Expect: 10/10 PASS, /ok 200 — paste the full output

# Step 2 — restore the current digest
fly deploy --app hc-rag-server-prod --config deploy/fly.prod.toml --image "${REPO}@${CURRENT_DIGEST}"
until curl -sf https://hc-rag-server-prod.fly.dev/ok | jq -e '.ok == true' >/dev/null; do sleep 5; done
LANGGRAPH_DEPLOYMENT_URL=https://hc-rag-server-prod.fly.dev \
LANGGRAPH_U1_TOKEN="<synthetic-u1-bearer>" \
LANGGRAPH_U2_TOKEN="<synthetic-u2-bearer>" \
LANGSMITH_API_KEY="<lsv2_...>" \
COACH_INTERNAL_TOKEN="<internal>" \
LANGSMITH_FEEDBACK_PROJECT_ID="<uuid>" \
LANGSMITH_TRACING=false \
uv run python scripts/deployed_smoke.py --url https://hc-rag-server-prod.fly.dev
# Expect: 10/10 PASS, /ok 200 — paste the full output

# Step 3 — record both smoke outputs in evidence and mark the exercise complete
```

If either smoke in the exercise fails, fix the rollback path before considering the hosting work done — the exercise is a gate, not a formality.

---

## 7. Rapid-tag Note — env lock serializes, GitHub is not FIFO

The `deploy-prod` job uses `concurrency: {group: deploy-production, cancel-in-progress: false}` — one production deploy runs at a time, queued jobs are not cancelled.

**But GitHub does not guarantee FIFO ordering across rapidly pushed tags.** If you push `v1.2.3` and then `v1.2.4` in quick succession, the queued `deploy-prod` runs may start in either order, and the last one to finish wins — which may not be the intended version.

**Rule:** after a burst of tags, check which version is actually running:

```bash
curl -sf https://hc-rag-server-prod.fly.dev/info | jq .
# or: fly releases --app hc-rag-server-prod
```

If the wrong tag won, **re-push the intended tag** to queue a fresh pipeline run:

```bash
git push origin v1.2.4 --force   # re-push the intended tag; pipeline re-runs deploy-prod with that tag's digest
# Or trigger via workflow_dispatch with the intended tag:
gh workflow run deploy.yml --ref main -f tag=v1.2.4
```

Do not rely on tag-push order alone. Always verify the running image:

```bash
fly status --app hc-rag-server-prod
fly image show --app hc-rag-server-prod
```

---

## 8. In-memory Wipe Caveat — every deploy/restart wipes threads, store, crons

All Agent Server state — threads (id/metadata/created/updated/expires_at), runs, store items, crons, queues — is **in-memory only** this stage (`SERVER_STORAGE=memory`). The Weaviate collection data on its 1 GB volume is the only persistent state.

Every `fly deploy`, every machine restart, every OOM or host migration **wipes** threads/store/crons:

- Thread IDs from before the restart 404.
- Runs queued or in-flight are lost.
- Store items (including uploaded-proposal reservations) are gone.
- Cron schedules are gone — they must be re-created through coach turns (the reminder flow re-creates the underlying cron after restart; no automatic cron resurrection this stage).

**Operator implication:** do not promise cross-restart continuity to users. Schedule deploys during low-traffic windows and re-run ingest only if Weaviate itself was reset (its volume preserves data across server restarts, but not across `fly volumes destroy`).

---

## 9. Future Persistence — seam limits, registries need their own migration

The storage seam (`server/storage.py:create_storage(...)` returning `saver + store + {threads,runs,crons}`) limits — but does not eliminate — future persistence work:

- The seam wraps **all** server state behind one factory. Swapping `InMemorySaver`/`InMemoryStore` for a Postgres-backed `AsyncPostgresSaver`/`AsyncPostgresStore` is a single-site change in that factory plus wiring `DATABASE_URI`.
- **Registries are not magically persisted.** Threads, runs, and crons are in-memory dicts behind the same seam today. A future persistent build must migrate those registries to durable tables (or to Postgres-backed equivalents) with their own DDL, retention, and sweep logic — the seam makes the cut point explicit but the migration is still a code + schema change.
- Weaviate's volume is the only durable piece today. A future move to Weaviate Cloud or a second volume is a separate migration with its own data copy.

Do not assume "switch `SERVER_STORAGE` to `postgres`" persists threads/crons without also migrating the registries.

---

## 10. Capacity / Overload Defaults — single-machine, tunable

Single-machine defaults (tunable via env / config):

| Resource | Default | Beyond → |
|---|---|---|
| Concurrent SSE streams (`GET /threads/{id}/runs/stream`, `.../join/stream`) | **50** | `503 Service Unavailable` + `Retry-After` |
| Queued runs (pending, server-wide FIFO) | **100** | `503` + `Retry-After` (never silent loss) |
| Threads (active) | **10 000** | `503` + `Retry-After` on `POST /threads` |
| Store items | **50 000** | `503` + `Retry-After` on `PUT /store/items` |
| Crons (registered schedules) | **500** | `409` or `503` per fixture (overflow) |
| Per-thread single active run | 1 active, rest queued | `multitask_strategy` `reject` → `409`, else enqueue/interrupt |

Overload never silently drops work — it returns `503` with `Retry-After` so callers can back off. Load tests assert these floors. Tune via `SERVER_*` env vars (see `server/config.py`).

The server is **single-machine** (`min_machines_running = 1`, `auto_stop_machines = false`); there is no autoscaling this build. If single-machine headroom is exceeded, scale up the Fly machine (`fly scale vm`) rather than adding a second machine — multi-replica state replication is not implemented.

---

## 11. Cost BOM — ~$18–25/mo + per-release smoke AI usage

| Item | Size / spec | ~Monthly cost (USD) | Notes |
|---|---|---|---|
| Fly Machine — `hc-rag-server-prod` | `shared-cpu-1x`, 512 MB–1 GB RAM, always-on (`auto_stop_machines=false`, `min_machines_running=1`) | **$5–10** | single machine, no autoscaling |
| Fly Machine — `hc-rag-weaviate-prod` | `shared-cpu-1x`, 512 MB–1 GB RAM, always-on, plus `cr.weaviate.io/semitechnologies/weaviate:1.30.2` | **$5–10** | private networking only |
| Fly Volume — `weaviate_data` | 1 GB | **$0.15** | `1 GB × $0.15/GB/mo`; the only persistent volume |
| Outbound / bandwidth | modest (API + chunks) | **$0–2** | Fly includes a small free allowance |
| **Subtotal (hosting)** | | **~$10–22** | rounded to **~$18–25/mo** with headroom in the plan |
| Per-release smoke AI usage | `scripts/deployed_smoke.py` may touch LLM retrieval via the server | **$0.01–0.10 per run** | synthetic accounts, tracing off; not a monthly fixed cost |

> The `$18–25/mo` band in the plan uses slightly larger machine sizing and leaves headroom for a memory bump. Actual Fly invoices vary with exact `vm` size and region. The per-release smoke AI usage is additional and scales with releases, not with traffic.

---

## Appendix A — File map

| File | Purpose |
|---|---|
| `deploy/fly.prod.toml` | Server Fly config — app `hc-rag-server-prod`, region `iad`, image-only, `[http_service] 8000` with `/ok` check |
| `deploy/fly.weaviate-prod.toml` | Weaviate companion — app `hc-rag-weaviate-prod`, image `1.30.2`, env from `docker-compose.yml:24-27`, volume `weaviate_data` |
| `server/Dockerfile` | Two-stage image, `SERVER_LOCAL_DEV=0` in runtime, Presidio/spaCy baked |
| `docker-compose.server.yml` | Local parity stack (server + Weaviate) |
| `.github/workflows/deploy.yml` | Tag → GHCR digest → `production` approval → `fly deploy --image ...@sha256:<digest>` → smoke |

## Appendix B — Quick command index

```bash
# Bootstrap
fly apps create hc-rag-server-prod --org "$FLY_ORG"
fly apps create hc-rag-weaviate-prod --org "$FLY_ORG"
fly volumes create weaviate_data --app hc-rag-weaviate-prod --region iad --size 1
fly deploy --config deploy/fly.weaviate-prod.toml --image cr.weaviate.io/semitechnologies/weaviate:1.30.2 --app hc-rag-weaviate-prod
fly tokens create deploy -x 8760h -a hc-rag-server-prod   # AFTER apps exist
fly secrets set --app hc-rag-server-prod OPENAI_API_KEY="<...>" SUPABASE_URL="<...>" SUPABASE_SERVICE_KEY="<...>" COACH_INTERNAL_TOKEN="<...>" CORS_ALLOW_ORIGINS="<...>" COACH_ALLOWED_ORIGINS="<...>" LANGSMITH_API_KEY="<...>" LANGSMITH_FEEDBACK_PROJECT_ID="<...>" LANGGRAPH_DEPLOYMENT_URL="<...>" LANGGRAPH_U1_TOKEN="<...>" LANGGRAPH_U2_TOKEN="<...>"
fly secrets list --app hc-rag-server-prod

# Release (strict vX.Y.Z for prod; -rc allowed only for local hermetic probes)
make release TAG=v1.2.3
git tag v1.2.3 && git push origin v1.2.3
gh workflow run deploy.yml --ref main -f tag=v1.2.3   # alternative dispatch

# Deploy (pipeline runs this; manual equivalent)
fly deploy --app hc-rag-server-prod --config deploy/fly.prod.toml --image ghcr.io/<owner>/<repo>@sha256:<digest>

# Ingest (make ingest-fly prints the command; real ingest is fly machines run with digest)
make ingest-fly
fly machines run ghcr.io/<owner>/<repo>@sha256:<digest> --app hc-rag-server-prod --region iad --rm --env WEAVIATE_HOST=hc-rag-weaviate-prod.internal --env WEAVIATE_PORT=8080 --command "python -m healthcare_rag.storage.vector_store --delete-all --collection Lipitor data/chunks_lipitor.json --collection Metformin data/chunks_metformin.json"

# Smoke (all six vars required)
curl -sf https://hc-rag-server-prod.fly.dev/ok | jq .
LANGGRAPH_DEPLOYMENT_URL=https://hc-rag-server-prod.fly.dev LANGGRAPH_U1_TOKEN="<...>" LANGGRAPH_U2_TOKEN="<...>" LANGSMITH_API_KEY="<...>" COACH_INTERNAL_TOKEN="<...>" LANGSMITH_FEEDBACK_PROJECT_ID="<...>" LANGSMITH_TRACING=false uv run python scripts/deployed_smoke.py --url https://hc-rag-server-prod.fly.dev

# Rollback
fly deploy --app hc-rag-server-prod --config deploy/fly.prod.toml --image ghcr.io/<owner>/<repo>@sha256:<previous>
curl -sf https://hc-rag-server-prod.fly.dev/ok | jq .
LANGGRAPH_DEPLOYMENT_URL=https://hc-rag-server-prod.fly.dev LANGGRAPH_U1_TOKEN="<...>" LANGGRAPH_U2_TOKEN="<...>" LANGSMITH_API_KEY="<...>" COACH_INTERNAL_TOKEN="<...>" LANGSMITH_FEEDBACK_PROJECT_ID="<...>" LANGSMITH_TRACING=false uv run python scripts/deployed_smoke.py --url https://hc-rag-server-prod.fly.dev

# Status
fly status --app hc-rag-server-prod
fly status --app hc-rag-weaviate-prod
fly volumes list --app hc-rag-weaviate-prod
fly logs --app hc-rag-server-prod
```
