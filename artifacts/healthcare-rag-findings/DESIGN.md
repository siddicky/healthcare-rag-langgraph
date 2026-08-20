# Healthcare RAG findings: design notes for the visual story

## 1. What the page is for

An evidence-first case study for Nymble's review panel, set on Nymble's cream paper with restrained data graphics. One visual idea runs through it: a continuous orange-red rule that becomes the chart axis, the timeline, the pipeline route and the final decision band. Call it the safety line.

Readers come in two kinds. Senior reviewers judging engineering judgment, healthcare safety, measurement discipline and how much the AI tooling sped the work up. And whoever inherits the repo next and needs the experiment history to read without narration.

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
| Nymble amber | `--amber` | `#f2aa45` | Secondary highlights, caveats |
| Safety good | `--safe` | `#1f7a5b` | Improvement and pass states |
| Safety good/deep | `--safe-deep` | `#166047` | Positive text on pale green |
| Safety good/pale | `--safe-pale` | `#dceee6` | Positive data fills |
| Risk | `--risk` | `#a82e28` | Regressions, rejected arms |
| Focus | `--focus` | `#2459a8` | Keyboard focus outline |

Red is reserved for required healthcare safety, the through-line and primary links. Green appears only where a metric moved in the safer direction or a stage was kept. Rejected retrieval arms use risk red in the table row header, never green. There are no blue or purple gradients; atmosphere comes from paper tones and two soft radial lights. Body contrast targets WCAG 2.2 AA.

## 3. Typography

Font stack: `Avenir Next`, `Segoe UI`, `Helvetica Neue`, Arial, sans-serif. Mono: `SFMono-Regular`, Consolas, monospace.

| Level | Size | Weight | Line height | Usage |
|---|---:|---:|---:|---|
| Display | `clamp(3.25rem, 8vw, 7.25rem)` | 750 | 0.92 | Hero statement |
| H1 | `clamp(2.5rem, 5vw, 4.75rem)` | 750 | 0.98 | Chapter conclusions |
| H2 | `clamp(1.75rem, 3vw, 2.75rem)` | 720 | 1.08 | Subsections |
| H3 | `1.25rem` | 700 | 1.25 | Evidence labels |
| Lead | `clamp(1.125rem, 1.6vw, 1.5rem)` | 430 | 1.55 | Chapter setup |
| Body | `1rem` | 430 | 1.65 | Explanatory text |
| Small | `0.875rem` | 550 | 1.45 | Notes and sources |
| Overline | `0.75rem` | 750 | 1.2 | Uppercase story markers |
| Metric | `clamp(2.25rem, 5vw, 4.75rem)` | 780 | 0.95 | Headline numbers |

Display statements stay under three lines at every breakpoint. Paragraph measure is capped at 66 characters. No body text below 14px. Emphasis uses weight and red ink, never gradient text. Headings are sentence case and state a conclusion, not a topic.

## 4. Spacing and layout

Base unit 4px.

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

Content max 1200px, reading max 760px. Desktop uses a 12-column editorial grid with 7/5 or 8/4 splits. Tablet drops to two columns only when each keeps at least 320px. Mobile is one column with a 20px gutter and no horizontal page scroll; wide tables scroll inside their own container with a visible hint. Breakpoints sit at 720px and 1040px because that is where the content reflows.

## 5. Components

**Story masthead.** Brand line, overline, display conclusion, short lead, jump link. One `h1`. No entrance animation; the opening relies on composition.

**Story section.** Numbered chapter, conclusion heading, lead, evidence region. Variants: paper, deep band, red decision band. The chapter number is the only decorative type.

**Metric pair.** Label, before value, directional arrow, after value, one-line interpretation. Variants: improvement (green after-value), trade-off (red after-value), unresolved. Values are repeated in prose or a table so nothing depends on the graphic.

**Evidence bar.** Metric label, proportional track, printed value, optional baseline marker. The text value comes first in reading order; the bar is `aria-hidden`.

**Experiment timeline.** Ordered list on the red axis with a tag, title, change and outcome per milestone. Ordered-list semantics carry the chronology without the axis.

**Pipeline route.** Ordered nodes with a left accent; branch callouts for short-circuit and gap-fill. Arrows are hidden from screen readers.

**Gate row.** Four steps of the retrieval gate (stage 1, stop rule, stage 2, frozen thresholds). Stop steps carry a risk-red top rule, pass steps a green one.

**Comparison table.** Caption states the sample and judge. Winner rows use the pale green fill; rejected arms use risk-red row headers. Never more than one bold cell per column.

**Decision note.** Conclusion, evidence, caveat, next action. Surface fill with a strong left rule. Amber variant for caveats, green for kept decisions. No floating-card shadow.

**Source note.** Bold lead-in naming the area, then the artifact paths in monospace. Full filenames wrap; provenance is never truncated.

## 6. Motion and interaction

No decorative animation. Links change color and underline thickness in 120ms ease-out. The print button is a real button with hover, active and focus-visible states. `prefers-reduced-motion` disables smooth scrolling. Print CSS removes the controls and avoids page breaks inside a chapter.

## 7. Depth

Separation comes from tonal shifts and rules. Evidence blocks use one thin rule on a paper surface rather than stacked cards. The only shadow is on the sticky print control: `0 8px 24px rgba(91, 27, 12, 0.12)`. No glass effects.

## 8. Accessibility and accepted debt

Targets: WCAG 2.2 AA, body contrast 4.5:1 minimum, semantic landmarks, ordered lists for sequences, visible focus rings, usable at 200% zoom and 375px width. Charts never rely on color alone; every value is printed. No auto-playing motion, blinking, or hover-only information. Abbreviations are expanded on first use.

Accepted debt: Lighthouse has not been run on a deployed URL, because the deliverable is a standalone HTML file, not a hosted site. Run it if the page is ever published.

## 9. Writing rules for the copy

Headings state a conclusion. Numbers come with their sample size and direction ("lower is better"). Every metric on the page maps to a committed report named in the footer. Caveats sit next to the number they qualify, in amber, not in a footnote. Straight quotes only; no em dashes; sentence case; no emoji.
