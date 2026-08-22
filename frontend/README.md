# Nymble coach — frontend

Next.js (App Router, TypeScript strict, Turbopack) member app for the Nymble
AI Coach. Runner: **bun** (`bun --cwd frontend run …` from the repo root).

```
bun --cwd frontend run dev     # develop on :3000
bun --cwd frontend run build   # production build (type-checked)
bun --cwd frontend run test    # vitest
```

## Layout

| area | path |
|---|---|
| design system (copied VERBATIM from `Nymble Health Design System/` — never edit here) | `src/design/` (`tokens/`, `base/`, `components/components.css`, `styles.css`) |
| ported Nymble components (core, data-display, 8 generative-ui cards) | `src/components/` |
| declarative catalog: wire schemas (`__ref` fact props), hydration, dispatch map, json-render catalog + registry | `src/catalog/` |
| supabase client, LangGraph SDK client factory (refresh-aware bearer) | `src/lib/` |
| routes: `/login` (Supabase email+password), `/chat` (shell — todo 13 fills it), `/` redirects to `/chat` | `src/app/` |

## Environment

Client-side only, all `NEXT_PUBLIC_` (names already in the repo `.env.example`):

```
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
NEXT_PUBLIC_LANGGRAPH_URL=http://localhost:2024
```

No server secrets, no LangSmith keys — the member Supabase bearer is the only
credential, injected per request by the SDK client factory.

## Catalog contract (short version)

- The compose_ui tree wire format is `{component, props, children?}` where
  fact props are data-ref objects `{__ref: {turn_scope_id, block_id, pointer}}`
  (RFC 6901 pointer into the envelope's `data`). Literals in fact props are
  zod-rejected; unresolved or cross-turn refs render nothing + telemetry.
- The registered component list is EXACTLY `catalog.js`: InjectionTracker,
  MiniCalendar, TrendCard, ActionCard, StatRow, ScoreRing, Timeline, Card,
  Tag, Label, Button. The four fixed-contract cards (CalendarChangeCard,
  MemoryExtractionCard, DocumentIngestCard, ReminderCard) render from
  interrupts/status/envelopes directly and are never composable.
- Button actions dispatch through the FIXED map in `src/catalog/dispatch.tsx`;
  unknown ids fail closed.
