# Healthcare RAG Findings - Visual Story Design System

## 1. Atmosphere & Identity

A warm, evidence-first technical narrative for Nymble's review panel. The page should feel like an editorial case study printed on Nymble's cream stock, then brought to life through restrained data graphics. The signature is the **safety line**: a continuous orange-red rule that becomes a chart axis, timeline, graph route, and final decision marker.

Primary audience: senior technical reviewers evaluating engineering judgment, healthcare safety, measurement discipline, and AI-coding leverage. Secondary audience: future contributors who need the experiment history to be legible without narration.

## 2. Color

| Role | Token | Value | Usage |
|---|---|---:|---|
| Paper/base | `--paper` | `#fbf4e6` | Page background |
| Paper/deep | `--paper-deep` | `#f2e5cb` | Atmospheric bands |
| Surface | `--surface` | `#fffdf8` | Evidence blocks and tables |
| Ink/primary | `--ink` | `#5b1b0c` | Headlines and body |
| Ink/secondary | `--ink-muted` | `#8a4b35` | Supporting copy |
| Rule | `--rule` | `#e9c9a3` | Dividers and chart grid |
| Nymble red | `--red` | `#ef452c` | Required/safety emphasis |
| Nymble red/deep | `--red-deep` | `#c8331f` | Hover and strong contrast |
| Nymble amber | `--amber` | `#f2aa45` | Secondary highlights |
| Safety good | `--safe` | `#1f7a5b` | Improvement and pass states |
| Safety good/deep | `--safe-deep` | `#166047` | Accessible positive text on pale green |
| Safety good/pale | `--safe-pale` | `#dceee6` | Positive data fills |
| Risk | `--risk` | `#a82e28` | Regressions and unresolved risk |
| Focus | `--focus` | `#2459a8` | Keyboard focus outline |

Rules:

- Red is reserved for required healthcare safety, the experiment through-line, and primary links.
- Green appears only where a metric is directionally safer or a runtime stage is retained.
- No purple/blue SaaS gradients. Atmosphere comes from paper tones and soft radial light.
- Body contrast targets WCAG 2.2 AA or better.

## 3. Typography

Font stack: `Avenir Next`, `Segoe UI`, `Helvetica Neue`, Arial, sans-serif. Mono: `SFMono-Regular`, Consolas, monospace.

| Level | Size | Weight | Line height | Usage |
|---|---:|---:|---:|---|
| Display | `clamp(3.25rem, 8vw, 7.25rem)` | 750 | 0.92 | Hero statement |
| H1 | `clamp(2.5rem, 5vw, 4.75rem)` | 750 | 0.98 | Section conclusions |
| H2 | `clamp(1.75rem, 3vw, 2.75rem)` | 720 | 1.08 | Subsections |
| H3 | `1.25rem` | 700 | 1.25 | Evidence labels |
| Lead | `clamp(1.125rem, 1.6vw, 1.5rem)` | 430 | 1.55 | Section setup |
| Body | `1rem` | 430 | 1.65 | Explanatory text |
| Small | `0.875rem` | 550 | 1.45 | Notes and sources |
| Overline | `0.75rem` | 750 | 1.2 | Uppercase story markers |
| Metric | `clamp(2.25rem, 5vw, 4.75rem)` | 780 | 0.95 | Headline numbers |

Rules:

- Display statements stay under three lines at every breakpoint.
- Paragraph measure is capped at 66 characters.
- No body text below 14px.
- Headline emphasis uses weight and red ink, never gradient text.

## 4. Spacing & Layout

Base unit: 4px.

| Token | Value | Usage |
|---|---:|---|
| `--s-1` | `4px` | Hairline adjustments |
| `--s-2` | `8px` | Inline gaps |
| `--s-3` | `12px` | Compact grouping |
| `--s-4` | `16px` | Standard content gap |
| `--s-5` | `20px` | Compact block padding |
| `--s-6` | `24px` | Evidence padding |
| `--s-8` | `32px` | Group separation |
| `--s-10` | `40px` | Section internals |
| `--s-12` | `48px` | Major transitions |
| `--s-16` | `64px` | Section padding |
| `--s-20` | `80px` | Large-screen section rhythm |
| `--s-24` | `96px` | Hero breathing room |

