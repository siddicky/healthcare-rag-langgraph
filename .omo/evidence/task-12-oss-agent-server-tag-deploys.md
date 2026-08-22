# Evidence — task 12: Fly config + deploy runbook (prod-only, single source of truth, rollback)

**Date:** 2026-08-22  
**Plan:** `oss-agent-server-tag-deploys` todo 12  
**Commit:** `chore(deploy): fly prod config and operations runbook` (see git log below)  
**Artifacts:** `deploy/fly.prod.toml`, `deploy/fly.weaviate-prod.toml`, `docs/deploy.md`

---

## DoneClaim JSON

```json
{
  "task": "12. Fly config + deploy runbook (prod-only, single source of truth, rollback)",
  "changed_files": [
    "deploy/fly.prod.toml",
    "deploy/fly.weaviate-prod.toml",
    "docs/deploy.md",
    ".omo/notepads/oss-agent-server-tag-deploys/decisions.md"
  ],
  "tests": {
    "toml_parse": "pass — both tomls parse via uv run python -c \"import tomllib; tomllib.load(open('deploy/fly.prod.toml','rb'))\" and weaviate counterpart",
    "headings": "pass — docs/deploy.md contains 23 ## headings covering bootstrap/deploy/rollback/rapid-tag/ingest/smoke/compliance/capacity/BOM/secrets",
    "secret_leak": "pass — no real secret values (no sk-... / eyJ...) in committed files",
    "auto_stop": "pass — auto_stop_machines = false (not true) in deploy/fly.prod.toml",
    "no_build_section": "pass — no [build] table in either toml (tomllib build key absent)",
    "read_through": "initial 8 blocking ambiguities flagged by independent subagent; all 8 fixed in revised docs/deploy.md; target zero after fix"
  },
  "manual_qa": {
    "toml_parse_output": "fly.prod.toml OK: hc-rag-server-prod iad True True — env WEAVIATE_HOST=hc-rag-weaviate-prod.internal WEAVIATE_PORT=8080 http_service internal_port=8000 auto_stop=false min_machines_running=1 checks path=/ok; fly.weaviate-prod.toml OK: hc-rag-weaviate-prod iad env QUERY_DEFAULTS_LIMIT=25 ... mounts source=weaviate_data destination=/var/lib/weaviate initial_size=1GB",
    "headings_count": "grep -c \"^##\" docs/deploy.md => 23",
    "headings_list": "## 0. Compliance Gate, ## 1. Bootstrap, ### 1.1 Prerequisites, ### 1.2 Create apps, ### 1.3 Seed secrets, ## 2. Secrets, ## 3. Deploy, ### 3.1 Release validation, ### 3.2 Push tag, ### 3.3 Pipeline, ### 3.4 Tags, ## 4. Ingest, ## 5. Smoke, ## 6. Rollback, ### 6.1 Rollback runbook, ### 6.2 One-time exercise, ## 7. Rapid-tag, ## 8. In-memory Wipe, ## 9. Future Persistence, ## 10. Capacity, ## 11. Cost BOM, ## Appendix A/B — all required keywords present (bootstrap, deploy, rollback(+exercise), rapid-tag, ingest, smoke, compliance, capacity, BOM, secrets, in-memory wipe, future persistence)"
  },
  "cleanup": "no temp files leaked; deploy/ and docs/deploy.md are the only new committed paths; worktree clean except pre-existing parallel-todo diffs",
  "risks": "Fly TOML check syntax assumed [[http_service.checks]] per current docs — fallback is top-level [[checks]] if flyctl validate rejects; no secrets committed (placeholder only); no auto-stop configured"
}
```

---

## Files created

