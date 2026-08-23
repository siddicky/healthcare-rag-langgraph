<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# src/components/core

## Purpose
Generic, style-only primitives used throughout the app and registered directly in the catalog (`Card`, `Tag`, `Label`, `Button`): a themed button, a content-container card, a hairline divider, an uppercase eyebrow label, a small pill tag, and a gold accent bar. None of these hold chat/domain state — they are pure presentational wrappers around design-system class names.

## Key Files
| File | Description |
|------|-------------|
| `Button.tsx` | `variant` (`primary`/`secondary`/`gold`), `size` (`default`/`sm`), `full` (stretch), extends native `ButtonHTMLAttributes` so any DOM button prop (`onClick`, `type`, `aria-*`) passes through via `...rest` |
| `Card.tsx` | `variant` (`elevated` raises shadow + hover-lift; `birch` is a flat tinted surface; unset is the base white card), `bordered` (2px carrot border), `large` (48px padding / 20px radius), `hoverLift` (set false to disable the hover transition, used by `LoginForm`) |
| `Divider.tsx` | 1px hairline; `gold` uses the 30%-opacity gold tint |
| `Label.tsx` | Uppercase eyebrow text; `gold` swaps camel color for gold |
| `Tag.tsx` | Small pill label; always gold-tinted today (`gold` prop kept for a future non-gold variant) |
| `AccentLine.tsx` | 60x3px gold bar under section headers; `center` centers it |

## For AI Agents

### Working In This Directory
- These components are registered verbatim in the catalog (`Card`, `Tag`, `Label`, `Button` — see `@/catalog/registry.tsx`), so their prop names/shapes must stay in sync with `ConcreteSchemas` in `@/catalog/schemas.ts`. Changing a prop name or default here without updating the catalog schema will silently break composed-tree rendering.
- Class-name composition follows one pattern everywhere: build an array of conditional class fragments, `.filter(Boolean).join(" ")` — match it for new variants rather than template-literal concatenation.

### Testing Requirements
- No dedicated unit tests; covered incidentally through `src/catalog/__tests__/catalog.test.tsx` (as registered catalog components) and through every component/E2E test that renders a screen using them.

### Common Patterns
- Every variant prop maps to a CSS class suffix (`btn-${variant}`, `card-${variant}`) rather than inline style branching — new variants should follow the same "prop -> class suffix" convention so styling stays entirely in `src/design/components/components.css`.

## Dependencies

### Internal
- `src/design/components/components.css` (all class names referenced here: `.btn`, `.btn-*`, `.card`, `.card-*`, `.label`, `.label-gold`, `.tag`, `.tag-gold`, `.divider`, `.divider-gold`, `.accent-line`, `.accent-line-center`)

### External
- `react` (`ButtonHTMLAttributes`, `ReactNode`, `CSSProperties`)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
