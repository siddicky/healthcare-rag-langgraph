<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# src/design

## Purpose
The Nymble Health Design System, copied VERBATIM from the `Nymble Health Design System/` source of truth — tokens (colors, typography, spacing, radii, shadows, motion), a CSS reset, page layout primitives, and every component class (`.btn-*`, `.card-*`, `.tag`, `.label`, `.form-*`, chat-specific classes, etc.) in one `components.css`. `styles.css` is the single entry point, imported once from `src/app/globals.css`. **Never edit files here by hand** — changes must come from re-copying the upstream design system.

## Key Files
| File | Description |
|------|-------------|
| `styles.css` | The aggregation entry point — `@import`s tokens, base, and components in a fixed order (colors -> typography -> spacing -> radii -> shadows -> motion -> reset -> layout -> components) |

## Subdirectories
| Directory | Purpose |
|-----------|---------|
| `tokens/` | CSS custom properties: `colors.css`, `typography.css`, `spacing.css`, `radii.css`, `shadows.css`, `motion.css` — the `var(--*)` values every component in `src/components/` and `src/chat/components/` references |
| `base/` | `reset.css` (CSS reset) and `layout.css` (page-level layout primitives) |
| `components/` | `components.css` — every component-level class name used across the app (`.btn`, `.card`, `.tag`, `.label`, `.form-input`, `.thread-scroll`, `.bubble`, etc.) |

## For AI Agents

### Working In This Directory
- **Do not hand-edit any file under `src/design/`.** This tree is a verbatim copy from an external design-system source (per `README.md`'s layout table: "copied VERBATIM ... never edit here"). If a token or class needs to change, the change belongs upstream and gets re-copied in, or — if that's not available — treat it as an exceptional, explicitly-flagged deviation rather than a routine edit.
- All app styling flows through class names and `var(--token)` references defined here; no other CSS files exist in the app except `src/app/chat/chat.css` (page-specific, layered on top) and `src/app/globals.css` (the one `@import` of `styles.css`).

### Testing Requirements
- No unit tests apply to CSS. Visual correctness is checked via Playwright screenshots in `e2e/smoke.spec.ts` (`e2e/__screenshots__/`).

### Common Patterns
- N/A — this directory holds token/CSS assets only, not application logic.

## Dependencies

### Internal
- Imported once, at the top of the cascade, by `src/app/globals.css`

### External
- None (plain CSS, no preprocessor)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
