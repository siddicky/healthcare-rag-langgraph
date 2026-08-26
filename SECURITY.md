# Security policy

This repository is a healthcare-context RAG assistant and coach platform. It
handles member messages that may contain personal health information, so we
treat privacy defects as security defects.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository
(Security tab, "Report a vulnerability"). If that is unavailable, email the
maintainer listed in `.github/CODEOWNERS` with the subject line
`[healthcare-rag security]`.

Do not open a public issue for anything that could expose member data or
bypass the safety gate.

Include what you can of: the affected path or endpoint, a minimal
reproduction, the version or git SHA (see `git tag` or `docs/deploy.md`), and
the impact you believe it has. You will get an acknowledgement within two
working days and a fix or a written deferral within fourteen.

Please do not test against the production deployment (`nymble.site` and the
`hc-rag-*` Fly apps) with real member accounts or real personal data. Every
runtime component runs locally with `make weaviate ingest run`, and
`SERVER_STORAGE=memory` gives you a throwaway server.

## Supported versions

Only the latest release tag on `main` receives fixes. Tags follow
`docs/decisions/release-tags-and-rollback.md`; older tags exist for rollback,
not for support.

## What counts as a security issue here

In rough order of severity:

1. Anything that lets one member read, write, or erase another member's
   threads, store data, reminders, uploads, or crons (the member perimeter in
   `server/` and `healthcare_rag/agent/`).
2. Personal identifiers reaching a model prompt, a checkpoint, the history
   file, a log line, or a response unscrubbed. The sanitizer in
   `healthcare_rag/processors/privacy.py` and the gate in
   `healthcare_rag/processors/safety.py` are the controls; `docs/safety.md`
   states the posture and its known limits.
3. A path that produces personal medical advice, a dosing number, or an
   emergency non-redirect despite the safety gate (prompt injection that
   changes the refusal templates, a boundary replay bypass, a coach answer
   that did not come from `medical_lookup`).
4. Authentication or JWT handling in the server perimeter, the cron wake
   token, and the upload reservation flow.
5. Anything that lets upload bytes persist beyond the request-lifetime buffer.
6. Supply-chain or deploy-pipeline issues: the digest-pinned Fly deploy,
   GHCR publishing, secret sync, the required-reviewer gate.

Out of scope: findings against the two drug monographs' content, denial of
service against the local dev stack, and vulnerabilities in the rented
vendors themselves (OpenAI, Supabase, LangSmith, Fly, Vercel). Report those to
the vendor.

## How we handle dependency alerts

Dependabot runs against `uv.lock` (the only manifest the build reads) and
`tests/server/oracle/requirements.txt` (the pinned 0.12.6 oracle environment
used by the parity suite, never deployed). Every open alert gets one of three
outcomes, recorded in `docs/decisions/`:

- upgraded, with the lockfile diff and the test run in the PR;
- deferred with a written reachability argument naming the code path that
  would have to exist for the advisory to apply and confirming it does not;
- dismissed as a ghost when the manifest it references no longer exists.

The current state is in `docs/decisions/dependabot-requirements-txt.md`.
The two open deferrals as of 2026-08-26 are GHSA-gr75-jv2w-4656 (`langchain`
file-search middleware and config loaders; neither is imported) and
GHSA-r7w7-9xr2-qq2r (`langchain-openai` image token counting; never called).
Both fixes require moving to `openai` 2.x and `langgraph-sdk` 0.4 and will be
taken as one gated upgrade.

## Controls you can rely on

- `make test` runs 1,956 backend tests without API keys; the safety gate,
  refusal boundary, sanitizer, and perimeter each have their own suites.
- `make eval` and `make eval-multiturn` measure the safety categories in
  `evals/golden_dataset.json`; any change to prompts, models, retrieval, or
  orchestration must show before/after numbers.
- `make parity` holds `server/` to the LangGraph platform contract via the
  pinned oracle, and CI proves by SBOM that the vendor's `langgraph-api`
  package is absent from the production image.
- `scripts/deployed_smoke.py` runs after every deploy and rollback; it
  includes ten forbidden perimeter calls, cross-member isolation, erasure to
  exact zero, and an upload source scan proving no bytes persist.
- Secrets live only in `.env` locally and in the `production` GitHub
  environment; the deploy verifies secret names, never values.

## Credit

We will credit reporters in the fix PR unless asked not to.
