"use client";

import { useEffect, useMemo } from "react";
import { chatTelemetry } from "@/chat/stream";

/**
 * Minimal AssembledToolCall projection — mirrors SDK shape but tolerant of
 * both camelCase and legacy keys so fallback synthesis from WireMessages works.
 * Real stream calls carry: id/callId, name, args/input, output/result,
 * status (running/finished/error), error, namespace.
 */
export interface ToolCallView {
  readonly id: string;
  readonly callId?: string;
  readonly name: string;
  readonly args?: unknown;
  readonly input?: unknown;
  readonly output?: unknown;
  readonly result?: unknown;
  readonly status: string;
  readonly error?: string | null;
  readonly namespace?: readonly string[];
}

export type ToolCallStatus = "pending" | "running" | "success" | "finished" | "error";

const KNOWN_TOOLS = new Set([
  "medical_lookup",
  "copy_to_clipboard",
  "query_lipitor",
  "query_metformin",
  "compose_ui",
  "log_metric",
  "log_injection",
  "view_schedule",
  "change_schedule",
  "remember_fact",
  "create_reminder",
  "edit_reminder",
  "cancel_reminder",
  "set_reminder",
  "claim_document",
  "review_document",
]);

const TOOL_LABELS: Record<string, string> = {
  medical_lookup: "Medical lookup",
  copy_to_clipboard: "Copy to clipboard",
  query_lipitor: "Lipitor reference",
  query_metformin: "Metformin reference",
  compose_ui: "Assembling view",
  log_metric: "Logging metric",
  log_injection: "Logging injection",
  view_schedule: "Checking schedule",
  change_schedule: "Updating schedule",
  remember_fact: "Saving note",
  create_reminder: "Creating reminder",
  edit_reminder: "Updating reminder",
  cancel_reminder: "Canceling reminder",
  set_reminder: "Setting reminder",
  claim_document: "Claiming document",
  review_document: "Reviewing document",
};

function normalizeStatus(raw: string): ToolCallStatus {
  const s = raw.toLowerCase();
  if (s === "running" || s === "pending") return "pending";
  if (s === "finished" || s === "success") return "success";
  if (s === "error") return "error";
  return "pending";
}

function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name.replace(/_/g, " ");
}

function scrubForTelemetry(value: string): string {
  // Redact obvious PII fragments before telemetry — never log raw args.
  return value
    .replace(/[\w.+-]+@[\w-]+\.[\w.-]+/g, "[email]")
    .replace(/\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b/g, "[phone]")
    .replace(/\b\d{2,3}-\d{2,3}-\d{4}\b/g, "[id]")
    .slice(0, 120);
}

function prettyArgs(raw: unknown): string {
  if (raw === undefined || raw === null) return "";
  let serialized: string;
  try {
    serialized = typeof raw === "string" ? raw : JSON.stringify(raw, null, 2);
  } catch {
    serialized = String(raw);
  }
  // Truncate long payloads — keep card compact, never render raw JSON blob unbounded.
  const TRUNCATE = 360;
  if (serialized.length > TRUNCATE) return `${serialized.slice(0, TRUNCATE)}…`;
  return serialized;
}

function previewResult(raw: unknown): string {
  if (raw === null || raw === undefined) return "";
  let text: string;
  if (typeof raw === "string") text = raw;
  else {
    try {
      text = JSON.stringify(raw, null, 2);
    } catch {
      text = String(raw);
    }
  }
  const LIMIT = 280;
  if (text.length > LIMIT) return `${text.slice(0, LIMIT)}…`;
  return text;
}

function IconForTool({ name }: { name: string }) {
  const common = {
    width: 16,
    height: 16,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  switch (name) {
    case "medical_lookup":
      return (
        <svg {...common} aria-hidden>
          <path d="M4 19.5A2.5 2.5 0 016.5 17H20" />
          <path d="M6.5 2H20v15H6.5A2.5 2.5 0 014 19.5V2z" />
          <path d="M8 7h8M8 11h6" />
        </svg>
      );
    case "copy_to_clipboard":
      return (
        <svg {...common} aria-hidden>
          <rect x="9" y="9" width="10" height="10" rx="2" />
          <path d="M5 15a2 2 0 01-2-2V5a2 2 0 012-2h8a2 2 0 012 2v2" />
        </svg>
      );
    case "compose_ui":
      return (
        <svg {...common} aria-hidden>
          <rect x="3" y="3" width="8" height="8" rx="2" />
          <rect x="13" y="3" width="8" height="8" rx="2" />
          <rect x="3" y="13" width="8" height="8" rx="2" />
          <rect x="13" y="13" width="8" height="8" rx="2" />
        </svg>
      );
    default:
      if (name.startsWith("query_") || name === "medical_lookup") {
        return (
          <svg {...common} aria-hidden>
            <circle cx="11" cy="11" r="7" />
            <path d="M20 20l-3.5-3.5" />
          </svg>
        );
      }
      if (name.includes("reminder") || name.includes("schedule")) {
        return (
          <svg {...common} aria-hidden>
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M16 2v4M8 2v4M3 10h18" />
          </svg>
        );
      }
      return (
        <svg {...common} aria-hidden>
          <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
          <path d="M14 2v6h6" />
          <path d="M10 13h6M10 17h6M10 9h2" />
        </svg>
      );
  }
}

function StatusBadge({ status }: { status: ToolCallStatus }) {
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
  if (status === "error") {
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
          color: "var(--error)",
        }}
      >
        <span
          aria-hidden
          style={{
            width: 16,
            height: 16,
            borderRadius: "50%",
            background: "var(--error)",
            color: "var(--white)",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 11,
            lineHeight: 1,
          }}
        >
          !
        </span>
        Error
      </span>
    );
  }
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
        color: "var(--success)",
      }}
    >
      <span
        aria-hidden
        style={{
          width: 16,
          height: 16,
          borderRadius: "50%",
          background: "var(--success)",
          color: "var(--white)",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 10,
          lineHeight: 1,
        }}
      >
        ✓
      </span>
      Done
    </span>
  );
}

