<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# deploy/

## Purpose
Fly.io deployment configuration for the two production apps this project
runs: the healthcare RAG server (`server/`, this repo's own image) and its
companion Weaviate vector store. Deploys are tag-triggered and image-only —
neither `fly.toml` has a `[build]` section; images are built and pushed
elsewhere (the release workflow / a pinned Weaviate image tag) and referenced
by digest/tag at deploy time. See `docs/deploy.md` for the full runbook
(bootstrap commands, secrets, one-time setup) — this directory only holds the
static per-app config.

## Key Files
| File | Description |
|------|-------------|
| `fly.prod.toml` | App `hc-rag-server-prod` (region `iad`). Env: `WEAVIATE_HOST=hc-rag-weaviate-prod.internal`, `WEAVIATE_PORT=8080`, `SERVER_STORAGE=memory` (production default as of this PR; code also supports `postgres` with `DATABASE_URI`/`DATABASE_URL` via `server/registries.py` + `server/storage.py` durable `hc_*` tables — activation is human-gated per `docs/deploy.md` §0b, release N+1), `SERVER_PORT=8000`; `SERVER_LOCAL_DEV` deliberately left unset here (the Dockerfile sets `ENV SERVER_LOCAL_DEV=0`) — must never be overridden to enable local-dev mode in prod. `[http_service]` on internal port 8000, `auto_stop_machines=false`, `min_machines_running=1`; health check hits `GET /ok` (public, readiness-gated — only 200 after lifespan completes; `/info` is the other public endpoint). CORS/allowed-origins and all secrets come from `fly secrets set`, sourced from the GitHub `production` environment — never hardcoded here. |
| `fly.weaviate-prod.toml` | App `hc-rag-weaviate-prod` (region `iad`), deployed with a **pinned** image (`cr.weaviate.io/semitechnologies/weaviate:1.30.2` — comment explicitly says "do not float the tag"). Env enables anonymous access (private-network only; not internet-exposed) and API-based modules. `[[mounts]]` attaches the `weaviate_data` volume (1GB initial) at `/var/lib/weaviate` — the vector-data durable volume. With `SERVER_STORAGE=memory` (current production as of this PR) this is the only durable state and server threads/runs/store/crons do not survive a restart in that mode; with `SERVER_STORAGE=postgres` server state persists in `hc_threads`/`hc_runs`/`hc_crons` plus `AsyncPostgresSaver`/`AsyncPostgresStore` (see `server/registries.py`, `server/storage.py`, `docs/deploy.md` §§0b/8–9) — code supports both, production still `memory` until the human-gated N+1 flip. Health check hits `GET /v1/.well-known/ready` on port 8080. |

## For AI Agents

### Working In This Directory
- These two files are the entire source of truth for prod topology; a change here has no build step to catch mistakes before `fly deploy` — get changes reviewed and check them against `docs/deploy.md` before applying.
- Never add secrets (API keys, tokens) to these `.toml` files — they're committed. Everything sensitive goes through `fly secrets set` per `docs/deploy.md`.
- Don't add a `[build]` section without also updating `docs/deploy.md` and `.github/workflows/deploy.yml` — the whole point of the current setup is CI supplies a pinned image digest, not Fly building on deploy.
- The Weaviate image tag is pinned deliberately (data-format/version compatibility risk); bump it only as a deliberate, tested upgrade, not incidentally.

### Testing Requirements
- No automated tests target these files directly. `make release TAG=vX.Y.Z` runs hermetic validation before a tag-triggered deploy; `scripts/verify/f3_realenv.sh` and `scripts/deployed_smoke.py` exercise the deployed result afterward.

### Common Patterns
- Every non-obvious env var or config choice is explained with an inline `#` comment referencing the reason (health-check semantics, why a section is absent, what must not be overridden) — keep that convention when editing.

## Dependencies

### Internal
- `server/Dockerfile` and `server/config.py` (env vars this config sets: `SERVER_STORAGE`, `SERVER_PORT`, `SERVER_LOCAL_DEV`, `WEAVIATE_HOST`, `WEAVIATE_PORT`)
- `.github/workflows/deploy.yml` (supplies the image digest at deploy time)

### External
- Fly.io (`flyctl`), `cr.weaviate.io/semitechnologies/weaviate:1.30.2`

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