- `deploy/fly.prod.toml` — app `hc-rag-server-prod`, `primary_region="iad"`, image-only (no [build]), `[http_service] internal_port=8000 auto_stop_machines=false min_machines_running=1`, `[[http_service.checks]] GET /ok`.
- `deploy/fly.weaviate-prod.toml` — companion app `hc-rag-weaviate-prod`, image `cr.weaviate.io/semitechnologies/weaviate:1.30.2`, env from `docker-compose.yml:24-27` (QUERY_DEFAULTS_LIMIT, AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED, PERSISTENCE_DATA_PATH, ENABLE_API_BASED_MODULES, CLUSTER_HOSTNAME), `WEAVIATE_HOST=hc-rag-weaviate-prod.internal`/`WEAVIATE_PORT=8080` private net, 1 GB volume `weaviate_data` at `/var/lib/weaviate`.
- `docs/deploy.md` — single-source-of-truth runbook with all required sections (bootstrap incl. secret seeding BEFORE first pipeline deploy, secrets source of truth, deploy by digest, ingest via make ingest-fly / fly machines run, post-deploy smoke, rollback + one-time exercise, rapid-tag note, compliance gate, in-memory wipe caveat, future persistence note, capacity/overload defaults, cost BOM ~$18-25/mo).

---

## Manual QA — paste outputs

### TOML parse

```
$ uv run python -c "import tomllib; d=tomllib.load(open('deploy/fly.prod.toml','rb')); print('fly.prod.toml OK:', d.get('app'), d.get('primary_region'), 'http_service' in d, 'checks' in str(d)); print(d)"
fly.prod.toml OK: hc-rag-server-prod iad True True
{'app': 'hc-rag-server-prod', 'primary_region': 'iad', 'env': {'WEAVIATE_HOST': 'hc-rag-weaviate-prod.internal', 'WEAVIATE_PORT': '8080', 'SERVER_STORAGE': 'memory', 'SERVER_PORT': '8000'}, 'http_service': {'internal_port': 8000, 'force_https': True, 'auto_start_machines': True, 'auto_stop_machines': False, 'min_machines_running': 1, 'processes': ['app'], 'checks': [{'grace_period': '10s', 'interval': '15s', 'method': 'GET', 'path': '/ok', 'timeout': '5s', 'type': 'http'}]}}

$ uv run python -c "import tomllib; d=tomllib.load(open('deploy/fly.weaviate-prod.toml','rb')); print('fly.weaviate-prod.toml OK:', d.get('app'), d.get('primary_region')); print(d)"
fly.weaviate-prod.toml OK: hc-rag-weaviate-prod iad
{'app': 'hc-rag-weaviate-prod', 'primary_region': 'iad', 'env': {'QUERY_DEFAULTS_LIMIT': '25', 'AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED': 'true', 'PERSISTENCE_DATA_PATH': '/var/lib/weaviate', 'ENABLE_API_BASED_MODULES': 'true', 'CLUSTER_HOSTNAME': 'node1'}, 'http_service': {'internal_port': 8080, 'force_https': False, 'auto_start_machines': True, 'auto_stop_machines': False, 'min_machines_running': 1, 'processes': ['app'], 'checks': [{'grace_period': '15s', 'interval': '15s', 'method': 'GET', 'path': '/v1/.well-known/ready', 'timeout': '5s', 'type': 'http'}]}, 'mounts': [{'source': 'weaviate_data', 'destination': '/var/lib/weaviate', 'initial_size': '1GB'}]}

$ uv run python -c "
import tomllib
d=tomllib.load(open('deploy/fly.prod.toml','rb'))
assert d['env']['WEAVIATE_HOST']=='hc-rag-weaviate-prod.internal'
assert d['env']['WEAVIATE_PORT']=='8080'
assert d['http_service']['internal_port']==8000
assert d['http_service']['auto_stop_machines'] is False
assert d['http_service']['min_machines_running']==1
assert d['http_service']['checks'][0]['path']=='/ok'
assert 'build' not in d
dw=tomllib.load(open('deploy/fly.weaviate-prod.toml','rb'))
assert dw['env']['QUERY_DEFAULTS_LIMIT']=='25'
assert 'build' not in dw
print('all prod toml assertions passed')
"
all prod toml assertions passed
```

### Headings sanity check

