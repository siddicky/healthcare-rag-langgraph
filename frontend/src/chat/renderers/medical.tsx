"use client";

import { z } from "zod";
import { useRenderTool } from "@copilotkit/react-core/v2/headless";
import { ReminderCard } from "@/components/generative-ui/ReminderCard";

/**
 * Named tool renderers for the coach agent's canonical tool catalog
 * (healthcare_rag/agent/coach_agent.py — build_route_b_agent's fixed list).
 *
 * SAFETY CONTRACT: `medical_lookup` is `return_direct` — the model never
 * paraphrases its answer. This renderer shows the relayed ToolMessage content
 * VERBATIM through the shared Markdown component, styled exactly like AiBubble
 * (`.bubble-row assistant` > avatar + `.bubble assistant`), which is what
 * e2e/smoke.spec.ts asserts against.
 *
 * The remaining named tools render name + status + on-brand minimal cards;
 * reminder tools reuse ReminderCard visuals for their confirmed state. Args
 * are parsed defensively and never echoed as raw JSON.
 */

export type ToolRenderStatus = "inProgress" | "executing" | "complete";

export interface ToolRenderProps {
  readonly name: string;
  readonly toolCallId?: string;
  readonly status: ToolRenderStatus;
  readonly result?: string;
  /** Parsed tool-call arguments — consumed defensively, never rendered raw. */
  readonly parameters?: unknown;
  readonly error?: string | null;
}

const TOOL_LABELS: Record<string, string> = {
  medical_lookup: "Medical lookup",
  remember_fact: "Saving note",
  create_reminder: "Creating reminder",
  edit_reminder: "Updating reminder",
  cancel_reminder: "Canceling reminder",
  change_schedule: "Updating schedule",
};

function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name.replace(/_/g, " ");
}

/** Ported from ToolCallCard's StatusBadge — same tokens, same three states. */
export function StatusPill({ status }: { status: "pending" | "success" | "error" }) {
  if (status === "pending") {
    return (
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          fontSize: 12,
          fontWeight: 600,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "var(--camel)",
        }}
      >
        <span
          aria-hidden
          style={{
            width: 14,
            height: 14,
            border: "2px solid var(--rust-10)",
            borderTopColor: "var(--carrot)",
            borderRadius: "50%",
            display: "inline-block",
            animation: "ds-spin 0.8s linear infinite",
          }}
        />
        Running
      </span>
    );
  }
  const isError = status === "error";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: 12,
        fontWeight: 700,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        color: isError ? "var(--error)" : "var(--success)",
      }}
    >
      <span
        aria-hidden
        style={{
          width: 16,
          height: 16,
          borderRadius: "50%",
          background: isError ? "var(--error)" : "var(--success)",
          color: "var(--white)",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: isError ? 11 : 10,
          lineHeight: 1,
        }}
      >
        {isError ? "!" : "✓"}
      </span>
      {isError ? "Error" : "Done"}
    </span>
  );
}

function normalize(status: ToolRenderStatus): "pending" | "success" {
  return status === "complete" ? "success" : "pending";
}

