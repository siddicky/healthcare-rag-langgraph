# Decision: release tag taxonomy and the rollback contract

- **Verdict: ADOPT.** The git tag is the release identity; the immutable semver
  image tag is the ledger that maps it to a digest; a rollback re-deploys a
  whole prior release — image **and** the `deploy/fly.prod.toml` from that tag —
  through a dispatch-triggered, environment-gated job. Deploy input is always a
  digest; no `latest`; no auto-rollback.
- Date: 2026-08-24 · Commit: `1c1fe66` · Trigger: rollback was a laptop `flyctl`
  session against a digest recovered by scrolling old Actions logs, and nothing
  stated which image tags were safe to deploy from.
- Evidence: `.github/workflows/deploy.yml`, `docs/deploy.md` §6, and
  `tests/agent/test_release_pipeline.py` (the invariants below are asserted
  against the workflow file, not just described here).

## The taxonomy

| ref | example | mutability | may a deploy consume it? |
|---|---|---|---|
| git tag | `v1.2.3` | **immutable** — never re-pointed | yes: it selects the release |
| image `{{version}}` | `ghcr.io/<repo>:1.2.3` | **immutable** — one tag, one build, one digest | only to *resolve* a digest |
| image `{{major}}.{{minor}}` | `ghcr.io/<repo>:1.2` | rolling | no — humans only |
| image `sha-<short>` | `ghcr.io/<repo>:sha-e414846` | immutable | traceability only |
| image digest | `ghcr.io/<repo>@sha256:…` | immutable by construction | **yes — the only thing ever deployed** |
| `latest` | — | — | does not exist, deliberately |

Three rules follow from the table:

1. **Machines deploy digests, humans read tags.** `fly deploy` never receives a
   tag. The forward path already worked this way; the rollback path now does
   too, and the resolution step validates `^sha256:[0-9a-f]{64}$` before it is
   allowed near a deploy.
2. **A release is a triple**: `(git tag, image digest, deploy/fly.prod.toml at
   that tag)`. Config travels with the image. This is what closes the
   `SERVER_STORAGE` trap in `docs/deploy.md` §6.3 — rolling the image back to a
   pre-Postgres digest while the live TOML still says `SERVER_STORAGE =
   "postgres"` produces a container that exits during config validation and a
   rollback that looks like an outage. The rollback job checks the repo out *at
   the target tag*, so the pair can never drift.
3. **The immutable semver image tag is the ledger.** `ghcr.io/<repo>:1.2.2` →
   digest is one `imagetools inspect` call, so there is no separate release
   database to keep in sync with reality. Consequence: **never delete a release
   tag from GHCR.** An untagged digest is eligible for registry GC, and GC-ing
   one deletes a rollback target.

## The rollback contract

- **Trigger:** `workflow_dispatch` on the Deploy workflow, inputs `version`
  (required, a released tag), `reason` (required, recorded), `image_digest`
  (optional break-glass override for when a release's GHCR tag is gone).
- **Gate:** the job declares `environment: production`, so it inherits that
  environment's required reviewers and its secrets. It shares the
  `deploy-production` concurrency group with `deploy-prod`, so a rollback and a
  tag deploy serialise instead of racing.
- **Does:** verify the tag on origin and its ancestry from `main`, resolve the
  digest, mirror it into the Fly registry, `fly deploy` it by digest with that
  release's TOML, wait for `/ok`, run the deployed smoke, upload the redacted
  log, and write the whole record (version, source digest, Fly digest, reason,
  actor, smoke result) to the job summary.
- **Deliberately does not** re-sync secrets from the GitHub Environment. A
  rollback is a recovery action, and one of the things it must be able to
  recover from is a bad secret sync; re-applying the current environment during
  a rollback would re-apply the fault.
- **Deliberately is not automatic.** `docs/deploy.md` records "NO auto-rollback"
  as policy: a red smoke leaves the bad version live and a human decides. That
  stands. A flaky smoke that redeploys prod on its own is a worse failure than
  a bad version sitting still for the minutes it takes a human to click. What
  changes here is only the *cost* of that click.

## How a tag gets created

`.github/workflows/release.yml`, dispatch-only:

```bash
make next-version                     # preview — same script CI runs
gh workflow run release.yml -f bump=auto
```

`auto` derives the bump from the Conventional Commit subjects since the last
tag (`feat!`/`BREAKING CHANGE:` → major, `feat` → minor, everything else →
patch), with one carve-out: while the major is still `0`, a breaking change
bumps the minor. Promoting `0.x` to `1.0.0` on the first `feat!` would declare
API stability by accident. `scripts/next_version.py` is the single
implementation, shared by CI and `make`, so the local preview is the number CI
will pick rather than one that looks like it.

Four things must hold before a tag exists:

1. **Cut from `main` only.** A release from a branch would tag a commit that
   never passed review.
2. **The tag must not already exist.** Release tags are immutable; the job
   refuses to re-point one rather than silently invalidating the digest already
   published under that version.
3. **`pyproject.toml` must already carry the version.** The wheel and the image
   report that number, so a tag that disagrees with it turns provenance into a
   guess. `make release-prep BUMP=…` writes the bump and re-locks; it lands as
   an ordinary reviewed PR, which is also where a human gets to disagree with
   the computed version.
4. **The commit must be green.** No red or still-running checks, and the
   offline suite must have succeeded on that exact commit.

**Not on every merge.** Tagging automatically on merge to `main` is continuous
deployment, and this repo is explicitly not that: production has required
reviewers on the `production` environment. Every merge
would queue a prod approval request, and approvals that arrive constantly stop
being read. A human decides when a release happens; the automation decides what
number it gets and does the mechanical part.

**The tag is pushed with `RELEASE_TAG_TOKEN`, not `GITHUB_TOKEN`.** GitHub does
not raise workflow-triggering events for refs pushed with `GITHUB_TOKEN`, so a
tag created with it would sit in the repo and never deploy — "the release
succeeded but nothing shipped", which is worse than a loud failure. The job
refuses to tag when the secret is missing and prints the local commands
instead. The secret is a fine-grained PAT with `contents: write` on this repo
only; it can create a tag, and the `production` environment's reviewers still
gate what that tag deploys.

## Prereleases deploy to production, by design

`v1.2.3-rc1` matches the `v*.*.*` deployment policy and reaches prod like any
other tag. This is recorded as intended, not overlooked: the system has exactly
one deployed environment, so a suffix is a human label about confidence, not a
routing rule — and a tag that silently built an image but deployed nowhere would
be the more surprising outcome. If a staging app is ever added, this is the
decision to revisit first: route non-final tags there, and tighten the
production environment's tag policy from `v*.*.*` to final versions only.

## Rejected

- **Tagging every merge to `main`** — see above: it is continuous deployment,
  which this repo's approval gate is designed not to be.
- **A release PR bot (release-please style)** — the PR it would open is the one
  `make release-prep` produces in a single command, and the bot adds a
  changelog format, a config file and a bot identity to maintain.
- **Auto-rollback on smoke failure** — reverses an explicit recorded policy, and
  couples prod stability to smoke flakiness.
- **A committed release ledger** (`deploy/releases.jsonl` written by CI) — a
  second source of truth that CI must push to `main` to keep accurate, and it
  can disagree with the registry. The registry already knows.
- **Deploying `{{major}}.{{minor}}`** — convenient and mutable, which is the
  combination that turns "roll back to 1.2" into "roll forward to whatever 1.2
  points at now".
