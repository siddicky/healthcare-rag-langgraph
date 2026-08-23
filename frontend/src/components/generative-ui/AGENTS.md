<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# src/components/generative-ui

## Purpose
The eight chat-surfaced "generative UI" cards, split into two groups with different rendering paths:

- **Catalog-registered** (composed by the model via `compose_ui` trees, facts arrive as hydrated `__ref`s): `ActionCard`, `InjectionTracker`, `MiniCalendar`, `TrendCard`.
- **Fixed-contract** (never composable — rendered directly by `@/chat/components/*` from interrupts, tool-message DATA envelopes, or upload/reminder state): `CalendarChangeCard`, `MemoryExtractionCard`, `DocumentIngestCard`, `ReminderCard`.

## Key Files
| File | Description |
|------|-------------|
| `ActionCard.tsx` | Catalog. Generic confirm/decide card, up to two buttons (`primaryAction`/`secondaryAction`) |
| `InjectionTracker.tsx` | Catalog. 7-day week strip (`InjectionDay[]`, exactly 7, Monday-first) with per-day status styling (`logged`/`due`/`today`/`upcoming`/`muted`) and an optional `nextDoseLabel` callout |
| `MiniCalendar.tsx` | Catalog. Month grid with `highlights` (`injection`/`checkin`/`today` colored dots) and an optional `onSelectDate` callback |
| `TrendCard.tsx` | Catalog. Big number + colored delta + inline SVG sparkline (`points`, oldest first) |
| `CalendarChangeCard.tsx` | Fixed-contract. Schedule-change confirmation: `pending` shows Confirm/Decline (the HITL interrupt), `confirmed`/`declined` shows the resolved outcome instead — same component renders both the live interrupt (via `InterruptPanel`) and the historical outcome (via `MessageList`'s `CalendarChangeEnvelope`) |
| `MemoryExtractionCard.tsx` | Fixed-contract. Extracted-fields review: unresolved mode (`fields`) lets the member inline-edit values before Save/Discard; resolved mode (`resolvedFields`) is read-only, showing per-field saved/discarded status with no buttons |
| `DocumentIngestCard.tsx` | Fixed-contract. Upload progress: `uploading` (progress bar) -> `scanning` -> `extracting` (spinner) -> `done` (checkmark) |
| `ReminderCard.tsx` | Fixed-contract. Full mode (icon, editable weekday/time via `onScheduleChange`, toggle, cancel) and `compact` mode (dense row for a reminders list, toggle only, no edit/cancel) |

## For AI Agents

### Working In This Directory
- The catalog-registered four MUST keep their prop shapes in sync with `ConcreteSchemas` in `@/catalog/schemas.ts` and their registry adapter in `@/catalog/registry.tsx` — a prop rename here without updating both breaks composed-tree rendering silently (wire validation passes, hydration passes, then the React component just gets `undefined`).
- The fixed-contract four must NEVER be added to `@/catalog/catalog.ts` — they render from interrupts/envelopes/upload-status directly (see `AGENTS.md` in `../../chat/components/`) and are explicitly excluded from the composable set per `healthcare_rag/agent/perimeter.py`'s contract.
- `CalendarChangeCard` is reused for two different rendering paths (live pending interrupt vs. resolved-outcome envelope) — when touching its `status` prop behavior, check both `InterruptPanel.tsx` and `MessageList.tsx`'s `CalendarChangeEnvelope`.
- `ReminderCard`'s `compact` prop is a genuinely different layout (not just smaller spacing) — it drops the icon, edit affordance, and cancel button entirely; don't try to derive `compact` styling by CSS alone.
- `MemoryExtractionCard` holds its own local edit state (`values`, `editingKey`) even though the parent (`useCoachChat`) owns the eventual accept/discard decision — `onSave` receives the member-edited fields, not the original `fields` prop.

### Testing Requirements
- Catalog-registered cards: `src/catalog/__tests__/catalog.test.tsx`. Fixed-contract cards: `src/chat/__tests__/genUi.test.tsx` and `chat.test.tsx`. Full visual/interaction coverage for both groups lives in `e2e/smoke.spec.ts` (screenshots under `e2e/__screenshots__/`, e.g. `trend-card.png`, `calendar-change-confirmed.png`).

### Common Patterns
- All eight cards render `<div className="card...">` wrappers styled by `src/design/components/components.css`, with per-element layout done via inline `style={{...}}` referencing design tokens (`var(--carrot)`, `var(--rust)`, etc.) rather than bespoke CSS classes — match this inline-style-on-token pattern for new cards.
- Callback props are always optional (`onConfirm?`, `onToggle?`, etc.) and guarded with `if (onX) onX()` before calling, so a card never crashes when rendered read-only (e.g. `CalendarChangeCard` in its resolved-outcome path passes no handlers at all).

## Dependencies

### Internal
- `@/catalog/weekstrip` (`StripStatus`, used by `InjectionTracker`)
- Catalog-registered subset consumed by `@/catalog/registry.tsx`; fixed-contract subset consumed by `@/chat/components/{InterruptPanel,MessageList}.tsx`
- `src/design/` (all styling, via `.card`, `.tag`, `.btn-*`, `.form-*`, `.label` classes)

### External
- `react` (`useState` in `ActionCard`'s siblings that need edit state — `MemoryExtractionCard`, `ReminderCard`)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