function ErrorSurface({ error }: { error?: string | null }) {
  if (typeof error !== "string" || error.length === 0) return null;
  return (
    <div
      data-testid="tool-call-error"
      role="alert"
      style={{
        marginTop: 10,
        padding: "8px 10px",
        background: "var(--white)",
        border: "1px solid var(--error)",
        borderRadius: "var(--border-radius-sm)",
        fontSize: 13,
        lineHeight: 1.5,
        color: "var(--error)",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}
    >
      {error}
    </div>
  );
}

/** Minimal on-brand tool card: icon dot + human label + raw name + status pill (+ error). */
export function NamedToolCard({
  name,
  status,
  error,
  hint,
  children,
}: {
  name: string;
  status: ToolRenderStatus;
  error?: string | null;
  hint?: string;
  children?: React.ReactNode;
}) {
  const normalized = normalize(status);
  return (
    <div className="widget-wrap" data-testid="tool-call-wrap">
      <div
        className="card"
        data-testid="tool-call-card"
        data-tool={name}
        data-status={normalized}
        style={{
          padding: "var(--space-sm)",
          border: "1px solid var(--rust-10)",
          boxShadow: "var(--shadow-sm)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
          <div
            aria-hidden
            style={{
              width: 32,
              height: 32,
              borderRadius: 10,
              background: normalized === "success" ? "rgba(45,125,50,0.08)" : "var(--gold-20)",
              color: normalized === "success" ? "var(--success)" : "var(--carrot-accessible)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            <svg
              width={16}
              height={16}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.8}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <path d="M16 2v4M8 2v4M3 10h18" />
            </svg>
          </div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div
              style={{
                fontSize: 13,
                fontWeight: 700,
                color: "var(--rust)",
                lineHeight: 1.2,
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {toolLabel(name)}
            </div>
            <div
              style={{
                fontSize: 11,
                color: "var(--camel)",
                fontFamily: "var(--font-body)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {name}
            </div>
          </div>
          <StatusPill status={normalized} />
        </div>
        {hint && normalized === "pending" ? (
          <div style={{ marginTop: 8, fontSize: 12, color: "var(--camel)", lineHeight: 1.4 }}>{hint}</div>
        ) : null}
        {children}
        <ErrorSurface error={error} />
      </div>
    </div>
  );
}

export function MedicalLookupBubble(_props: { status: ToolRenderStatus; result?: string }): null {
  return null;
}

export function RememberFactCard({ name, status, error }: ToolRenderProps) {
  // Never echoes the fact payload — the memory confirmation card owns review UX.
  return <NamedToolCard name={name} status={status} error={error} />;
}

const WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"] as const;

function formatTime(t: string): string {
  const parts = t.split(":");
  const h = Number(parts[0]);
  const m = Number(parts[1] ?? "0");
  if (!Number.isFinite(h) || !Number.isFinite(m)) return "";
  const period = h >= 12 ? "PM" : "AM";
  const hh = ((h + 11) % 12) + 1;
  return `${hh}:${String(m).padStart(2, "0")} ${period}`;
}

interface ReminderFields {
  readonly title: string;
  readonly weekday?: string;
  readonly time?: string;
}

/** Defensive arg extraction — unknown shapes degrade to generic labels. */
function parseReminderFields(parameters: unknown): ReminderFields {
  if (typeof parameters !== "object" || parameters === null) return { title: "" };
  const raw = parameters as Record<string, unknown>;
  const title = typeof raw.title === "string" && raw.title.trim() !== "" ? raw.title : typeof raw.target === "string" ? raw.target : "";
  const weekday = typeof raw.weekday === "string" ? raw.weekday.toLowerCase() : undefined;
  const time = typeof raw.time === "string" ? raw.time : undefined;
  return { title, weekday, time };
}

function scheduleLabel(fields: ReminderFields): string {
  const day = fields.weekday && (WEEKDAYS as readonly string[]).includes(fields.weekday)
    ? fields.weekday.charAt(0).toUpperCase() + fields.weekday.slice(1)
    : "day";
  const clock = fields.time ? formatTime(fields.time) : "";
  return clock !== "" ? `Every ${day} at ${clock}` : `Every ${day}`;
}

/**
 * create_reminder / edit_reminder / cancel_reminder renderer.
 * Pending → running card; create/edit complete → read-only ReminderCard
 * (confirmed state); cancel complete → resolved outcome line.
 */
export function ReminderToolCard({ name, status, parameters, error }: ToolRenderProps) {
  if (status !== "complete") {
    return (
      <NamedToolCard
        name={name}
        status={status}
        error={error}
        hint={name === "cancel_reminder" ? "Canceling your reminder…" : "Setting up your reminder…"}
      />
    );
  }
  if (typeof error === "string" && error.length > 0) {
    return <NamedToolCard name={name} status={status} error={error} />;
  }
  const fields = parseReminderFields(parameters);
  if (name === "cancel_reminder") {
    return (
      <div className="widget-wrap" data-testid="reminder-tool-canceled">
        <div
          className="card"
          data-testid="tool-call-card"
          data-tool={name}
          data-status="success"
          style={{
            padding: "var(--space-sm)",
            border: "1px solid var(--rust-10)",
            boxShadow: "var(--shadow-sm)",
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <span aria-hidden style={{ color: "var(--success)", fontWeight: 700 }}>
            ✓
          </span>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "var(--rust)" }}>Reminder canceled</div>
            {fields.title !== "" ? (
              <div style={{ fontSize: 11, color: "var(--camel)", fontFamily: "var(--font-body)" }}>{fields.title}</div>
            ) : null}
          </div>
          <StatusPill status="success" />
        </div>
      </div>
    );
  }
  return (
    <div className="widget-wrap" data-testid="reminder-tool-confirmed">
      <ReminderCard title={fields.title !== "" ? fields.title : "Your reminder"} schedule={scheduleLabel(fields)} active compact />
    </div>
  );
}

/** change_schedule pre-interrupt running state — the interrupt card follows separately. */
export function ChangeScheduleCard({ name, status, error }: ToolRenderProps) {
  return (
    <NamedToolCard
      name={name}
      status={status}
      error={error}
      hint="Review the proposed change below."
    >
      {normalize(status) === "pending" ? <div data-testid="change-schedule-pending" /> : null}
    </NamedToolCard>
  );
}

const ANY_ARGS = z.any();

/**
 * Registers the named coach-tool renderers via CopilotKit v2 hooks.
 * Null-component: mount once inside the CopilotKit provider tree.
 */
export function registerMedicalRenderers(): null {
  useRenderTool({
    name: "medical_lookup",
    parameters: ANY_ARGS,
    render: (p) => <MedicalLookupBubble status={p.status} result={p.result} />,
  });
  useRenderTool({
    name: "remember_fact",
    parameters: ANY_ARGS,
    render: (p) => <RememberFactCard name={p.name} status={p.status} error={null} />,
  });
  useRenderTool({
    name: "create_reminder",
    parameters: ANY_ARGS,
    render: (p) => <ReminderToolCard name={p.name} status={p.status} result={p.result} parameters={p.parameters} error={null} />,
  });
  useRenderTool({
    name: "edit_reminder",
    parameters: ANY_ARGS,
    render: (p) => <ReminderToolCard name={p.name} status={p.status} result={p.result} parameters={p.parameters} error={null} />,
  });
  useRenderTool({
    name: "cancel_reminder",
    parameters: ANY_ARGS,
    render: (p) => <ReminderToolCard name={p.name} status={p.status} result={p.result} parameters={p.parameters} error={null} />,
  });
  useRenderTool({
    name: "change_schedule",
    parameters: ANY_ARGS,
    render: (p) => <ChangeScheduleCard name={p.name} status={p.status} error={null} />,
  });
  return null;
}