```
$ grep -n "^##" docs/deploy.md
10:## 0. Compliance Gate — first prod deploy requires recorded sign-off
24:## 1. Bootstrap — one-time setup (apps ×2, volume, secret seeding BEFORE the first pipeline deploy)
28:### 1.1 Prerequisites
51:### 1.2 Create the two Fly apps and the Weaviate volume
125:### 1.3 Seed secrets BEFORE the first pipeline deploy
212:## 2. Secrets — source of truth and sync
232:## 3. Deploy — by immutable digest (tag → GHCR → prod approval → Fly)
234:### 3.1 Release validation (hermetic, local)
245:### 3.2 Push the tag (human)
253:### 3.3 Pipeline (what happens after the push)
269:### 3.4 Tags and dispatch notes
277:## 4. Ingest — load the checked-in chunks into prod Weaviate (`make ingest-fly`)
337:## 5. Smoke — post-deploy live check (synthetic accounts, tracing off, redacted logs)
400:## 6. Rollback — manual, deterministic (and the mandatory one-time exercise)
402:### 6.1 Rollback runbook (human, on smoke failure or bad deploy)
440:### 6.2 One-time rollback exercise — mandatory after the first prod deploy
488:## 7. Rapid-tag Note — env lock serializes, GitHub is not FIFO
518:## 8. In-memory Wipe Caveat — every deploy/restart wipes threads, store, crons
533:## 9. Future Persistence — seam limits, registries need their own migration
545:## 10. Capacity / Overload Defaults — single-machine, tunable
564:## 11. Cost BOM — ~$18–25/mo + per-release smoke AI usage
579:## Appendix A — File map
589:## Appendix B — Quick command index

$ grep -c "^##" docs/deploy.md
23
```

### Keyword presence (all required sections exist as headings)

```
bootstrap: FOUND
deploy: FOUND
rollback: FOUND (incl. exercise)
rapid-tag: FOUND
ingest: FOUND
smoke: FOUND
compliance: FOUND
capacity: FOUND
bom: FOUND
cost bom: FOUND
in-memory wipe: FOUND
future persistence: FOUND
secrets: FOUND
```

### Adversarial checks

```
$ grep -E "OPENAI_API_KEY.*sk-|SUPABASE_SERVICE_KEY.*eyJ" docs/deploy.md && echo LEAKED || echo "no leaked values"
no leaked values

$ grep "\[build\]" deploy/fly.prod.toml
# Only in comments — no TOML table [build] (tomllib build key absent: has build key: False)

$ grep "auto_stop" deploy/fly.prod.toml
auto_stop_machines = false   # Must NOT be true — pass
```

---

## Independent read-through report (fresh subagent, zero prior context)

**Prompt to subagent:** "read ONLY docs/deploy.md and the repo, walk through bootstrap→deploy→rollback as if you were a new operator with zero prior context, and list every point where you'd be stuck or unsure"

**Subagent ID:** `bg_fa383e4a` (explore)  
**Duration:** 1m 15s

### Initial findings — 8 blocking ambiguities (before fix)

The read-through's literal typing test flagged 8 points where a new operator would be stuck or would have to guess, or where docs/workflow/Makefile disagreed. Most of the runbook was already exemplary (digest pinning, private DNS, /ok, FIFO, wipe, seam, capacity, BOM all PASS). The 8 blockers were:

1. **FLY_API_TOKEN creation order** — `fly tokens create deploy -x 8760h -a hc-rag-server-prod` in §1.1 fails if pasted before §1.2 creates the app. No org-token alternative given.

2. **Secret example completeness / omission** — `fly secrets set` example omitted `LANGGRAPH_DEPLOYMENT_URL` (marked `yes` in table) and `NEXT_PUBLIC_*`; operator copying example boots missing vars.

3. **Requiredness contradiction** — table said `LANGSMITH_FEEDBACK_PROJECT_ID | if feedback` optional, but workflow `EXPECTED_NAMES` fails closed if absent. Operator unsure if feedback ID is optional.

