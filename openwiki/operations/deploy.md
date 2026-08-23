---
type: runbook
title: Production deploy (Fly, tag pipeline)
description: Prod-only Fly deploy of the server image - compliance gate, GitHub Environment production as the single secrets source, immutable-digest tag pipeline in .github/workflows/deploy.yml, Weaviate companion app, prod ingest, and the synthetic-account deployed smoke. Summarizes docs/deploy.md.
tags: [deploy, fly, operations, ci]
openwiki:
  roles: [operations, delivery]
  change_kinds: [release, infrastructure]
  source_paths: [docs/deploy.md, deploy/fly.prod.toml, deploy/fly.weaviate-prod.toml, .github/workflows/deploy.yml, Makefile, scripts/deployed_smoke.py, scripts/forget_member.py, server/Dockerfile]
  symbols: [make release, make ingest-fly, make deployed-smoke, make forget-member, deploy-prod]
  invariants: [Only immutable image digests are deployed - never a mutable tag., Secrets have exactly one source of truth: the GitHub Environment production; the workflow syncs them to Fly and verifies names only., The production environment requires a reviewer and a strict ^v\d+\.\d+\.\d+$ tag policy; the workflow fails closed if protection rules are missing., The deployed smoke always uses synthetic accounts (LANGGRAPH_U1_TOKEN/LANGGRAPH_U2_TOKEN) with LANGSMITH_TRACING=false.]
  validation_commands: [make release TAG=v0.0.1-rc, make deployed-smoke]
---

# Production deploy (Fly, tag pipeline)

Production-only (no staging): the [server](../server/agent-server.md) runs as the
Fly app `hc-rag-server-prod` (`deploy/fly.prod.toml`) next to a private Weaviate
companion `hc-rag-weaviate-prod` (`deploy/fly.weaviate-prod.toml`, region `iad`,
the only persistent volume). `docs/deploy.md` is the canonical step-by-step
runbook — this page is the map; paste commands from there.

## Compliance gate (pre-condition of the first approval)

No prod deploy of real member data runs until the operator records a dated
sign-off in `.omo/evidence/task-12-oss-agent-server-tag-deploys.md` (mirrored to
`.omo/notepads/oss-agent-server-tag-deploys/decisions.md`) stating they reviewed
`docs/safety.md`, that release checks use synthetic test accounts only with
`LANGSMITH_TRACING=false`, and the `production` protection rule is in place.
The workflow's required-reviewer click is still mandatory for every deploy.

## Pipeline shape

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: a semicolon inside a label breaks rendering; rephrase the label. -->
```text
flowchart LR
    T["git push origin vX.Y.Z (human; make release TAG=... validated first)"] --> B["build job: image -> ghcr.io, semver+sha tags, digest output"]
    B --> D["deploy-prod job (environment: production, concurrency lock)"]
    D --> A{"required reviewer approves"}
    A --> F["fly deploy --image <digest> --config deploy/fly.prod.toml"]
    F --> S["sync secrets GitHub Env -> Fly, verify names"]
    S --> W["wait /ok"] --> SM["scripts/deployed_smoke.py (10 checks, synthetic accounts, tracing off)"]
```

- `make release TAG=vX.Y.Z` is hermetic: it validates the tag shape
  (`^v[0-9]+\.[0-9]+\.[0-9]+(-…)?$` locally; the workflow enforces the strict
  no-suffix form) and prints the push commands without pushing.
- Secrets: GitHub Environment `production` is the single source of truth
  (including `FLY_API_TOKEN`). The workflow syncs every secret to Fly and asserts
  the expected names via `fly secrets list` (names only, values never echoed).
  Never put secret values in `deploy/*.toml`, compose files, or docs.
- Weaviate is reached only over the Fly private network
  (`WEAVIATE_HOST=hc-rag-weaviate-prod.internal`); no public Weaviate URL.

## Post-deploy operations

- **Ingest**: `make ingest-fly` currently prints the exact `fly machines run`
  one-off (documented in `docs/deploy.md` §4) that runs
  `healthcare_rag.storage.vector_store --delete-all` against prod Weaviate using
  the last green build's digest. Idempotent; run after bootstrap and after any
  chunk update.
- **Deployed smoke**: `make deployed-smoke` runs `scripts/deployed_smoke.py`
  against `LANGGRAPH_DEPLOYMENT_URL`; it fail-fasts unless all six env vars are
  present (`LANGGRAPH_DEPLOYMENT_URL`, `LANGGRAPH_U1_TOKEN`, `LANGGRAPH_U2_TOKEN`,
  `LANGSMITH_API_KEY`, `COACH_INTERNAL_TOKEN`, `LANGSMITH_FEEDBACK_PROJECT_ID`).
  `--allow-insecure-staging` is for http harnesses only, never prod.
- **Member erasure**: `make forget-member` drives `scripts/forget_member.py`
  through the deployed coach self-erase flow (see [coach agent](../agent/coach.md)).
- **Frontend E2E against prod**: the deployed mode of the
  [member frontend](../frontend/member-frontend.md) Playwright suite consumes the
  same synthetic identities and runfile contract.

Local setup (uv, Docker/Weaviate, ingestion) lives in the
[local runbook](runbook.md); this page covers production only.