Grid:

- Content max: `1200px`; reading max: `760px`.
- Desktop: 12-column editorial grid; asymmetric 7/5 or 8/4 splits.
- Tablet: two columns only when each retains at least 320px.
- Mobile: one column, 20px gutter, no horizontal content scroll.
- Breakpoints: 720px and 1040px, driven by content reflow.

## 5. Components

### Story Masthead

- **Structure**: brand line, overline, display conclusion, short lead, jump link.
- **Variants**: opening; closing decision.
- **States**: link default/hover/focus/active.
- **Accessibility**: one `h1`, descriptive anchor text, no decorative text in reading order.
- **Motion**: none; the opening relies on composition, not entrance effects.

### Story Section

- **Structure**: numbered overline, conclusion heading, lead, evidence region.
- **Variants**: paper; deep band; red decision band.
- **Layout**: stack with optional editorial split.

### Metric Pair

- **Structure**: label, before value, directional rule, after value, interpretation.
- **Variants**: improvement; trade-off; unresolved.
- **States**: static; abbreviations use accessible expansion in surrounding copy.
- **Accessibility**: values are repeated in prose or table form for non-visual access.

### Evidence Bar

- **Structure**: metric label, proportional track, visible value, optional baseline marker.
- **Variants**: positive, neutral, risk.
- **Accessibility**: semantic text value precedes decorative bar; bar is `aria-hidden`.

### Experiment Timeline

- **Structure**: ordered list, red axis, milestone marker, title, change, outcome.
- **Variants**: baseline; rejected; turning point; final.
- **Accessibility**: ordered-list semantics preserve chronology without the axis.

### Pipeline Route

- **Structure**: ordered nodes separated by directional arrows; branch callout for short-circuit and gap-fill.
- **Variants**: normal path; short-circuit path.
- **Accessibility**: ordered-list semantics; arrows hidden from screen readers.

### Decision Note

- **Structure**: conclusion, evidence, caveat, next action.
- **Variants**: recommendation; unresolved risk.
- **Depth**: surface fill plus strong left rule; no floating-card shadow.

### Source Note

- **Structure**: source label, local artifact names, methodology caveat.
- **Accessibility**: full filenames wrap; never truncate critical provenance.

## 6. Motion & Interaction

- No decorative animation.
- Links change color and underline thickness in `120ms ease-out`.
- The print/download affordance is a real button with hover, active, and focus-visible states.
- `prefers-reduced-motion` is respected; smooth scrolling is disabled when requested.
- Print CSS removes navigation controls and preserves page breaks around major sections.

## 7. Depth & Surface

Strategy: **mixed, restrained**.

- Default separation uses tonal shifts and rules.
- Evidence blocks use one thin rule and a subtle paper surface, not repeated floating cards.
- Atmosphere uses two soft radial gradients on the page background; no glassmorphism.
- Shadow is limited to the sticky utility control: `0 8px 24px rgba(91, 27, 12, 0.12)`.

## 8. Accessibility Constraints & Accepted Debt

Constraints:

- WCAG 2.2 AA target; body contrast 4.5:1 minimum.
- Semantic landmarks, ordered experiment/pipeline lists, visible focus rings.
- Fully usable at 200% zoom and 375px width.
- Charts never rely on color alone; every value is printed.
- No auto-playing motion, blinking, or hover-only information.
- Technical abbreviations are expanded on first use.

Accepted debt:

| Item | Location | Why accepted | Owner / Exit |
|---|---|---|---|
| Lighthouse is not run on a deployed URL | Local standalone HTML | User requested an HTML artifact, not hosting | Run after deployment if the artifact becomes a public site |
