"use client";

import { useRenderTool } from "@copilotkit/react-core/v2/headless";
import { z } from "zod";
import { parseDataEnvelope } from "@/catalog/envelopes";
import { ConcreteSchemas } from "@/catalog/schemas";
import { INJECTION_STATUSES, sparseToWeekStrip } from "@/catalog/weekstrip";
import { InjectionTracker } from "@/components/generative-ui/InjectionTracker";
import { MiniCalendar } from "@/components/generative-ui/MiniCalendar";
import { TrendCard } from "@/components/generative-ui/TrendCard";
import { ReminderEnvelopeCards } from "@/chat/components/InterruptPanel";
import { chatTelemetry } from "@/chat/stream";
import { RendererLoadingCard } from "./compose";

/**
 * CopilotKit renderers for the envelope tools: each tool's result IS the DATA
 * envelope JSON string (`{turn_scope_id, block_id, data, text}`), parsed with
 * the shared `parseDataEnvelope` and validated against the EXISTING catalog
 * concrete schemas before any card renders. Anything unparseable, mismatched
 * against the tool's block id, or failing validation renders nothing plus a
 * `chatTelemetry` event — never raw envelope JSON.
 *
 *   log_metric      -> trend:<metric>        -> TrendCard
 *   log_injection   -> weekstrip:injection   -> InjectionTracker (sparse week-strip adapter)
 *   view_schedule   -> calendar:<month>      -> MiniCalendar
 *   reminder tools  -> reminders:list        -> compact ReminderCard list
 */

type ToolStatus = "inProgress" | "executing" | "complete";

/** Any tool-call args object; envelope tools' facts ride the RESULT, not args. */
const LooseArgsSchema = z.record(z.string(), z.unknown());

interface EnvelopeToolViewProps {
  readonly status: ToolStatus;
  readonly result: string | undefined;
}

function failClosed(toolName: string, detail: string): null {
  chatTelemetry({ kind: "unknown_tool", name: toolName, detail });
  return null;
}

/**
 * Shared envelope gate: loading shimmer until complete, then parse + block-id
 * check. `renderData` owns per-tool data validation and the card itself.
 */
function EnvelopeToolView({
  toolName,
  blockPrefix,
  status,
  result,
  renderData,
}: EnvelopeToolViewProps & {
  toolName: string;
  blockPrefix: string;
  renderData: (data: unknown) => React.ReactNode;
}) {
  if (status !== "complete") return <RendererLoadingCard name={toolName} />;
  const envelope = parseDataEnvelope(result);
  if (envelope === null) return failClosed(toolName, "result is not a DATA envelope");
  if (!envelope.block_id.startsWith(blockPrefix)) {
    return failClosed(toolName, `unexpected block_id ${envelope.block_id}`);
  }
  return <>{renderData(envelope.data)}</>;
}

// ---------------------------------------------------------------------------
// log_metric -> TrendCard (concrete schema reused from @/catalog/schemas)
// ---------------------------------------------------------------------------

