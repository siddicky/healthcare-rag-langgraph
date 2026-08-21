"use client";

import { z } from "zod";
import { CalendarChangeCard } from "@/components/generative-ui/CalendarChangeCard";
import { MemoryExtractionCard } from "@/components/generative-ui/MemoryExtractionCard";
import { ReminderCard } from "@/components/generative-ui/ReminderCard";
import { chatTelemetry } from "@/chat/stream";
import { classifyInterruptPayload, type ExtractedField } from "@/chat/model";
import type { ResumePayload } from "@/chat/coachProtocol";

/**
 * The pending-interrupt card — exactly one (the server guarantees at most
 * one interrupt per run). Approval issues the unified
 * `Command(resume={accept, fields?})` envelope via runs/stream.
 */

export function InterruptPanel({
  value,
  onApprove,
  disabled,
}: {
  value: unknown;
  onApprove: (resume: ResumePayload) => void;
  disabled: boolean;
}) {
  const classified = classifyInterruptPayload(value);
  if (classified.kind === "unknown") {
    chatTelemetry({ kind: "unknown_interrupt" });
    return null;
  }
  const approve = disabled ? () => {} : onApprove;
  if (classified.kind === "calendar-change") {
    return (
      <div className="widget-wrap" data-testid="interrupt-card">
        <CalendarChangeCard
          eventLabel={classified.card.eventLabel}
          fromLabel={classified.card.fromLabel}
          toLabel={classified.card.toLabel}
          reason={classified.card.reason}
          status="pending"
          onConfirm={() => approve({ accept: true })}
          onDecline={() => approve({ accept: false })}
        />
      </div>
    );
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
          approve({
            accept: true,
            fields: edited.map((field) => ({ key: field.key, value: field.value })),
          })
        }
        onDiscard={() => approve({ accept: false })}
      />
    </div>
  );
}

const ReminderEntrySchema = z.object({
  title: z.string(),
  schedule: z.string(),
  weekday: z.string().optional(),
  time: z.string().optional(),
  nextRun: z.string().optional(),
  active: z.boolean().optional(),
});

type ReminderEntry = z.infer<typeof ReminderEntrySchema>;

/**
 * ReminderCard(s) from a `reminders:list` DATA envelope. Tolerates the
 * single-card object, the bare array, and the `{reminders: [...]}` wrapper;
 * anything else renders nothing + telemetry instead of crashing.
 */
export function ReminderEnvelopeCards({ data }: { data: unknown }) {
  let entries: unknown[];
  if (Array.isArray(data)) {
    entries = data;
  } else if (
    typeof data === "object" &&
    data !== null &&
    Array.isArray((data as Record<string, unknown>).reminders)
  ) {
    entries = (data as Record<string, unknown>).reminders as unknown[];
  } else if (typeof data === "object" && data !== null) {
    entries = [data];
  } else {
    chatTelemetry({ kind: "unknown_interrupt", detail: "reminders:list" });
    return null;
  }
  const parsed = entries
    .map((entry) => ReminderEntrySchema.safeParse(entry))
    .filter((result) => result.success)
    .map((result) => result.data);
  if (parsed.length === 0) {
    chatTelemetry({ kind: "unknown_interrupt", detail: "reminders:list" });
    return null;
  }
  return (
    <>
      {parsed.map((reminder: ReminderEntry) => (
        <div className="widget-wrap" key={`${reminder.title}:${reminder.schedule}`}>
          <ReminderCard
            title={reminder.title}
            schedule={reminder.schedule}
            nextRun={reminder.nextRun}
            weekday={reminder.weekday}
            time={reminder.time}
            active={reminder.active}
            compact
          />
        </div>
      ))}
    </>
  );
}

