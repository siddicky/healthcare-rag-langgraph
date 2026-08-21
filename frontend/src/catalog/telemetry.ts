import type { DataRef } from "./dataRef";

/**
 * Structured telemetry events emitted by the catalog render pipeline.
 * Every fail-closed path (unknown component, wire rejection, unresolved ref,
 * unknown dispatch, hydration type mismatch) reports here and renders nothing.
 */
export type TelemetryEvent =
  | { kind: "unknown_component"; component: string }
  | { kind: "wire_rejection"; component: string; issues: string[] }
  | { kind: "unknown_dispatch"; component: string; action: string }
  | { kind: "unregistered_dispatch"; component: string; action: string }
  | { kind: "unresolved_ref"; component: string; prop: string; ref: DataRef }
  | { kind: "cross_turn_ref"; component: string; prop: string; ref: DataRef }
  | { kind: "hydrate_rejection"; component: string; issues: string[] };

/**
 * Swappable sink so tests (and later the chat app) can observe catalog
 * telemetry without scraping the console.
 */
export const telemetrySink: { emit: (event: TelemetryEvent) => void } = {
  emit: (event) => {
    if (process.env.NODE_ENV !== "test") {
      console.warn("[coach-ui]", event);
    }
  },
};

export function telemetry(event: TelemetryEvent): void {
  telemetrySink.emit(event);
}