export function LogMetricToolView({ status, result }: EnvelopeToolViewProps) {
  return (
    <EnvelopeToolView
      toolName="log_metric"
      blockPrefix="trend:"
      status={status}
      result={result}
      renderData={(data) => {
        const parsed = ConcreteSchemas.TrendCard.safeParse(data);
        if (!parsed.success) return failClosed("log_metric", "trend data failed validation");
        return (
          <div className="widget-wrap" data-testid="log-metric-card">
            <TrendCard {...parsed.data} />
          </div>
        );
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// log_injection -> InjectionTracker via the sparse week-strip adapter
// ---------------------------------------------------------------------------

const SparseInjectionDataSchema = z.object({
  medicationName: z.string(),
  doseLabel: z.string(),
  days: z.array(z.object({ date: z.string(), status: z.enum(INJECTION_STATUSES) })),
  nextDoseLabel: z.string().optional(),
});

export function LogInjectionToolView({ status, result }: EnvelopeToolViewProps) {
  return (
    <EnvelopeToolView
      toolName="log_injection"
      blockPrefix="weekstrip:injection"
      status={status}
      result={result}
      renderData={(data) => {
        const parsed = SparseInjectionDataSchema.safeParse(data);
        if (!parsed.success) return failClosed("log_injection", "injection data failed validation");
        return (
          <div className="widget-wrap" data-testid="log-injection-card">
            <InjectionTracker
              medicationName={parsed.data.medicationName}
              doseLabel={parsed.data.doseLabel}
              days={sparseToWeekStrip(parsed.data.days)}
              nextDoseLabel={parsed.data.nextDoseLabel}
            />
          </div>
        );
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// view_schedule -> MiniCalendar (concrete schema reused from @/catalog/schemas)
// ---------------------------------------------------------------------------

export function ViewScheduleToolView({ status, result }: EnvelopeToolViewProps) {
  return (
    <EnvelopeToolView
      toolName="view_schedule"
      blockPrefix="calendar:"
      status={status}
      result={result}
      renderData={(data) => {
        const parsed = ConcreteSchemas.MiniCalendar.safeParse(data);
        if (!parsed.success) return failClosed("view_schedule", "calendar data failed validation");
        return (
          <div className="widget-wrap" data-testid="view-schedule-card">
            <MiniCalendar
              monthLabel={parsed.data.monthLabel}
              firstWeekday={parsed.data.firstWeekday}
              daysInMonth={parsed.data.daysInMonth}
              highlights={parsed.data.highlights}
            />
          </div>
        );
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// Reminder tools -> compact ReminderCard list (existing ReminderEnvelopeCards)
// ---------------------------------------------------------------------------

const REMINDER_LIST_TOOLS = ["create_reminder", "edit_reminder", "cancel_reminder"] as const;
type ReminderListToolName = (typeof REMINDER_LIST_TOOLS)[number];

export function ReminderListToolView({
  toolName,
  status,
  result,
}: EnvelopeToolViewProps & { toolName: string }) {
  return (
    <EnvelopeToolView
      toolName={toolName}
      blockPrefix="reminders:list"
      status={status}
      result={result}
      renderData={(data) => (
        <div className="widget-wrap">
          <ReminderEnvelopeCards data={data} />
        </div>
      )}
    />
  );
}

// ---------------------------------------------------------------------------
// Registration hook
// ---------------------------------------------------------------------------

/** Registers every envelope-tool renderer. Registrations stay stable (deps []). */
export function useEnvelopeToolRenderers(): void {
  useRenderTool(
    {
      name: "log_metric",
      parameters: LooseArgsSchema,
      render: ({ status, result }) => <LogMetricToolView status={status} result={result} />,
    },
    [],
  );
  useRenderTool(
    {
      name: "log_injection",
      parameters: LooseArgsSchema,
      render: ({ status, result }) => <LogInjectionToolView status={status} result={result} />,
    },
    [],
  );
  useRenderTool(
    {
      name: "view_schedule",
      parameters: LooseArgsSchema,
      render: ({ status, result }) => <ViewScheduleToolView status={status} result={result} />,
    },
    [],
  );
  useRenderTool(
    {
      name: "create_reminder",
      parameters: LooseArgsSchema,
      render: ({ status, result }) => <ReminderListToolView toolName="create_reminder" status={status} result={result} />,
    },
    [],
  );
  useRenderTool(
    {
      name: "edit_reminder",
      parameters: LooseArgsSchema,
      render: ({ status, result }) => <ReminderListToolView toolName="edit_reminder" status={status} result={result} />,
    },
    [],
  );
  useRenderTool(
    {
      name: "cancel_reminder",
      parameters: LooseArgsSchema,
      render: ({ status, result }) => <ReminderListToolView toolName="cancel_reminder" status={status} result={result} />,
    },
    [],
  );
}
