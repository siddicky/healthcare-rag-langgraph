# Nymble coach frontend

Next.js 16 App Router app for the Nymble AI Coach. Members sign in with
Supabase, then chat through the CopilotKit v2 headless transport. The app uses
TypeScript strict mode, React 19, and Turbopack. Bun is the package runner.

## Run it

From the repository root:

```sh
bun install --cwd frontend
bun run --cwd frontend dev          # development server on http://localhost:3000
bun run --cwd frontend build        # production build and type check
bun run --cwd frontend start        # serve a completed production build
bun run --cwd frontend test         # Vitest unit and component tests
bun run --cwd frontend playwright   # hermetic Playwright end-to-end suite
```

The Playwright suite starts its own scripted dependencies, a real local agent
server, and a production Next.js server. It uses ephemeral ports and makes no
external network calls. The suite is serial and may take several minutes to
boot on its first run.

## Environment

Copy the repository `.env.example` to `.env` for the complete server setup.
For frontend-only development, put these values in `frontend/.env.local`:

```dotenv
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key
NEXT_PUBLIC_LANGGRAPH_URL=http://localhost:2024
LANGGRAPH_DEPLOYMENT_URL=http://localhost:2024
```

`NEXT_PUBLIC_LANGGRAPH_URL` configures the browser SDK and defaults to
`http://localhost:2024`. `LANGGRAPH_DEPLOYMENT_URL` configures the server-side
`/api/copilotkit` proxy. The proxy accepts `NEXT_PUBLIC_LANGGRAPH_URL` as a
fallback, but production should set both to the same Agent Server origin.

Two optional build-time flags control guarded UI and protocol behavior:

```dotenv
NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER=v2
NEXT_PUBLIC_COACH_HISTORY_BRANCH_UI=1
```

The first selects the v2 member stream envelope. It must match the server's
`HC_RAG_MEMBER_STREAM_PERIMETER` value. The second enables history branching
and turn editing; those controls stay hidden unless its value is exactly `1`.

Never put service-role keys, internal tokens, OpenAI keys, or LangSmith keys in
`NEXT_PUBLIC_*` variables. Members authenticate with Supabase email and
password. The app refreshes their bearer token and sends it to direct Agent
Server requests and the same-origin CopilotKit proxy. The server enforces
identity and thread ownership.

## Layout

| Area | Path |
|---|---|
| App Router pages and CopilotKit runtime route | `src/app/` |
| CopilotKit engine, chat state, protocol, and tool renderers | `src/chat/` |
| Catalog schemas, data-ref hydration, registry, and dispatch | `src/catalog/` |
| Auth, core, data-display, and generative UI components | `src/components/` |
| Supabase, Agent Server clients, auth headers, and environment access | `src/lib/` |
| Copied Nymble design system, not edited by hand | `src/design/` |
| Hermetic Playwright stack and specs | `e2e/` |

`/` redirects to `/chat`. `/chat` checks the Supabase session and redirects
unauthenticated members to `/login`. It mounts `CopilotKitProvider` at
`/api/copilotkit`; `useCoachStream.ts` projects AG-UI messages into the chat
model, `src/chat/renderers/` registers tool cards, and interrupts render through
CopilotKit's interrupt hook.

## Catalog contract

The `compose_ui` wire format is `{component, props, children?}`. Fact-bearing
props must use a data reference shaped as
`{__ref: {turn_scope_id, block_id, pointer}}`. The pointer follows RFC 6901 and
targets the same turn's DATA envelope. Literal facts, unresolved references,
and cross-turn references fail closed.

The composable registry contains `InjectionTracker`, `MiniCalendar`,
`TrendCard`, `ActionCard`, `StatRow`, `ScoreRing`, `Timeline`, `Card`, `Tag`,
`Label`, and `Button`. `CalendarChangeCard`, `MemoryExtractionCard`,
`DocumentIngestCard`, and `ReminderCard` consume fixed interrupt, status, or
envelope contracts and are not composable. Button actions pass through the
fixed map in `src/catalog/dispatch.tsx`; unknown action ids fail closed.

## Deployment

Run `bun run --cwd frontend build` with the production environment present,
then deploy the generated Next.js app with its server runtime. This repository
does not currently automate frontend deployment. Configure the Agent Server's
`COACH_ALLOWED_ORIGINS` and `CORS_ALLOW_ORIGINS` with the deployed frontend
origin. See `docs/deploy.md` for the matching backend configuration and release
checks.
