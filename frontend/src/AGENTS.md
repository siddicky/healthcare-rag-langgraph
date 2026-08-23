<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# src

## Purpose
The app's TypeScript source root. Groups five layers: `app/` (Next.js routes/pages), `catalog/` (the fail-closed compose_ui render pipeline), `chat/` (protocol, API, message model, and the `useCoachChat` controller), `components/` (ported Nymble UI, split into auth/core/data-display/generative-ui), `design/` (the verbatim design system), and `lib/` (Supabase + LangGraph SDK client factories, env config). No files live directly in `src/` — it is purely an organizing root.

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `app/` | Next.js App Router pages: `/`, `/login`, `/chat` (see `app/AGENTS.md`) |
| `catalog/` | compose_ui wire schemas, hydration, dispatch, registry, telemetry (see `catalog/AGENTS.md`) |
| `chat/` | coach protocol, API client, message model, stream reducer, `useCoachChat` (see `chat/AGENTS.md`) |
| `components/` | auth/, core/, data-display/, generative-ui/ React components (see `components/AGENTS.md`) |
| `design/` | design tokens/base/component CSS, copied verbatim (see `design/AGENTS.md`) |
| `lib/` | Supabase client, LangGraph SDK factory, env (see `lib/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- Import alias `@/*` resolves to `src/*` (configured in `vitest.config.ts` and the Next.js `tsconfig`) — use it instead of relative `../../` chains.
- Layer boundaries are intentional: `catalog/` and `chat/` are framework-light (mostly pure functions + hooks) so they're cheap to unit test; `components/` holds the presentational React; `app/` wires routes to `chat/` and `components/auth`.
- Don't add a new top-level directory here without updating this file and `../AGENTS.md`'s subdirectory table.

### Testing Requirements
- `bun --cwd frontend run test` runs every `src/**/*.test.{ts,tsx}` file via Vitest; each subdirectory with logic keeps its tests in a local `__tests__/` folder.

### Common Patterns
- Pure-function-first: business logic (message merging, hydration, upload state machine, erase flow) is written as pure functions/reducers in `chat/` and `catalog/`, with React components and hooks (`useCoachChat`, `ChatShell`) as thin wiring layers around them — this is what makes the scripted-fakes testing style in `chat/AGENTS.md` possible.

## Dependencies

### Internal
See each subdirectory's `AGENTS.md`.

### External
See `../AGENTS.md`.

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
