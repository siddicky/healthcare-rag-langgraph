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

## 0b. Postgres Activation Checklist — staged flip to release N+1 (human-gated, NOT part of this PR)

> **This PR is release N — `SERVER_STORAGE=memory` stays live.** The code for Postgres persistence is **delivered but not active**: `server/storage.py: create_storage(...)` now has a `postgres` path, `server/config.py` accepts `SERVER_STORAGE=postgres`, and `DATABASE_URI` wiring + `hc_*` DDL + pgvector are in the image. None of that is active in production until a human deliberately completes this checklist and ships release **N+1**. Do not flip `deploy/fly.prod.toml` until every box below is checked and recorded.

Follow this checklist **in order**. Each step is a gate — do not proceed if it fails.

**1 — Provision Fly Postgres (todo 18's exact commands):**

```bash
# Create an unmanaged single-node Postgres in the same org/region as the prod apps
fly postgres create \
  --name hc-rag-pg-prod \
  --org "$FLY_ORG" \
  --region iad \
  --vm-size shared-cpu-1x \
  --volume-size 10 \
  --initial-cluster-size 1

# Attach it to the server app — this creates DATABASE_URL (and DATABASE_URI alias)
# inside hc-rag-server-prod automatically; do NOT set DATABASE_URI manually via fly secrets set
fly postgres attach hc-rag-pg-prod --app hc-rag-server-prod
# Fly prints: Postgres cluster hc-rag-pg-prod is now attached to hc-rag-server-prod
# with the following secrets set: DATABASE_URL

# Verify attach succeeded (names only — value is never echoed)
fly secrets list --app hc-rag-server-prod | grep -E 'DATABASE_(URL|URI)'
```

**2 — Verify isolation + pgvector extension:**

```bash
# No public IP must be allocated to the Postgres app
fly ips list --app hc-rag-pg-prod
# Expected: no public v4/v6 — only private .internal DNS

# Confirm pgvector works (connect via Fly's private network)
fly postgres connect --app hc-rag-pg-prod -d postgres -c "CREATE EXTENSION IF NOT EXISTS vector; SELECT extname, extversion FROM pg_extension WHERE extname='vector';"
# Expected: one row showing vector extension installed
```

If either check fails, stop — do not continue. Re-create or debug the Postgres app before proceeding.

**3 — Record durability/PHI sign-off in `.omo/evidence/` per §0 convention:**

Add a dated entry to **`.omo/evidence/task-12-oss-agent-server-tag-deploys.md`** (canonical) and mirror to **`.omo/notepads/oss-agent-server-tag-deploys/decisions.md`**, stating the reviewer, date, and that you explicitly accept:

- **Scrubbed conversation state now persists.** Thread messages, resumable state, and store items that today vanish on every deploy will survive restarts once `SERVER_STORAGE=postgres` is live. Only scrubbed forms persist (PHI is scrubbed before storage), but durability itself is the change being accepted — data at rest now exists.
- **Cron records embed the `cron_wake` token at rest.** Each reminder cron stores its rotating `cron_wake` token in the `hc_crons` row. With Postgres, these tokens survive restarts (they are ephemeral today). Accept that cron continuity now means token-bearing rows at rest.
- **Retention is live-rows-only with no automated backups.** There is no nightly backup, no point-in-time recovery, and no automated purge job in this build. Retention is: live rows in `hc_*` tables until explicitly deleted (thread eviction, cron removal, store delete). Accept that operational backups and retention policy are operator-owned beyond this PR. Name these three items verbatim in the sign-off.

**4 — Confirm regression gate evidence is still current:**

```bash
cat .omo/evidence/regression-gate.txt
# Must exist and match the HEAD that ships N+1 (todo 17's gate).
# If any commit since the last regression gate changed retriever/reranker/safety/validation code,
# re-run the gate: make eval-compatible checks + re-record regression-gate.txt before flipping.
```

Do not flip if `regression-gate.txt` is stale or missing — re-run the gate.

**5 — Flip and ship release N+1 (only after 1-4 are done and recorded):**

```bash
# In deploy/fly.prod.toml, change exactly one value:
#   SERVER_STORAGE = "memory"  →  SERVER_STORAGE = "postgres"
# Commit: docs(deploy): activate Postgres persistence (N+1)
# Then:
make release TAG=vX.Y.Z   # hermetic validation — prints git tag/push, does not push
git tag vX.Y.Z && git push origin vX.Y.Z   # triggers deploy.yml → production approval → prod
# Post-deploy, verify persistence is live (see §8) and re-run smoke (see §5).
```

> **Flip requires all four gates.** Provisioning + isolation check + sign-off + regression gate — missing any one blocks the flip. The value change itself is a one-line TOML edit, but the sign-off is the authorization that makes it legitimate.

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

The coach reminder tools also call back into the Agent Server itself
(`healthcare_rag/agent/reminders.py` defaults to `localhost:2024`, the
`langgraph dev` port). The prod config pins the loopback to the server's own
port — do not remove it, or every reminder create/edit silently fails with
"reminder service unavailable":

```toml
# deploy/fly.prod.toml [env]
LANGGRAPH_API_URL = "http://127.0.0.1:8000"
```

The same variable must be set for any local OSS-server run on a non-2024 port
(e.g. `LANGGRAPH_API_URL=http://127.0.0.1:8000 make server-test-live`).

No public Weaviate URL is exposed. Scaling or re-creating the volume is a separate migration (not covered here).

### 1.2b Postgres provisioning — DOCUMENTED STEPS for later activation (NOT executed in this PR)

> **Staged rollout.** Production stays `SERVER_STORAGE=memory` in this release (N). The steps below set up Fly Postgres for the **future** flip to `SERVER_STORAGE=postgres` (release N+1). They are instructions for the human operator to run **later** when executing the checklist in **§0b** — do not run them now, and do not change `deploy/fly.prod.toml` in this PR.

When the activation checklist (§0b) says to provision, run exactly:

```bash
# 1. Create the Postgres cluster (unmanaged single-node, same org/region as prod)
fly postgres create \
  --name hc-rag-pg-prod \
  --org "$FLY_ORG" \
  --region iad \
  --vm-size shared-cpu-1x \
  --volume-size 10 \
  --initial-cluster-size 1

# 2. Attach to the server app — Fly sets DATABASE_URL (and DATABASE_URI alias) automatically
fly postgres attach hc-rag-pg-prod --app hc-rag-server-prod
# Fly output: Postgres cluster hc-rag-pg-prod is now attached ... secrets set: DATABASE_URL

# 3. Verify attach + isolation (same checks as §0b step 2)
fly secrets list --app hc-rag-server-prod | grep -E 'DATABASE_(URL|URI)'
fly ips list --app hc-rag-pg-prod
# Expected: no public v4/v6 — only private .internal DNS
fly postgres connect --app hc-rag-pg-prod -d postgres -c "CREATE EXTENSION IF NOT EXISTS vector; SELECT extname FROM pg_extension WHERE extname='vector';"
```

> **Do not set `DATABASE_URI` / `DATABASE_URL` manually via `fly secrets set`.** The `fly postgres attach` command creates them. If you rotate the database, re-attach — do not hand-edit the secret. See §1.3 for the secrets table entry and §0b for the full flip sequence including the compliance sign-off.

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
| `COACH_ALLOWED_ORIGINS` | yes | `<allowed-origins>` | must contain the deployed frontend origin, e.g. `https://<frontend>` |
| `CORS_ALLOW_ORIGINS` | yes | `<cors-origins>` | must contain the deployed frontend origin **and** `https://smith.langchain.com` (Studio Connect panel); keep the frontend origin aligned with `COACH_ALLOWED_ORIGINS` |
| `LANGSMITH_FEEDBACK_PROJECT_ID` | yes | `<uuid>` | `00000000-...` shape — required by smoke (`COACH_INTERNAL_TOKEN`/`LANGSMITH_FEEDBACK_PROJECT_ID` both required); if feedback project not yet configured, create one and use its UUID |
| `LANGGRAPH_DEPLOYMENT_URL` | yes | `<https://hc-rag-server-prod.fly.dev>` | public prod URL |
| `LANGSMITH_API_KEY` | yes (for smoke) | `<lsv2_...>` | required by `scripts/deployed_smoke.py` even when `LANGSMITH_TRACING=false` |
| `LANGSMITH_PROJECT` | if tracing | `<healthcare-rag>` | optional |
| `LANGGRAPH_U1_TOKEN` | yes (smoke) | `<synthetic-u1-bearer>` | synthetic Supabase user JWT — see §5 for provisioning |
| `LANGGRAPH_U2_TOKEN` | yes (smoke) | `<synthetic-u2-bearer>` | synthetic Supabase user JWT — see §5 |
| `SUPABASE_JWT_SECRET` | if used | `<jwt-secret>` | only if auth needs it |
| `DATABASE_URI` / `DATABASE_URL` | attach-provided (N+1 only) | *(not set manually — see below)* | Set automatically by `fly postgres attach hc-rag-pg-prod --app hc-rag-server-prod` (see §1.2b / §0b). **Do NOT** add via `gh secret set` or `fly secrets set`. As of this release (N) production still runs `SERVER_STORAGE=memory` and this row is **not present**; it appears only after the human operator provisions Postgres and completes the activation checklist for release N+1. |

> **About `DATABASE_URI` / `DATABASE_URL`:** Fly's `postgres attach` creates `DATABASE_URL`; the server also reads `DATABASE_URI` as an alias (either name works — see `server/config.py`). Fly injects the value directly into the server app's secrets — there is no GitHub Environment `production` entry for it and the deploy workflow does not sync it. Do not create a GitHub secret for it and do not paste a connection string into any doc or env file — use the placeholder `<postgres-uri>` only if you must refer to it.

> **Origin alignment contract:** `COACH_ALLOWED_ORIGINS` and `CORS_ALLOW_ORIGINS` must both contain the deployed frontend origin (`https://<frontend>`). `CORS_ALLOW_ORIGINS` must additionally contain `https://smith.langchain.com` for the LangSmith Studio Connect panel. CORS wraps auth (outermost) so preflight `OPTIONS` succeed unauthenticated and every response, including `401`, carries CORS headers — the browser can read an expired-token `401` and refresh the session. `NEXT_PUBLIC_LANGGRAPH_URL` must equal the server origin the browser calls (same value as `LANGGRAPH_DEPLOYMENT_URL` in prod). Use placeholder syntax only (e.g. `https://<frontend>`, `https://smith.langchain.com`) — never a real secret value.

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

**Repository secret (not an environment secret): `RELEASE_TAG_TOKEN`.** The Release
workflow (§3.1) pushes the tag with it; a tag pushed with `GITHUB_TOKEN` triggers no
workflow, so the deploy would never start. Fine-grained PAT, this repo only,
`contents: write`, nothing else — it can create a tag, not deploy one.

```bash
gh secret set RELEASE_TAG_TOKEN            # repo scope, NOT --env production
gh secret list | grep RELEASE_TAG_TOKEN
```

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

### 3.1 Cut the release (the tag is created for you)

```bash
make next-version                 # preview: current tag, bump, next tag, commits driving it
make next-version BUMP=minor      # preview an explicit bump

# If pyproject.toml is behind the version being cut, prep it first — this lands
# as an ordinary PR, and it is where you get to disagree with the number:
make release-prep BUMP=minor      # bumps pyproject + uv.lock, prints the commit/PR commands

# Then cut it (dispatch from main):
gh workflow run release.yml -f bump=minor
gh workflow run release.yml -f bump=auto -f dry_run=true   # plan only, tags nothing
```

`bump=auto` derives the version from the Conventional Commit subjects since the
last tag: `feat!`/`BREAKING CHANGE:` → major (minor while the major is still 0),
`feat` → minor, everything else → patch. `make next-version` and the workflow run
the same `scripts/next_version.py`, so the preview is the number CI will pick.

The Release job refuses to tag unless: the dispatch is on `main`; the tag does not
already exist on origin (release tags are immutable); `pyproject.toml` already
carries the version being tagged; and the commit is green — no red or pending
checks, with the offline suite green on that exact commit. It then creates an
**annotated** tag whose message is the release notes, pushes it, and publishes a
GitHub Release.

> **Requires the `RELEASE_TAG_TOKEN` secret** — a fine-grained PAT with
> `contents: write` on this repo. GitHub raises no workflow-triggering events for
> refs pushed with `GITHUB_TOKEN`, so a tag created with the default token would
> never deploy. The job fails closed with that explanation rather than creating a
> tag that ships nothing. Without the secret, tag locally (§3.2).
>
> The token can create tags; it cannot deploy. The `production` environment's
> required reviewers still gate what any tag ships.

### 3.2 Tagging by hand (fallback)

`make release TAG=vX.Y.Z` validates the format and prints the commands without
pushing. Use this only when the Release workflow cannot run:

```bash
make release TAG=v1.2.3
git tag -a v1.2.3 -m "release v1.2.3"
git push origin v1.2.3
# The workflow triggers on push: tags: ['v*.*.*']. workflow_dispatch on the
# Deploy workflow is rollback only (§6.1).
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

### 3.4 Tag taxonomy — what is mutable, and what may be deployed

Full rationale: `docs/decisions/release-tags-and-rollback.md`. The rules are asserted
against the workflow by `tests/agent/test_release_pipeline.py`.

| ref | example | mutability | may a deploy consume it? |
|---|---|---|---|
| git tag | `v1.2.3` | **immutable** — never re-pointed | yes: it selects the release |
| image `{{version}}` | `ghcr.io/<repo>:1.2.3` | **immutable** — one tag, one build, one digest | only to *resolve* a digest |
| image `{{major}}.{{minor}}` | `ghcr.io/<repo>:1.2` | rolling | no — humans reading the registry only |
| image `sha-<short>` | `ghcr.io/<repo>:sha-e414846` | immutable | traceability only |
| image digest | `ghcr.io/<repo>@sha256:…` | immutable by construction | **yes — the only thing ever deployed** |
| `latest` | — | — | does not exist, deliberately |

- **Machines deploy digests, humans read tags.** `fly deploy` never receives a tag; both
  the tag deploy and the rollback validate `^sha256:[0-9a-f]{64}$` before deploying.
- **A release is a triple:** `(git tag, image digest, deploy/fly.prod.toml at that tag)`.
  Config travels with the image — that is what makes §6.3's `SERVER_STORAGE` trap
  unreachable from the rollback workflow.
- **Never delete a release tag from GHCR.** The immutable `{{version}}` tag *is* the
  ledger that maps a release to its digest; an untagged digest is eligible for registry
  GC, and GC-ing one deletes a rollback target.
- `workflow_dispatch` on this workflow is **rollback only** (§6.1). It never builds: the
  `build` job is gated to `github.event_name == 'push'`.
- **A `-rc` suffix deploys to production like any other tag.** `tags: ["v*.*.*"]` and
  the environment's tag policy both match `v1.2.3-rc1`. Deliberate: there is one
  deployed environment, so the suffix is a human label about confidence, not a routing
  rule. Note that `make next-version` only ever computes final versions — a prerelease
  is a deliberate hand-tagged act (§3.2). If a staging app is ever added, tighten the
  policy to final versions only.

Resolve any release to its digest locally:

```bash
make release-digest TAG=v1.2.2
# ghcr.io/<owner>/<repo>:1.2.2
# digest: sha256:<64 hex>
```


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

Smoke failure does **not** auto-rollback. A red pipeline leaves the bad version running until a human dispatches the rollback (§6.1) — the failing step prints the exact `gh workflow run` command.

---

## 6. Rollback — one dispatch, human-decided (and the mandatory one-time exercise)

### 6.1 Rollback (on smoke failure or a bad deploy)

> **Policy:** NO auto-rollback. A red smoke leaves the bad version live and a human
> decides — a flaky smoke that redeploys prod on its own is a worse failure than a bad
> version sitting still for the minutes it takes to click. What follows is only the
> cheapest way to make that decision act.

```bash
# 0. Pick the release to go back to (the previous green tag).
make rollback TAG=v1.2.2 REASON="smoke red on v1.2.3"
# Prints the exact dispatch command; pushes nothing.

# 1. Dispatch it.
gh workflow run deploy.yml -f version=v1.2.2 -f reason="smoke red on v1.2.3"

# 2. Approve the `production` environment gate in the Actions UI (same reviewer
#    as a forward deploy — a rollback is a prod change).
```

The `rollback` job then, without rebuilding anything:

1. validates the version, verifies the tag exists on origin and is reachable from `main`;
2. **checks the repo out at that tag**, so the image and `deploy/fly.prod.toml` come from
   the same release (this is what makes §6.3's trap unreachable here);
3. resolves `ghcr.io/<owner>/<repo>:<X.Y.Z>` → digest, validating the sha256 shape;
4. mirrors it into the Fly registry and `fly deploy`s it **by digest**;
5. waits for `/ok`, re-runs the deployed smoke (§5), uploads the redacted log as
   `rollback-prod-<version>`;
6. writes the evidence record — target, commit, source digest, Fly digest, smoke result,
   operator, reason — to the job summary.

It deliberately does **not** re-sync secrets from the GitHub Environment: a rollback must
be able to recover from a bad secret sync, and re-applying the current environment during
one would re-apply the fault. If the rollback is *for* a secret, fix the environment first,
then dispatch.

Break-glass, when the release's GHCR tag no longer resolves (someone deleted it — see
§3.4): pass the digest explicitly.

```bash
gh workflow run deploy.yml -f version=v1.2.2 -f reason="..." -f image_digest=sha256:<64hex>
# The config still comes from v1.2.2; only digest resolution is bypassed.
```

If the smoke fails on the rolled-back version too, the job goes red and stops. Prod is now
running the older release and is still unhealthy — escalate; do not dispatch a third
version blindly.

### 6.1b Manual fallback (only when Actions itself is unavailable)

The workflow is the supported path. Use this only if GitHub Actions is down — and mind
§6.3, because here nothing pairs the config to the image for you.

```bash
export PREV_DIGEST="sha256:<previous-good-digest>"   # make release-digest TAG=v1.2.2
export REPO="ghcr.io/<owner>/<repo>"

# Check out the target release first — the TOML must match the image (§6.3).
git checkout v1.2.2 -- deploy/fly.prod.toml

fly deploy \
  --app hc-rag-server-prod \
  --config deploy/fly.prod.toml \
  --image "${REPO}@${PREV_DIGEST}"

until curl -sf https://hc-rag-server-prod.fly.dev/ok | jq -e '.ok == true' >/dev/null; do
  echo "waiting for /ok ..."; sleep 5
done
echo "rollback: /ok is 200"

# Smoke the rolled-back version (all six vars — see §5)
LANGGRAPH_DEPLOYMENT_URL=https://hc-rag-server-prod.fly.dev \
LANGGRAPH_U1_TOKEN="<synthetic-u1-bearer>" \
LANGGRAPH_U2_TOKEN="<synthetic-u2-bearer>" \
LANGSMITH_API_KEY="<lsv2_...>" \
COACH_INTERNAL_TOKEN="<internal>" \
LANGSMITH_FEEDBACK_PROJECT_ID="<uuid>" \
LANGSMITH_TRACING=false \
uv run python scripts/deployed_smoke.py --url https://hc-rag-server-prod.fly.dev

# Record it by hand — the dispatch path writes this record for you:
#   date, tag rolled back from, digest rolled back to, smoke result, operator.
```

### 6.2 One-time rollback exercise — mandatory after the first prod deploy

> **When:** immediately after the first production deploy goes green and its smoke passes.
> **Why:** to prove the rollback path works before it is needed under pressure — and the
> path that must be proven is the dispatch (§6.1), because that is the one an operator
> will reach for at 3am.
> **Evidence:** the two runs record themselves (job summary + `rollback-prod-<version>`
> artifact). Link both run URLs in **`.omo/evidence/task-12-oss-agent-server-tag-deploys.md`**
> (canonical; mirror the completion note to `decisions.md`). The exercise is not complete
> until both links are recorded.

```bash
# Step 1 — roll back to the previous release and prove it serves.
gh workflow run deploy.yml -f version=v1.2.2 -f reason="one-time rollback exercise (step 1/2)"
# Approve the production gate. Expect: /ok 200, smoke 10/10, green job.

# Step 2 — roll forward to the release that is meant to be live.
gh workflow run deploy.yml -f version=v1.2.3 -f reason="one-time rollback exercise (step 2/2)"
# Approve. Expect: /ok 200, smoke 10/10, green job.

# Step 3 — link both run URLs in the evidence file and mark the exercise complete.
```

On a first-ever deploy there is no previous release: dispatch the *current* tag for both
steps. The point is to prove the dispatch → approval → digest resolution → deploy → `/ok`
→ smoke round trip, not that the version changed.

If either run fails, fix the rollback path before considering the hosting work done — the
exercise is a gate, not a formality.

### 6.3 Rollback trap — `SERVER_STORAGE=postgres` vs pre-Postgres images

> **Staged-rollout trap.** After the flip to release N+1 (`SERVER_STORAGE=postgres` in `deploy/fly.prod.toml` + `DATABASE_URL` from `fly postgres attach`), rolling back to an image **predating this PR** while `SERVER_STORAGE` is still `postgres` will **fail to boot**. The old image's `server/config.py` does not accept `"postgres"` as a valid `SERVER_STORAGE` value — the container exits during config validation, `/ok` never becomes 200, and the rollback looks like an outage.

> **The §6.1 dispatch already handles this**: it checks the repo out at the target tag, so the image and its `deploy/fly.prod.toml` always come from the same release. What follows matters for the §6.1b manual fallback, and for understanding why the pairing rule exists.

**Safe rollback pairs:**

| Desired state | What to deploy | Env to keep |
|---|---|---|
| Roll back to a pre-Postgres image (before this PR) | `fly deploy --image ghcr.io/<repo>@sha256:<pre-pg-digest>` | Must also set `SERVER_STORAGE=memory`: `fly secrets set --app hc-rag-server-prod SERVER_STORAGE=memory` is **not** correct — `SERVER_STORAGE` is an `[env]` in `deploy/fly.prod.toml`, not a Fly secret. Instead, re-deploy with a TOML that has `SERVER_STORAGE = "memory"` (e.g. `git checkout <pre-pg-tag> -- deploy/fly.prod.toml && fly deploy --config deploy/fly.prod.toml --image ...@sha256:<pre-pg-digest>`), or cherry-pick the `fly.prod.toml` from before the flip. |
| Roll back within Postgres-capable history (this PR onward) | Any digest at or after this PR | Keep `SERVER_STORAGE=postgres` as shipped in that digest's TOML — no extra step |

> **Simplest safe rollback after Postgres is live:** stay within Postgres-capable digests. If you must go pre-Postgres, first restore `deploy/fly.prod.toml` to `SERVER_STORAGE = "memory"` (the value as of this PR, release N) and deploy that TOML with the old digest — do not leave the env claiming `postgres` while the image only knows `memory`.

**How to tell which digests are Postgres-capable:**

```bash
# A Postgres-capable image has 'postgres' in its server config
# Check without deploying — inspect the built image locally:
docker run --rm --entrypoint python ghcr.io/<owner>/<repo>@sha256:<digest> -c "from server.config import Settings; print(Settings.model_fields['server_storage'].annotation)"
# Postgres-capable → shows Literal including 'postgres'; pre-Postgres → only 'memory'
```

Record which rollback path was chosen in the same evidence file as §6.1.

---

## 7. Rapid-tag Note — env lock serializes, GitHub is not FIFO

The `deploy-prod` job uses `concurrency: {group: deploy-production, cancel-in-progress: false}` — one production deploy runs at a time, queued jobs are not cancelled.

**But GitHub does not guarantee FIFO ordering across rapidly pushed tags.** If you push `v1.2.3` and then `v1.2.4` in quick succession, the queued `deploy-prod` runs may start in either order, and the last one to finish wins — which may not be the intended version.

**Rule:** after a burst of tags, check which version is actually running:

```bash
curl -sf https://hc-rag-server-prod.fly.dev/info | jq .
# or: fly releases --app hc-rag-server-prod
```

If the wrong tag won, **dispatch the intended one**. The §6.1 job deploys any released
tag's image digest and its `deploy/fly.prod.toml`, so re-asserting a version is the same
motion as rolling back to one:

```bash
gh workflow run deploy.yml -f version=v1.2.4 -f reason="v1.2.3 won the race; re-asserting the intended release"
```

> **Never force-push a git tag to re-run a deploy.** A re-pointed tag would name a
> different commit than the image already published under that version, and the immutable
> `ghcr.io/<repo>:1.2.4` → digest mapping the rollback path relies on would start lying
> (§3.4). If a release is wrong, cut the next version.

Do not rely on tag-push order alone. Always verify the running image:

```bash
fly status --app hc-rag-server-prod
fly image show --app hc-rag-server-prod
```

---

## 8. Durability Caveat — AS OF THIS RELEASE (N) every deploy/restart still wipes; durable reality only after the flip to N+1

> **Read this carefully — three states matter.** Code capability (what this PR delivers) vs production state (what is live today) vs activated durability (what happens after the human-gated flip). Confusing them is the main risk this section exists to prevent.

**As of this release (N) — what production actually does TODAY:**

Production still runs `SERVER_STORAGE=memory` (see `deploy/fly.prod.toml` — flipped only in release N+1 per §0b). So **every `fly deploy`, every machine restart, every OOM or host migration still wipes** all Agent Server state — exactly as before this PR:

- Thread IDs from before the restart 404.
- Runs queued or in-flight are lost.
- Store items (including uploaded-proposal reservations) are gone.
- Cron schedules are gone — they must be re-created through coach turns (the reminder flow re-creates the underlying cron after restart; no automatic cron resurrection).

**Operator implication for release N:** do not promise cross-restart continuity to users. Schedule deploys during low-traffic windows and re-run ingest only if Weaviate itself was reset (its volume preserves data across server restarts, but not across `fly volumes destroy`).

**After the flip to release N+1 (`SERVER_STORAGE=postgres`) — the delivered durable reality:**

Once the human operator completes §0b and ships N+1, the same `server/storage.py` factory that today returns `InMemorySaver`/`InMemoryStore` + in-memory dicts instead returns `AsyncPostgresSaver`/`AsyncPostgresStore` + `hc_*` tables (see §9). Then:

- Threads (id/metadata/created/updated/expires_at), store items, and cron registrations **survive** machine restarts and deploys — they are rows in Postgres, not process memory. Weaviate's 1 GB volume remains durable alongside them.
- Queued/in-flight **run** state is still not durable the way threads are — a deploy still drains the old machine.

**Residual ephemera that remain even after the eventual flip:**

Even with Postgres, these never become durable — they are process-local by nature:

- **In-flight runs** — a run actively executing during a deploy dies with the old machine; the client must retry. The DB retains the thread/store/cron rows, but the compute for that turn is lost.
- **Queues** — the pending-run FIFO lives in process memory. A deploy drops the queue; callers get `503 + Retry-After` or must re-submit.
- **SSE streams** — `GET /threads/{id}/runs/stream` and `.../join/stream` are live HTTP connections pinned to one machine. A deploy breaks every open stream; the browser must reconnect and re-join.
- **Deploy-overlap transient** — Fly may briefly run two machines (old + new) during a rolling deploy. A `running` record written on the old machine can **404** when read through the new process for a few seconds until the old machine drains. This is inherent to single-machine + rolling deploys and survives the Postgres migration — do not treat a brief `running`-404 as data loss.

> **Summary:** this PR delivers durable code (release N) but production is still ephemeral (`SERVER_STORAGE=memory`). Durability only takes effect after the separate, human-gated flip to N+1 per §0b. Until then, every deploy still wipes Agent Server state.

---

## 9. Delivered Persistence Design — code capability NOW EXISTS; activation is N+1

> **What NOW EXISTS IN CODE (this PR, release N) vs what is ACTIVE in production (still memory).** Everything below is **shipped in the image** and ready to activate — but it does nothing in production until the §0b flip sets `SERVER_STORAGE=postgres`. Today production still reads `SERVER_STORAGE=memory` and takes the in-memory path.

**Delivered design (in the image as of this PR):**

- **Single factory, two paths.** `server/storage.py:create_storage(...)` now has both arms: `memory` returns `InMemorySaver`/`InMemoryStore` + in-memory dicts `{threads,runs,crons}`; `postgres` returns `AsyncPostgresSaver`/`AsyncPostgresStore` against `DATABASE_URI` (or `DATABASE_URL` alias) plus **durable `hc_*` tables** that replace those dicts. `server/config.py` validates `SERVER_STORAGE` as `Literal["memory","postgres"]` — the `postgres` value is accepted as of this PR (pre-Postgres images reject it; see §6.3).
- **Registries are migration-complete — not future work.** The `hc_*` DDL (threads, runs, crons, store) ships in this PR and is applied on the `postgres` path. The seam made the cut point explicit; the migration is **delivered**, not pending. Switching `SERVER_STORAGE` to `postgres` therefore **does** persist threads/store/crons — the caveat in earlier docs ("registries need their own migration") is resolved as of this PR. What remains ephemeral even after activation is only the process-local pieces named in §8.
- **Run-input redaction divergence between modes (deliberate).** `memory` keeps the existing posture — run inputs live in process memory and vanish on restart. `postgres` persists a **redacted** form of run inputs (identifiers scrubbed before write, aligned with `docs/safety.md`'s scrubbing guarantees). The two modes diverge on purpose: memory has no at-rest copy to redact, postgres has a scrubbed one. Do not assume the wire-visible input equals the at-rest row — logging and storage both go through the scrubber.
- **Weaviate's 1 GB volume** remains durable alongside Postgres once activated; it is unchanged by the storage-mode switch. A future move to Weaviate Cloud is a separate migration with its own data copy (not covered here).

**What is NOT yet active in production (as of release N):**

- No Postgres cluster exists until the operator runs §0b/§1.2b. No `DATABASE_URL`/`DATABASE_URI` is set. All three states above sit dormant — the image carries them, but `deploy/fly.prod.toml` still ships `SERVER_STORAGE = "memory"` and that is what Fly runs.

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

## 11. Cost BOM — ~$18–25/mo today (N); ~$23–35/mo once Postgres is activated (N+1)

| Item | Size / spec | ~Monthly cost (USD) | Notes |
|---|---|---|---|
| Fly Machine — `hc-rag-server-prod` | `shared-cpu-1x`, 512 MB–1 GB RAM, always-on (`auto_stop_machines=false`, `min_machines_running=1`) | **$5–10** | single machine, no autoscaling |
| Fly Machine — `hc-rag-weaviate-prod` | `shared-cpu-1x`, 512 MB–1 GB RAM, always-on, plus `cr.weaviate.io/semitechnologies/weaviate:1.30.2` | **$5–10** | private networking only |
| Fly Volume — `weaviate_data` | 1 GB | **$0.15** | `1 GB × $0.15/GB/mo`; the only persistent volume today |
| Fly Postgres — `hc-rag-pg-prod` *(N+1 only, cost once activated)* | `shared-cpu-1x`, 10 GB volume, unmanaged single-node (`fly postgres create --vm-size shared-cpu-1x --volume-size 10 --initial-cluster-size 1`) | **~$5–10** | **Not spent yet — as of this release (N) production still runs `SERVER_STORAGE=memory` and this cluster does not exist.** Provisioned by the human operator in §0b/§1.2b for release N+1; includes `vector` (pgvector) extension. Fly Postgres is single-node, no automatic backups; see §0b sign-off item 3 |
| Outbound / bandwidth | modest (API + chunks) | **$0–2** | Fly includes a small free allowance |
| **Subtotal — as of this PR (N, still memory)** | | **~$10–22** | rounded to **~$18–25/mo** with headroom in the plan |
| **Subtotal — once Postgres activated (N+1)** | | **~$15–32** | rounded to **~$23–35/mo** with headroom — the +$5–10 is the Postgres line above |
| Per-release smoke AI usage | `scripts/deployed_smoke.py` may touch LLM retrieval via the server | **$0.01–0.10 per run** | synthetic accounts, tracing off; not a monthly fixed cost |

> The `$18–25/mo` band (release N) uses slightly larger machine sizing and leaves headroom for a memory bump. The `$23–35/mo` band (release N+1) is the same plus the unmanaged single-node Postgres add-on. Actual Fly invoices vary with exact `vm` size and region. Until the §0b checklist is completed, the Postgres cost is **$0 — it is not spent**. Per-release smoke AI usage is additional and scales with releases, not with traffic.

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

# Release (§3.1 — the workflow creates the tag)
make next-version                                    # preview the computed version
make release-prep BUMP=minor                         # bump pyproject + uv.lock (PR it)
gh workflow run release.yml -f bump=minor            # cut it
gh workflow run release.yml -f bump=auto -f dry_run=true   # plan only
# Fallback, no RELEASE_TAG_TOKEN / Actions down (§3.2):
make release TAG=v1.2.3 && git tag -a v1.2.3 -m "release v1.2.3" && git push origin v1.2.3

# Deploy (pipeline runs this; manual equivalent)
fly deploy --app hc-rag-server-prod --config deploy/fly.prod.toml --image ghcr.io/<owner>/<repo>@sha256:<digest>

# Ingest (make ingest-fly prints the command; real ingest is fly machines run with digest)
make ingest-fly
fly machines run ghcr.io/<owner>/<repo>@sha256:<digest> --app hc-rag-server-prod --region iad --rm --env WEAVIATE_HOST=hc-rag-weaviate-prod.internal --env WEAVIATE_PORT=8080 --command "python -m healthcare_rag.storage.vector_store --delete-all --collection Lipitor data/chunks_lipitor.json --collection Metformin data/chunks_metformin.json"

# Smoke (all six vars required)
curl -sf https://hc-rag-server-prod.fly.dev/ok | jq .
LANGGRAPH_DEPLOYMENT_URL=https://hc-rag-server-prod.fly.dev LANGGRAPH_U1_TOKEN="<...>" LANGGRAPH_U2_TOKEN="<...>" LANGSMITH_API_KEY="<...>" COACH_INTERNAL_TOKEN="<...>" LANGSMITH_FEEDBACK_PROJECT_ID="<...>" LANGSMITH_TRACING=false uv run python scripts/deployed_smoke.py --url https://hc-rag-server-prod.fly.dev

# Rollback (§6.1 — dispatch, then approve the production gate)
make rollback TAG=v1.2.2 REASON="smoke red on v1.2.3"     # prints the command, dispatches nothing
gh workflow run deploy.yml -f version=v1.2.2 -f reason="smoke red on v1.2.3"
make release-digest TAG=v1.2.2                            # resolve a release to its digest
# Break-glass, GHCR tag gone: add -f image_digest=sha256:<64hex>
# Actions itself down: §6.1b (check out the tag's fly.prod.toml first, then fly deploy by digest)

# Status
fly status --app hc-rag-server-prod
fly status --app hc-rag-weaviate-prod
fly volumes list --app hc-rag-weaviate-prod
fly logs --app hc-rag-server-prod
```
