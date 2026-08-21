import { z } from "zod";

/**
 * Sparse-to-seven adapter for InjectionTracker.
 *
 * `log_injection` DATA envelopes carry only what was reported plus what the
 * approved schedule derives — a sparse, date-keyed day list. InjectionTracker
 * always renders an exactly-seven, Monday-first week strip; days with no data
 * become the `muted` filler status.
 *
 * Deterministic rules:
 * - The strip covers the Monday..Sunday window of the anchor date. Without an
 *   explicit anchor the LATEST entry date anchors the window.
 * - Entries outside the window are dropped (a same-weekday entry from another
 *   week never leaks into the strip).
 * - Two entries with the same date collide on one slot: the LATER array entry
 *   wins (fold order).
 */

export const INJECTION_STATUSES = ["logged", "due", "today", "upcoming"] as const;
export type InjectionStatus = (typeof INJECTION_STATUSES)[number];

export const STRIP_STATUSES = [...INJECTION_STATUSES, "muted"] as const;
export type StripStatus = (typeof STRIP_STATUSES)[number];

export interface WeekStripDay {
  label: string;
  status: StripStatus;
}

const SparseDaySchema = z.object({
  date: z.string(),
  status: z.enum(INJECTION_STATUSES),
});

export type SparseInjectionDay = z.infer<typeof SparseDaySchema>;

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
const DAY_MS = 86_400_000;

/** "YYYY-MM-DD" -> UTC ms, or null for anything unparseable. */
function isoToUtcMs(iso: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return null;
  const ms = Date.parse(`${iso}T00:00:00Z`);
  return Number.isNaN(ms) ? null : ms;
}

export function sparseToWeekStrip(
  sparse: readonly unknown[],
  anchorDate?: string,
): WeekStripDay[] {
  const entries: { ts: number; status: InjectionStatus }[] = [];
  for (const raw of sparse) {
    const parsed = SparseDaySchema.safeParse(raw);
    if (!parsed.success) continue;
    const ts = isoToUtcMs(parsed.data.date);
    if (ts === null) continue;
    entries.push({ ts, status: parsed.data.status });
  }

  const explicitAnchor = anchorDate !== undefined ? isoToUtcMs(anchorDate) : null;
  const anchorTs =
    explicitAnchor !== null
      ? explicitAnchor
      : entries.length > 0
        ? Math.max(...entries.map((e) => e.ts))
        : Number.NaN;

  // Monday of the anchor's week: ((weekday + 6) % 7) days back from Sunday=0.
  const weekStart = Number.isNaN(anchorTs)
    ? Number.NaN
    : anchorTs - ((new Date(anchorTs).getUTCDay() + 6) % 7) * DAY_MS;

  const strip: WeekStripDay[] = [];
  for (let i = 0; i < 7; i++) {
    if (Number.isNaN(weekStart)) {
      strip.push({ label: WEEKDAY_LABELS[i] ?? "", status: "muted" });
      continue;
    }
    const slotTs = weekStart + i * DAY_MS;
    const matching = entries.filter((e) => e.ts === slotTs);
    const last = matching[matching.length - 1];
    strip.push({
      label: WEEKDAY_LABELS[i] ?? "",
      status: last === undefined ? "muted" : last.status,
    });
  }
  return strip;
}
