<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# src/components

## Purpose
Ported Nymble UI components, grouped by role: `auth/` (the login form), `core/` (generic primitives — Button, Card, Label, Tag, Divider, AccentLine), `data-display/` (StatRow, ScoreRing, Timeline), and `generative-ui/` (the eight cards: four registered in the catalog — `ActionCard`, `InjectionTracker`, `MiniCalendar`, `TrendCard` — and four fixed-contract cards driven directly from interrupts/envelopes/upload status — `CalendarChangeCard`, `MemoryExtractionCard`, `DocumentIngestCard`, `ReminderCard`). No files live directly in `src/components/` — it is purely an organizing root, same as `src/`.

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `auth/` | `LoginForm` — Supabase email+password sign-in (see `auth/AGENTS.md`) |
| `core/` | Generic style primitives: `Button`, `Card`, `Label`, `Tag`, `Divider`, `AccentLine` (see `core/AGENTS.md`) |
| `data-display/` | `StatRow`, `ScoreRing`, `Timeline` — small stat/metric visualizations (see `data-display/AGENTS.md`) |
| `generative-ui/` | The eight chat cards — four catalog-registered, four fixed-contract (see `generative-ui/AGENTS.md`) |

## For AI Agents

### Working In This Directory
- Every component in this tree is styled entirely through class names and CSS custom properties from `src/design/` (e.g. `var(--carrot)`, `var(--rust)`, `.card`, `.btn-primary`) — never introduce a hardcoded color or a new global class name here; extend the design system instead if a new token is needed.
- Components take plain props and call plain callback props (`onClick`, `onSave`, `onConfirm`, etc.) — none of them import `useCoachChat`, `fetch`, or Supabase directly. Wiring to chat state/dispatch happens one layer up, in `@/chat/components/*`.

### Testing Requirements
- `auth/__tests__/login.test.tsx` covers `LoginForm`. The catalog-registered `generative-ui` components are exercised via `src/catalog/__tests__/catalog.test.tsx`; the fixed-contract cards are exercised via `src/chat/__tests__/genUi.test.tsx` and `e2e/smoke.spec.ts`. `core/` and `data-display/` have no dedicated unit tests — they're simple enough to be covered incidentally through the components that use them.

### Common Patterns
- Optional props default inline in the function signature (e.g. `variant`, `size = "default"`, `active = true`) rather than via a separate defaults object — match this style for new components.

## Dependencies

### Internal
- `src/design/` (all styling)
- Consumed by `@/catalog/registry.tsx` (catalog-registered subset) and `@/chat/components/*` (fixed-contract subset + `LoginForm` via `src/app/login`)

### External
- `react` (all client-interactive components are `"use client"`)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
