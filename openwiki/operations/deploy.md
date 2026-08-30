---
type: deployment runbook
title: Deployment topology and release acceptance
description: Local and Fly topology, configuration ownership, persistence boundaries, tag-triggered immutable release flow, readiness, and deployed smoke acceptance.
tags: [operations, deployment, fly, release]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-30T08:22:08.381Z
sources:
  - id: openwiki-source-6766b7a0c14857435d2077c9
    resource: repo://.github/workflows/deploy.yml
  - id: openwiki-source-4d1d392666be6dfdd7a91a2e
    resource: repo://.github/workflows/release.yml
  - id: openwiki-source-dabcc1fcca4e6a749405a2dd
    resource: repo://deploy/fly.prod.toml
  - id: openwiki-source-b4e178295535008dd57481f3
    resource: repo://deploy/fly.weaviate-prod.toml
  - id: openwiki-source-7db537d1e393db595930e642
    resource: repo://docker-compose.server.yml
  - id: openwiki-source-b79fbbd921df689b4bbdc82f
    resource: repo://docker-compose.yml
  - id: openwiki-source-221b56705ecc18ccecb34d08
    resource: repo://docs/decisions/release-tags-and-rollback.md
  - id: openwiki-source-98c2d416de5872823203ac78
    resource: repo://docs/deploy.md
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-012f2c78e3b1446dfc35803f
    resource: repo://Makefile
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-bf90e16d0f806741d36c310e
    resource: repo://scripts/next_version.py
  - id: openwiki-source-9e104b33bac732a674234ae9
    resource: repo://scripts/release_green_check.py
  - id: openwiki-source-11188a592edb7e1f2a5551d1
    resource: repo://server/Dockerfile
  - id: openwiki-source-74220444f424d3f8bcca9653
    resource: repo://tests/test_deploy_workflow.py
  - id: openwiki-source-3b0e6a6e174da164328abc99
    resource: repo://tests/test_next_version.py
  - id: openwiki-source-154726e80d6877904062b651
    resource: repo://tests/test_release_pipeline.py
generated: { by: "openwiki/0.4.3", at: "2026-08-30T08:22:08.381Z" }
---

# Deployment topology and release acceptance

The base runtime, clean-room [agent server](../server/agent-server.md), and member frontend have distinct configuration owners. `pyproject.toml` and `uv.lock` own Python dependencies; do not restore the removed root `requirements.txt` (decision `docs/decisions/dependabot-requirements-txt.md`). `langgraph.json` owns graph/auth/custom-app selection; graph settings own model/pipeline knobs; compose/Fly files own topology-specific environment overrides. Read [models and runtime](../configuration/models-and-runtime.md) before changing an environment variable.

## Topology and persistence

Local base RAG compose runs Weaviate on 8080/50051 with persistent `weaviate_data`, anonymous access, and `restart: on-failure:0`. The app profile waits for health and forces tracing off. The server compose stack similarly waits for Weaviate, exposes server port 8000, uses read-only filesystem/tmpfs, and remains in-memory.

Fly deploys the server as an **immutable image digest** on internal port `8000` and a private-DNS Weaviate app in `iad`. `LANGGRAPH_API_URL=http://127.0.0.1:8000` is a loopback callback used by reminder/cron behavior, not a public endpoint. The HTTP service forces HTTPS, maintains one minimum running machine with auto-stop disabled, and health-checks `GET /ok`. Weaviate mounts a persistent volume; production runs `SERVER_STORAGE=postgres` since v1.0.7 (flip executed 2026-08-24, `docs/deploy.md` §§0b/8–9), so threads, store items, and cron registrations now survive deploys/restarts — only in-flight runs, the pending-run queue, and open SSE streams remain process-local. `SERVER_STORAGE=memory` (in-process, wipes on restart) remains the local/dev default — see the [clean-room agent server](../server/agent-server.md)'s dual-backend storage section. `WEAVIATE_HOST` is the private Fly hostname. Production must not enable `SERVER_LOCAL_DEV`.

Production also runs the [member stream perimeter](../agent/member-perimeter.md) at `HC_RAG_MEMBER_STREAM_PERIMETER=v2` (flipped 2026-08-25); the frontend build's `NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER` must be built at the matching version in the same release window, or member chat requests are denied.

## Tag release path

`.github/workflows/deploy.yml` is tag-triggered (`v*.*.*`). It verifies tag ancestry/HEAD, requires production policy checks, builds GHCR, mirrors and deploys an immutable Fly Registry digest, stages required secrets before deployment, polls `/ok`, then executes deployed smoke with tracing off. Smoke failure fails the deployment; there is no automatic rollback. `make release TAG=vX.Y.Z` only validates and prints tag commands; it does not push.

## Release identity, version bumps, and rollback

