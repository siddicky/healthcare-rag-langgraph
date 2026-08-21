import { z } from "zod";
import { DataRefSchema } from "./dataRef";
import { STRIP_STATUSES } from "./weekstrip";

/**
 * Two schema layers per catalog component:
 *
 * 1. WIRE schemas — the compose_ui tree as the model emitted it. Every
 *    fact-bearing prop (a value that must come from verified tool data) is a
 *    data-ref OBJECT; a literal there is zod-rejected. Static-copy props
 *    (fixed UI phrases, enums, booleans, dispatch ids) stay literal — the
 *    backend composition validator owns the static-copy allow-list.
 *
 * 2. CONCRETE schemas — the props AFTER hydration (refs resolved to real
 *    values). These type what the registered TSX components receive and are
 *    re-validated at the render boundary as defense-in-depth.
 */

export const CATALOG_COMPONENT_NAMES = [
  "InjectionTracker",
  "MiniCalendar",
  "TrendCard",
  "ActionCard",
  "StatRow",
  "ScoreRing",
  "Timeline",
  "Card",
  "Tag",
  "Label",
  "Button",
] as const;

export type CatalogComponentName = (typeof CATALOG_COMPONENT_NAMES)[number];

const CardActionWireSchema = z.object({
  label: z.string(),
  action: z.string(),
});

export const WireSchemas = {
  InjectionTracker: z.object({
    medicationName: DataRefSchema,
    doseLabel: DataRefSchema,
    days: DataRefSchema,
    nextDoseLabel: DataRefSchema.optional(),
  }),
  MiniCalendar: z.object({
    monthLabel: DataRefSchema,
    firstWeekday: DataRefSchema.optional(),
    daysInMonth: DataRefSchema.optional(),
    highlights: DataRefSchema.optional(),
    onDateSelectAction: z.string().optional(),
  }),
  TrendCard: z.object({
    label: DataRefSchema,
    value: DataRefSchema,
    unit: DataRefSchema.optional(),
    delta: DataRefSchema.optional(),
    deltaGood: DataRefSchema.optional(),
    points: DataRefSchema,
  }),
  ActionCard: z.object({
    title: DataRefSchema,
    body: DataRefSchema.optional(),
    primaryAction: CardActionWireSchema.optional(),
    secondaryAction: CardActionWireSchema.optional(),
  }),
  StatRow: z.object({
    stats: DataRefSchema,
  }),
  ScoreRing: z.object({
    score: DataRefSchema.optional(),
    label: z.string().optional(),
  }),
  Timeline: z.object({
    items: DataRefSchema,
  }),
  Card: z.object({
    variant: z.enum(["elevated", "birch"]).optional(),
    bordered: z.boolean().optional(),
    large: z.boolean().optional(),
    text: z.string(),
  }),
  Tag: z.object({
    text: z.string(),
  }),
  Label: z.object({
    gold: z.boolean().optional(),
    text: z.string(),
  }),
  Button: z.object({
    variant: z.enum(["primary", "secondary", "gold"]).optional(),
    size: z.enum(["default", "sm"]).optional(),
    full: z.boolean().optional(),
    disabled: z.boolean().optional(),
    label: z.string(),
    action: z.string(),
  }),
} as const satisfies Record<CatalogComponentName, z.ZodTypeAny>;

const CardActionConcreteSchema = z.object({
  label: z.string(),
  action: z.string(),
});

export const ConcreteSchemas = {
  InjectionTracker: z.object({
    medicationName: z.string(),
    doseLabel: z.string(),
    days: z.array(z.object({ label: z.string(), status: z.enum(STRIP_STATUSES) })).length(7),
    nextDoseLabel: z.string().optional(),
  }),
  MiniCalendar: z.object({
    monthLabel: z.string(),
    firstWeekday: z.number().int().min(0).max(6).optional(),
    daysInMonth: z.number().int().min(28).max(31).optional(),
    highlights: z
      .array(z.object({ date: z.number().int().positive(), type: z.enum(["injection", "checkin", "today"]) }))
      .optional(),
    onDateSelectAction: z.string().optional(),
  }),
  TrendCard: z.object({
    label: z.string(),
    value: z.string(),
    unit: z.string().optional(),
    delta: z.string().optional(),
    deltaGood: z.boolean().optional(),
    points: z.array(z.number()),
  }),
  ActionCard: z.object({
    title: z.string(),
    body: z.string().optional(),
    primaryAction: CardActionConcreteSchema.optional(),
    secondaryAction: CardActionConcreteSchema.optional(),
  }),
  StatRow: z.object({
    stats: z.array(z.object({ value: z.string(), label: z.string() })).min(2).max(4),
  }),
  ScoreRing: z.object({
    score: z.number().min(0).max(100).optional(),
    label: z.string().optional(),
  }),
  Timeline: z.object({
    items: z.array(z.object({ week: z.string(), title: z.string(), desc: z.string() })),
  }),
  Card: z.object({
    variant: z.enum(["elevated", "birch"]).optional(),
    bordered: z.boolean().optional(),
    large: z.boolean().optional(),
    text: z.string(),
  }),
  Tag: z.object({
    text: z.string(),
  }),
  Label: z.object({
    gold: z.boolean().optional(),
    text: z.string(),
  }),
  Button: z.object({
    variant: z.enum(["primary", "secondary", "gold"]).optional(),
    size: z.enum(["default", "sm"]).optional(),
    full: z.boolean().optional(),
    disabled: z.boolean().optional(),
    label: z.string(),
    action: z.string(),
  }),
} as const satisfies Record<CatalogComponentName, z.ZodTypeAny>;

/** The compose_ui wire format: {component, props, children?} — z.lazy recursion. */
export interface ComposedNode {
  component: CatalogComponentName;
  props?: Record<string, unknown>;
  children?: ComposedNode[];
}

export const ComposedNodeSchema: z.ZodType<ComposedNode> = z.object({
  component: z.enum(CATALOG_COMPONENT_NAMES),
  props: z.record(z.string(), z.unknown()).optional(),
  children: z.array(z.lazy(() => ComposedNodeSchema)).optional(),
});