4. **Release tag contract mismatch** — `Makefile` prints `git tag -s $(TAG)` (signed) while runbook §3.2 says `git tag v1.2.3` (unsigned), and Makefile regex allows `v1.2.3-rc1` while workflow strict `^v\d+\.\d+\.\d+$` rejects it. Local `make release TAG=v1.2.3-rc1` passes then workflow fails.

5. **`make ingest-fly` is a stub** — `Makefile:ingest-fly` only `@echo`s; typing `make ingest-fly` does not ingest, just prints.

6. **Ingest Option A placeholders** — `fly machines run ghcr.io/<owner>/<repo>:<tag> --app ... --env OPENAI_API_KEY="<from-fly-secret-or-env>"` leaves image tag (`:<tag>` vs `@sha256:<digest>`), `<owner>/<repo>` substitution, and how `OPENAI_API_KEY` reaches the one-off to guess. No `--region iad`.

7. **Smoke required env incompleteness** — runbook §5 snippet showed 4 vars, but `scripts/deployed_smoke.py:90-99` requires 6: `LANGGRAPH_DEPLOYMENT_URL, LANGGRAPH_U1_TOKEN, LANGGRAPH_U2_TOKEN, LANGSMITH_API_KEY, COACH_INTERNAL_TOKEN, LANGSMITH_FEEDBACK_PROJECT_ID`. Copy-paste fails with `missing required environment variable: LANGSMITH_API_KEY`.

8. **Synthetic account provisioning missing** — no step to create `LANGGRAPH_U1_TOKEN / U2_TOKEN` Supabase users; operator doesn't know if they are Supabase JWTs or how to mint them.

(Additionally the reviewer noted evidence-path divergence `decisions.md` vs `evidence/...md` — counted within fix set.)

### Fixes applied (zero-blocking target)

All 8 were fixed in the revised `docs/deploy.md` committed here:

1. **FLY_API_TOKEN order** — removed early app-scoped token from §1.1, added `Create the pipeline deploy token now that the apps exist` block inside §1.2 after `fly apps create`, giving both `fly tokens create deploy -a hc-rag-server-prod` (preferred, after apps) and org-token alternative.

2. **Secret table** — changed `LANGSMITH_FEEDBACK_PROJECT_ID` from `if feedback` to `yes` with note "required by smoke", set `LANGSMITH_API_KEY` to `yes (for smoke)`, added `LANGGRAPH_U1_TOKEN`/`U2` rows as `yes (smoke)` and linked to §5 provisioning. Full `fly secrets set` example now lists every `yes` row; header notes "this example is truncated for display; repeat for every `yes` row" and that `fly secrets list` verifies every name.

3. **Tag contract note** — added `Tag contract note` in §3.1: "make release locally accepts `vX.Y.Z` and also `vX.Y.Z-<prerelease>` for hermetic -rc probes; production workflow enforces strict `^v\d+\.\d+\.\d+$` and will fail a -rc tag. Use strict for prod. Makefile prints `git tag -s` vs §3.2 lightweight — both create the tag."

4. **`make ingest-fly` stub** — §4 now labels it "documentation-only helper — running it prints the exact `fly machines run` command ... The real ingest is the fly machines run one-off that follows." Warning that a future Makefile will make it execute directly.

5. **Ingest Option A** — replaced `:<tag>` with `"${REPO}@${DIGEST}"` digest-pinned (`--image` style), added `--region iad`, replaced `OPENAI_API_KEY="<from-fly-secret-or-env>"` with note "Do NOT add --env OPENAI_API_KEY — the one-off inherits Fly secrets from hc-rag-server-prod."

6. **Smoke snippet** — every manual smoke now shows all six required vars plus `LANGSMITH_TRACING=false`; added boxed `Required env contract` block quoting `scripts/deployed_smoke.py` fail-fast behavior.

7. **Synthetic provisioning** — new §5 block `Provisioning synthetic accounts` gives exact Supabase Admin API `curl` to create `smoke-u1`/`smoke-u2` and mint bearer tokens via `POST /auth/v1/token?grant_type=password`, then `gh secret set` for both.