export interface ToolCallCardProps {
  call: ToolCallView;
}

export function ToolCallCard({ call }: ToolCallCardProps) {
  const normalized = useMemo(() => normalizeStatus(call.status), [call.status]);
  const isKnown = KNOWN_TOOLS.has(call.name);
  const rawArgs = call.args ?? call.input;
  const rawOutput = call.output ?? call.result;
  const argsText = useMemo(() => prettyArgs(rawArgs), [rawArgs]);
  const resultText = useMemo(() => previewResult(rawOutput), [rawOutput]);

  useEffect(() => {
    if (!isKnown) {
      const safe = scrubForTelemetry(call.name);
      chatTelemetry({ kind: "unknown_tool", name: safe, detail: safe });
    }
  }, [isKnown, call.name]);

  const label = toolLabel(call.name);
  const errorText = typeof call.error === "string" && call.error.length > 0 ? call.error : null;

  return (
    <div
      className="card"
      data-testid="tool-call-card"
      data-tool={call.name}
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
            background: normalized === "error" ? "rgba(198,40,40,0.08)" : normalized === "success" ? "rgba(45,125,50,0.08)" : "var(--gold-20)",
            color: normalized === "error" ? "var(--error)" : normalized === "success" ? "var(--success)" : "var(--carrot-accessible)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <IconForTool name={call.name} />
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--rust)", lineHeight: 1.2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {label}
          </div>
          <div style={{ fontSize: 11, color: "var(--camel)", fontFamily: "var(--font-body)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {call.name}
            {call.namespace && call.namespace.length > 0 ? ` · ${call.namespace.join("/")}` : ""}
          </div>
        </div>
        <StatusBadge status={normalized} />
      </div>

      {normalized === "pending" && (
        <div
          data-testid="tool-call-pending"
          style={{
            marginTop: 10,
            height: 6,
            borderRadius: 999,
            background: "var(--rust-10)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: "42%",
              background: "var(--carrot)",
              borderRadius: 999,
              animation: "toolcall-shimmer 1.1s ease-in-out infinite",
            }}
          />
        </div>
      )}

      {argsText !== "" && (
        <div style={{ marginTop: 10 }}>
          <div className="label" style={{ marginBottom: 4, fontSize: 11 }}>
            Arguments
          </div>
          <pre
            data-testid="tool-call-args"
            style={{
              margin: 0,
              padding: "8px 10px",
              background: "var(--birch)",
              border: "1px solid var(--rust-10)",
              borderRadius: "var(--border-radius-sm)",
              fontSize: 12,
              lineHeight: 1.5,
              color: "var(--rust)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 120,
              overflow: "auto",
            }}
          >
            {argsText}
          </pre>
        </div>
      )}

      {normalized === "success" && resultText !== "" && (
        <div style={{ marginTop: 10 }}>
          <div className="label" style={{ marginBottom: 4, fontSize: 11 }}>
            Result
          </div>
          <div
            data-testid="tool-call-result"
            style={{
              padding: "8px 10px",
              background: "var(--white)",
              border: "1px solid var(--rust-10)",
              borderRadius: "var(--border-radius-sm)",
              fontSize: 13,
              lineHeight: 1.5,
              color: "var(--rust)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 140,
              overflow: "auto",
            }}
          >
            {resultText}
          </div>
        </div>
      )}

      {normalized === "error" && (
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
          {errorText ?? "Tool failed — please try again."}
        </div>
      )}

      {!isKnown && (
        <div style={{ marginTop: 8, fontSize: 11, color: "var(--camel)", lineHeight: 1.4 }}>
          Unknown tool — rendered as a generic card.
        </div>
      )}

      <style>{`@keyframes toolcall-shimmer{0%{transform:translateX(-100%)}100%{transform:translateX(260%)}}`}</style>
    </div>
  );
}
