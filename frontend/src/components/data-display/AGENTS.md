<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# src/components/data-display

## Purpose
Three small, stateless metric-display components, all registered in the catalog: `StatRow` (2-4 value+label pairs with dividers), `ScoreRing` (a 0-100 conic-gradient ring with a label), and `Timeline` (a vertical list of week/title/desc milestones). Purely presentational — no interactivity, no callbacks.

## Key Files
| File | Description |
|------|-------------|
| `StatRow.tsx` | `stats: {value, label}[]` — renders `.stat-item`s separated by `.stat-divider`s (no divider after the last item); keyed by `label` |
| `ScoreRing.tsx` | `score` (0-100, default 78) drawn via an inline `conic-gradient` from `var(--carrot)`/`var(--gold)`/`var(--gold-20)`; `label` (default "Health score") |
| `Timeline.tsx` | `items: {week, title, desc}[]` — a dotted vertical list, keyed by `week` |

## For AI Agents

### Working In This Directory
- These three are catalog-registered (`StatRow`, `ScoreRing`, `Timeline`) — their prop shapes must match `ConcreteSchemas.StatRow`/`.ScoreRing`/`.Timeline` in `@/catalog/schemas.ts` exactly (`StatRow.stats` is validated `.min(2).max(4)` at the wire layer, so don't relax that without updating both sides).
- `StatRow` keys its items by `label`, so two stats with the same label in one row will collide in React's reconciliation — this mirrors the catalog contract (stats are meant to be distinct labels) rather than being a bug to silently work around with an index key.
- `ScoreRing`'s gradient math (`score * 3.6`) assumes `score` is already clamped 0-100 by the concrete schema; don't add additional clamping here — the validation boundary is `@/catalog/schemas.ts`.

### Testing Requirements
- No dedicated unit tests; covered via `src/catalog/__tests__/catalog.test.tsx` as registered catalog components, and via `e2e/smoke.spec.ts` where hydrated trend/stat data renders on screen.

### Common Patterns
- All three accept a default empty array/value in their destructured props (`stats = []`, `items = []`) so a hydration edge case never crashes the render — keep that defensive default for any new data-display component.

## Dependencies

### Internal
- `src/design/components/components.css` (`.stats-row`, `.stat-item`, `.stat-divider`, `.score-visual`, `.score-inner`, `.timeline`, `.timeline-item`, etc.)

### External
- `react` (`Fragment`)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
