import { defineCatalog } from "@json-render/core";
import { schema } from "@json-render/react/schema";
import { ConcreteSchemas } from "./schemas";

/**
 * The Nymble AI Coach chat catalog — EXACTLY the catalog.js component list:
 * the four generative-ui cards plus the core/data-display primitives.
 * Descriptions are copied verbatim from catalog.js.
 *
 * The four fixed-contract cards (CalendarChangeCard, MemoryExtractionCard,
 * DocumentIngestCard, ReminderCard) are NOT catalog components — they render
 * from interrupts / upload status / reminders envelopes directly and are
 * never composable.
 *
 * Props here are the CONCRETE (post-hydration) shapes; the wire grammar
 * (fact props as __ref objects) lives in schemas.ts.
 */
export const nymbleChatCatalog = defineCatalog(schema, {
  components: {
    InjectionTracker: {
      description:
        "A 7-day week strip showing GLP-1/weekly-injection adherence, with an optional next-dose callout. Use whenever the member logs a dose or asks about their injection schedule.",
      props: ConcreteSchemas.InjectionTracker,
    },
    MiniCalendar: {
      description:
        "A compact month calendar with colored dots for injection days and check-ins. Use when the member asks what's coming up this month, or to confirm a scheduled date.",
      props: ConcreteSchemas.MiniCalendar,
    },
    TrendCard: {
      description:
        "A sparkline card for one tracked metric (weight, waist, steps) with a big number and colored delta. Use when the member logs a metric or asks how they're trending.",
      props: ConcreteSchemas.TrendCard,
    },
    ActionCard: {
      description:
        "Generic confirm/decide card with up to two buttons. The catch-all for anything needing a yes/no or two-option choice inline (reschedule, confirm a swapped meal, accept a plan change).",
      props: ConcreteSchemas.ActionCard,
    },
    StatRow: {
      description:
        "A row of 2-4 plain value+label stat pairs. Use for a quick side-by-side stat callout that doesn't need a full TrendCard sparkline.",
      props: ConcreteSchemas.StatRow,
    },
    ScoreRing: {
      description:
        "A single conic-gradient ring showing a 0-100 score with a label underneath. Use for an overall health-score callout.",
      props: ConcreteSchemas.ScoreRing,
    },
    Timeline: {
      description:
        "A vertical list of dated milestones (week + title + short description). Use to recap recent progress across a few weeks.",
      props: ConcreteSchemas.Timeline,
    },
    Card: {
      description:
        "A generic content container (white surface, rounded, soft shadow). Use as a wrapper when no other catalog component fits the content.",
      props: ConcreteSchemas.Card,
    },
    Tag: {
      description:
        'A small gold-tinted pill label. Use for a single short status word (e.g. "On track", "Missed").',
      props: ConcreteSchemas.Tag,
    },
    Label: {
      description: "Small uppercase eyebrow text, used as a section heading above a card or stat group.",
      props: ConcreteSchemas.Label,
    },
    Button: {
      description:
        "A single tappable action button. Prefer ActionCard for anything with a title/body — use bare Button only for a single inline call-to-action with no surrounding copy.",
      props: ConcreteSchemas.Button,
    },
  },
  actions: {},
});
