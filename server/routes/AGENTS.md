<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# server/routes/

## Purpose
Holds standalone Starlette route modules that don't belong to one of the
resource engines (threads/runs/crons/store/assistants live directly under
`server/`). Currently a single module for the two unauthenticated
platform-probe endpoints.

## Key Files
| File | Description |
|------|-------------|
| `system.py` | `ok_endpoint` (`GET /ok`, 503 until `app.state.readiness.is_ready()`, else `{"ok": true}`) and `info_endpoint` (`GET /info`, returns `{"api_version": config.api_version}`); both public, no auth. Exported as `routes: list[Route]`. |

## For AI Agents

### Working In This Directory
- `/ok` is the Fly.io health-check target (`deploy/fly.prod.toml`'s `[[http_service.checks]]` hits it) — it must stay dependency-free and fast, and must only return `200` once `ReadinessState.is_ready()` in `server/app.py` is genuinely true (all registered checks flipped).
- `/info` is intentionally minimal (just the API version) and public — do not add anything sensitive to it.
- Both routes read from `request.app.state`, not from module-level globals, so they work correctly across the app's lifespan.

### Testing Requirements
- Covered indirectly by `tests/server/test_scaffold.py` and the parity suite (`make parity`); there's no dedicated `test_system.py` — add one there if you add routes here.

### Common Patterns
- Export a module-level `routes: list[Route]`, matching every other route module under `server/`, for `app.py` to mount with `app.routes.extend(...)`.

## Dependencies

### Internal
- `server/app.py`'s `ReadinessState` and `ServerConfig` (read via `request.app.state`)

### External
- `starlette`

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
