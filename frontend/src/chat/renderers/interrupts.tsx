"use client";

import type { ReactElement } from "react";
import {
  useInterrupt,
  type InterruptEvent,
  type InterruptRenderProps,
} from "@copilotkit/react-core/v2/headless";
import { CalendarChangeCard } from "@/components/generative-ui/CalendarChangeCard";
import { MemoryExtractionCard } from "@/components/generative-ui/MemoryExtractionCard";
import { chatTelemetry } from "@/chat/stream";
import { classifyInterruptPayload, type ExtractedField } from "@/chat/model";
import type { ResumePayload } from "@/chat/coachProtocol";

/**
 * useInterrupt handlers for the two member-facing HITL interrupts (plan todo 9).
 *
 * Each handler is keyed on the interrupt PAYLOAD SHAPE via `enabled` — the
 * same fixed contracts `classifyInterruptPayload` enforces and
 * `healthcare_rag/agent/perimeter.py:_validate_resume` admits on resume:
 * exactly `{accept: boolean, fields?: [{key: string, value: string}]}`.
 * Unknown payloads render nothing + telemetry (fail-closed), never a crash.
 */

/**
 * The perimeter's resume contract, ported from useCoachStream.ts
 * (`isValidResumePayload`). Every resolve() call-site below is guarded by it,
 * so a malformed payload can never reach the wire.
 */
export function isValidResumePayload(value: unknown): value is ResumePayload {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (typeof v.accept !== "boolean") return false;
  if (v.fields !== undefined) {
    if (!Array.isArray(v.fields)) return false;
    for (const f of v.fields as unknown[]) {
      if (typeof f !== "object" || f === null) return false;
      const ff = f as Record<string, unknown>;
      if (typeof ff.key !== "string" || typeof ff.value !== "string") return false;
    }
  }
  return true;
}

/**
 * Extract the LangGraph interrupt VALUE from the legacy-compatible event the
 * hook hands to `enabled`/`render`. Two shapes arrive here:
 * - legacy `on_interrupt` custom event: `event.value` IS the raw payload;
 * - AG-UI standard interrupt (RUN_FINISHED outcome=interrupt): `event.value`
 *   is the `Interrupt` envelope `{id, reason, ...}` whose LangGraph value
 *   rides `metadata.value` (the adapter mapping useCoachStream also reads).
 */
export function interruptValueFromEvent(event: InterruptEvent): unknown {
  let value = event.value;
  // The locked adapter serializes the LangGraph interrupt value into the
  // legacy `on_interrupt` custom event as a JSON string.
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return value;
    }
  }
  if (typeof value !== "object" || value === null) return value;
  const record = value as Record<string, unknown>;
  if (typeof record.id === "string" && typeof record.reason === "string") {
    const metadata = record.metadata;
    if (typeof metadata === "object" && metadata !== null && "value" in metadata) {
      return (metadata as Record<string, unknown>).value;
    }
    if ("value" in record) return record.value;
    return value;
  }
  return value;
}

function resolveGuarded(
  resolve: InterruptRenderProps["resolve"],
  payload: ResumePayload,
): void {
  if (!isValidResumePayload(payload)) {
    chatTelemetry({ kind: "unknown_interrupt", detail: "malformed resume" });
    return;
  }
  void resolve(payload);
}

function renderCalendarInterrupt({
  event,
  resolve,
}: Pick<InterruptRenderProps, "event" | "resolve">): ReactElement {
  const classified = classifyInterruptPayload(interruptValueFromEvent(event));
  if (classified.kind !== "calendar-change") {
    // Unreachable while `enabled` gates on the same classifier — fail closed.
    chatTelemetry({ kind: "unknown_interrupt", detail: "calendar render mismatch" });
    return <></>;
  }
  const card = classified.card;
  return (
    <div className="widget-wrap" data-testid="interrupt-card">
      <CalendarChangeCard
        eventLabel={card.eventLabel}
        fromLabel={card.fromLabel}
        toLabel={card.toLabel}
        reason={card.reason}
        status="pending"
        onConfirm={() => resolveGuarded(resolve, { accept: true })}
        onDecline={() => resolveGuarded(resolve, { accept: false })}
      />
    </div>
  );
}

function renderMemoryInterrupt({
  event,
  resolve,
}: Pick<InterruptRenderProps, "event" | "resolve">): ReactElement {
  const classified = classifyInterruptPayload(interruptValueFromEvent(event));
  if (classified.kind !== "memory-extraction") {
    chatTelemetry({ kind: "unknown_interrupt", detail: "memory render mismatch" });
    return <></>;
  }
  const fields: ExtractedField[] = classified.payload.fields.map((field) => ({
    key: field.key,
    label: field.label,
    value: field.value,
    needsReview: field.needsReview,
  }));
  return (
    <div className="widget-wrap" data-testid="interrupt-card">
      <MemoryExtractionCard
        sourceLabel={classified.payload.sourceLabel}
        fields={fields}
        onSave={(edited) =>
          resolveGuarded(resolve, {
            accept: true,
            fields: edited.map((field) => ({ key: field.key, value: field.value })),
          })
        }
        onDiscard={() => resolveGuarded(resolve, { accept: false })}
      />
    </div>
  );
}

/** Calendar-change interrupts only (payload shape = CalendarChangePayload). */
export function useCalendarChangeInterrupt(): ReactElement | null {
  return useInterrupt({
    agentId: "coach",
    enabled: (event) =>
      classifyInterruptPayload(interruptValueFromEvent(event)).kind === "calendar-change",
    render: renderCalendarInterrupt,
    renderInChat: false,
  });
}

/** Memory-extraction interrupts only (payload shape = MemoryExtractionPayload). */
export function useMemoryExtractionInterrupt(): ReactElement | null {
  return useInterrupt({
    agentId: "coach",
    enabled: (event) =>
      classifyInterruptPayload(interruptValueFromEvent(event)).kind === "memory-extraction",
    render: renderMemoryInterrupt,
    renderInChat: false,
  });
}

/**
 * Mount-once component registering BOTH interrupt handlers. At most one
 * returns an element per pending interrupt (the `enabled` predicates are
 * disjoint); that element renders wherever this component mounts. Todo 11
 * wires it into the shell when the headless filter retires InterruptPanel.
 */
export function registerInterruptHandlers(): ReactElement | null {
  const calendar = useCalendarChangeInterrupt();
  const memory = useMemoryExtractionInterrupt();
  return calendar ?? memory;
}
