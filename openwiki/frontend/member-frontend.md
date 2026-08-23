---
type: application
title: Member frontend
description: The Next.js App Router member app in frontend/ - Supabase login, coach chat client and protocol, the declarative json-render catalog with data-ref hydration, and the deployed/hermetic Playwright E2E suites.
tags: [frontend, nextjs, coach, catalog, e2e]
openwiki:
  roles: [architecture, integration, testing]
  change_kinds: [public-api, ui]
  source_paths: [frontend/src/chat/coachApi.ts, frontend/src/chat/useCoachChat.ts, frontend/src/chat/model.ts, frontend/src/catalog/catalog.ts, frontend/src/catalog/hydrate.ts, frontend/src/catalog/dispatch.tsx, frontend/src/catalog/schemas.ts, frontend/src/lib/langgraph.ts, frontend/src/lib/supabase.ts, frontend/e2e/smoke.spec.ts, frontend/e2e/server.py, frontend/e2e/run.ts]
  symbols: [useCoachChat, coachApi, catalog.js registry, dispatch map, hydrate, createLangGraphClient]
  test_paths: [frontend/src/chat/__tests__, frontend/src/catalog/__tests__, frontend/src/lib/__tests__, frontend/e2e/smoke.spec.ts]
  invariants: [The registered component list is exactly catalog.js; unknown components or dispatch ids fail closed., Fact props in compose_ui trees must be data-ref objects with RFC 6901 pointers; literals there are zod-rejected, and unresolved or cross-turn refs render nothing plus telemetry., No server secrets in the frontend - the Supabase member bearer is the only credential and is injected per request by the SDK client factory.]
  validation_commands: [bun --cwd frontend run test, bun --cwd frontend run build]
---

# Member frontend

`frontend/` is the member-facing Next.js App Router app (TypeScript strict,
Turbopack) for the Nymble AI Coach. It talks to the deployed coach surface
([coach agent](../agent/coach.md)) — served either by the platform or the
[OSS Agent Server](../server/agent-server.md)) through
`@langchain/langgraph-sdk`. Runner is **bun** (`bun --cwd frontend run …`).
`frontend/README.md` is the authoritative short contract.

- **Auth** (`src/lib/supabase.ts`, `src/app/login`): Supabase email+password;
  `src/lib/langgraph.ts` builds the SDK client with a refresh-aware bearer.
  Env is client-side only (`NEXT_PUBLIC_SUPABASE_URL`,
  `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_LANGGRAPH_URL`) — names already
  in the repo `.env.example`.
- **Chat** (`src/chat/`): `useCoachChat` drives the turn loop over
  `coachApi.ts`/`coachProtocol.ts` (stream parsing in `stream.ts`, envelope model
  in `model.ts`, upload flow in `uploadFlow.ts`, self-erase in `erase.ts`).
- **Design system** (`src/design/`): copied verbatim from
  `Nymble Health Design System/` — never edit here. Ported Nymble components
  including the eight generative-UI cards live in `src/components/`.

## Catalog contract (compose_ui rendering)

Route B's `compose_ui` tool emits declarative component trees the frontend
renders — the shared contract with the [coach agent](../agent/coach.md)'s
catalog-composition middleware:

- Wire format is `{component, props, children?}`; fact props are data-ref objects
  `{__ref: {turn_scope_id, block_id, pointer}}` (RFC 6901 pointer into the
  envelope's `data`). Hydration lives in `src/catalog/hydrate.ts` +
  `src/catalog/dataRef.ts`; schemas in `src/catalog/schemas.ts` (zod).
- The registered component list is **exactly** `catalog.js` (`src/catalog/`):
  InjectionTracker, MiniCalendar, TrendCard, ActionCard, StatRow, ScoreRing,
  Timeline, Card, Tag, Label, Button. The four fixed-contract cards
  (CalendarChangeCard, MemoryExtractionCard, DocumentIngestCard, ReminderCard)
  render directly from interrupts/status/envelopes and are never composable.
- Button actions dispatch through the fixed map in `src/catalog/dispatch.tsx`;
  unknown ids fail closed.

## Tests and E2E

```bash
bun --cwd frontend run test    # vitest (unit: chat, catalog, lib __tests__)
bun --cwd frontend run build   # type-checked production build
bun --cwd frontend run playwright  # e2e/smoke.spec.ts
```

The Playwright suite supports two modes driven by a runfile
(`COACH_E2E_RUNFILE`, default `frontend/e2e/.tmp/run.json`, built by
`e2e/run.ts` + `global-setup.ts`): **hermetic**, against the Python fake backend
`frontend/e2e/server.py`, and **deployed**, against real `dep_url`/`server_url`
with two synthetic member identities (`u1`/`u2`) and internal headers for
cross-checking thread state. The deployed mode is the smoke companion of the
[deploy runbook](../operations/deploy.md).

## Change guidance

- Adding a composable card: register in `catalog.js`, add the schema, then extend
  the catalog unit tests — and remember the server-side coach middleware must
  accept the composition before the frontend will ever see it.
- Changing chat protocol shapes: update `coachProtocol.ts`/`model.ts` and the
  hermetic fake (`e2e/server.py`) in the same change so E2E stays meaningful.