`docs/decisions/release-tags-and-rollback.md` records the taxonomy a release depends on: the git tag is the immutable release identity, the immutable `{{version}}` GHCR image tag is the ledger mapping a tag to a digest, and only a validated `sha256:...` digest is ever deployed — never `latest` or a rolling tag. A release is the triple `(git tag, image digest, that tag's deploy/fly.prod.toml)`; rolling back re-deploys the whole triple so an old image never runs against a newer storage configuration.

- `.github/workflows/release.yml` (dispatch-only, inputs `bump` and `dry_run`) cuts a tag; it never runs on a merge, since that would queue a production deploy request for every merge against this repo's human-gated posture. `scripts/next_version.py` derives the bump from Conventional Commit subjects since the last tag (`feat!`/`BREAKING CHANGE:` → major, `feat` → minor, else patch; a `0.x` breaking change bumps minor, not major) and is shared by CI and `make next-version --explain` so the local preview matches what CI will pick. The workflow requires the tag to be cut from `main` with full tag history fetched, to not already exist (tags are immutable — re-pointing one is refused), and `pyproject.toml`'s version to already match — `make release-prep BUMP=auto|patch|minor|major` writes that bump into `pyproject.toml`/`uv.lock` and re-locks so a human reviews it as an ordinary PR before the tag exists.
- Before tagging, `release.yml` requires the target commit to be green: it fetches the commit's check runs and workflow runs via the GitHub API and asserts the "Offline test suite" check (the one required check that runs on every push to `main`) succeeded, via `scripts/release_green_check.py`; a red or still-pending commit cannot be released. The tag itself is pushed using a `RELEASE_TAG_TOKEN` PAT, not `GITHUB_TOKEN` — refs pushed with `GITHUB_TOKEN` raise no workflow-triggering events, so a tag created with it would sit inert and never reach `deploy.yml`; the job refuses to tag rather than create that silently-dead tag. A successful run also publishes an annotated tag (message = generated release notes) and a GitHub release.
- `make rollback TAG=vX.Y.Z REASON='why'` and `make release-digest TAG=vX.Y.Z` are hermetic previews (validate/print or resolve-a-digest only); they do not dispatch or mutate anything. The actual rollback is a `workflow_dispatch` on `deploy.yml` with a required `version` and `reason`, gated by the `production` GitHub Environment and the same `deploy-production` concurrency group as a tag deploy, so the two paths serialize instead of racing. It deliberately does not re-sync secrets — a rollback must be able to recover from a bad secret sync — and it is never automatic: a red deployed smoke leaves the last-good version live for a human to decide.
- `tests/test_release_pipeline.py` asserts these invariants directly against both workflows: `deploy.yml` (digest-only deploys, no rebuild on rollback, environment/concurrency gating, no secret resync, tag-ancestry checks, redacted-log-only smoke artifacts) and `release.yml` (dispatch-only trigger, cut-from-main + full-history checkout, immutable-tag refusal, pyproject/tag version agreement, the green-commit gate, and the `RELEASE_TAG_TOKEN` requirement). `tests/test_next_version.py` pins the bump derivation against real, temporary git repositories rather than mocking `git log`, and `tests/test_release_green_check.py` covers the check-run/workflow-run parsing that decides green vs. red. Changing `deploy.yml`, `release.yml`, or `scripts/next_version.py` without updating these tests is the main way this contract silently breaks.

```mermaid
flowchart TD
  T["release tag"] --> V["verify tag and production policy"]
  V --> B["build GHCR image"]
  B --> M["mirror immutable digest to Fly"]
  M --> S["stage secrets then deploy"]
  S --> R["poll readiness endpoint"]
  R --> SM["deployed smoke"]
  SM -->|"pass"| A["accepted"]
  SM -->|"fail"| F["failed release requires human action"]
```

Caption: image identity and smoke acceptance are deployment gates, not optional post-deploy diagnostics.

## Configuration and recovery rules

`OPENAI_API_KEY` is required for the base RAG/Weaviate vectorizer. LangSmith, Supabase, internal tokens, and feedback configuration have topology-specific owners; never copy values into docs. The custom coach app can fail startup on malformed feedback-project configuration. The server's storage-index initialization may explicitly fall back to lexical search for recognized index/dependency/auth failures; it does not create durable server storage.

If Weaviate exits cleanly or fails, run `make weaviate` again; restart policy does not retry it. Re-ingestion deletes all collections when using `make ingest`; follow [retrieval ingestion](../retrieval/weaviate-and-ingestion.md). Use `make server-image`, `make container-server-smoke`, `make server-test`, and `make parity` for server changes. Use `make deployed-smoke` only with a real deployment and required configured synthetic accounts/tokens.

`tests/test_deploy_workflow.py` verifies tag/policy checks, immutable image flow, secret staging, and smoke enforcement. Treat historical deployment docs as supporting material when they disagree with current workflow source; source and tests are authoritative.