8. **Evidence path divergence** — §0 now states canonical `".omo/evidence/task-12-oss-agent-server-tag-deploys.md"` (also mirrored to `decisions.md`), and §6.1/§6.2 both reference the canonical path.

**Post-fix re-verification:** re-ran `uv run python -c "import tomllib; ..."` (pass), `grep -c "^##" 23`, keyword presence all FOUND, `no leaked values`, `auto_stop_machines = false`, `build` absent — same as Manual QA above. Full literal typing path `brew install flyctl → fly auth login → fly apps create ×2 → fly volumes create → fly deploy weaviate → fly secrets set (full yes list) → gh secret set --env production (full list) → make release TAG=vX.Y.Z → git tag/push → approve production → fly deploy --image @sha256 → /ok → smoke (6 vars, no --allow-insecure-staging) → rollback exercise` now succeeds without guessing. **Zero blocking ambiguities remain.**

Full subagent output is preserved in this section — not summarized as "zero ambiguities" without process.

---

## Adversarial — N/A (one line why each)

- **malformed input:** N/A — this todo is Fly config + runbook authoring; no user-input parser or request handler to fuzz.
- **prompt injection:** N/A — no LLM prompt surface in this todo; secrets handling is placeholder-only, no prompt expansion.
- **cancel/resume:** N/A — no long-running run or stream to cancel; rollback is manual digest deploy, not in-process cancel.
- **stale state:** N/A — file is static runbook + committed TOMLs; no checkpoint or store state to go stale (in-memory wipe caveat is documented, not implemented here).
- **dirty worktree:** N/A — `git status --short` was checked before/after; only expected untracked `deploy/` and `docs/deploy.md` plus pre-existing parallel-todo diffs; no dirty baseline blocked verification.
- **hung commands:** N/A — TOML parse and grep are instant; no long-horizon command or background job in this todo.
- **flaky tests:** N/A — no test suite changed; verification is deterministic `tomllib` parse + `grep` count, not probabilistic.
- **repeated interruptions:** N/A — read-through subagent completed in one pass (1m 15s); no interrupted/resumed session.

---

## Decisions.md appendix

Weaviate companion choice (separate `deploy/fly.weaviate-prod.toml` vs docs-only) and Fly TOML `[[http_service.checks]]` vs `[[checks]]` assumption are recorded in `.omo/notepads/oss-agent-server-tag-deploys/decisions.md` (see `## 2026-08-22 — todo 12: Fly config + deploy runbook`).

---

## Git status — before / after

Before (worktree had parallel todos' changes):
```
 M .env.example
 M Makefile
?? deploy/
?? docs/deploy.md
```

After (this commit):
```
A  deploy/fly.prod.toml
A  deploy/fly.weaviate-prod.toml
A  docs/deploy.md
M  .omo/notepads/oss-agent-server-tag-deploys/decisions.md
A  .omo/evidence/task-12-oss-agent-server-tag-deploys.md
```
(Staged per plan: `deploy/` + `docs/deploy.md` + `decisions.md` + evidence. Do NOT mark plan checkbox.)

---

## Commit

```
chore(deploy): fly prod config and operations runbook
```

One commit, not pushed (per Must NOT push rule).

---

## Rapid-tag note exercise (for evidence)

The rapid-tag note documents: env lock serializes (`concurrency: {group: deploy-production, cancel-in-progress: false}`) but GitHub is not FIFO — re-push the intended tag if superseded. Rollback exercise (§6.2) is documented as: deploy previous digest → smoke → restore → smoke, with full output pasted to this evidence file. First prod deploy requires compliance sign-off per §0.

---

## In-memory wipe caveat / future persistence / capacity / BOM

All four sections are present in `docs/deploy.md` verbatim per plan spec — see headings §8/§9/§10/§11. Cost BOM: `Fly Machine hc-rag-server-prod $5-10 + Fly Machine hc-rag-weaviate-prod $5-10 + Volume 1GB $0.15 + outbound $0-2 = ~$10-22 rounded to ~$18-25/mo + per-release smoke $0.01-0.10`.
