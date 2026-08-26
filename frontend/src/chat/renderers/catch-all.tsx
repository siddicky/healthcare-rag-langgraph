"use client";

import { useEffect } from "react";
import { useRenderTool } from "@copilotkit/react-core/v2/headless";
import { chatTelemetry } from "@/chat/stream";

/**
 * Fail-closed catch-all tool renderer (CopilotKit v2 wildcard, name "*").
 *
 * PHI posture: renders the tool NAME + a status pill + an error surface ONLY.
 * Never raw args, never raw result — not even on error. Unknown tools (not in
 * the coach agent's canonical catalog) additionally fire one structured
 * telemetry event; known-but-unregistered tools render the same card silently
 * because a sibling renderer may claim them by exact name later.
 */

export interface CatchAllToolProps {
  readonly name: string;
  readonly status: "inProgress" | "executing" | "complete";
  readonly result?: string;
  readonly parameters?: unknown;
  readonly error?: string | null;
}

/** The coach agent's canonical tool catalog (coach_agent.py build_route_b_agent). */
const KNOWN_TOOLS = new Set([
  "medical_lookup",
  "remember_fact",
  "log_metric",
  "log_injection",
  "view_schedule",
  "change_schedule",
  "create_reminder",
  "edit_reminder",
  "cancel_reminder",
  "compose_ui",
  "copy_to_clipboard",
]);

function normalize(status: CatchAllToolProps["status"]): "pending" | "success" {
  return status === "complete" ? "success" : "pending";
}

export function CatchAllToolCard({ name, status, error }: CatchAllToolProps) {
  const normalized = normalize(status);
  const isKnown = KNOWN_TOOLS.has(name);

  useEffect(() => {
    if (!isKnown) {
      chatTelemetry({ kind: "unknown_tool", name });
    }
  }, [isKnown, name]);

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
              <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
              <path d="M14 2v6h6" />
              <path d="M10 13h6M10 17h6M10 9h2" />
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
              {name.replace(/_/g, " ")}
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
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              fontSize: 12,
              fontWeight: normalized === "success" ? 700 : 600,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              color: normalized === "success" ? "var(--success)" : "var(--camel)",
            }}
          >
            {normalized === "pending" ? (
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
            ) : (
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
            )}
            {normalized === "pending" ? "Running" : "Done"}
          </span>
        </div>

        {typeof error === "string" && error.length > 0 ? (
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
        ) : null}
      </div>
    </div>
  );
}

/**
 * Registers the wildcard catch-all via CopilotKit v2's `useRenderTool`.
 * Null-component: mount once inside the CopilotKit provider tree. The
 * `result`/`parameters` props are deliberately NOT forwarded — fail-closed.
 */
export function registerCatchAllRenderer(): null {
  useRenderTool({
    name: "*",
    render: (p) => <CatchAllToolCard name={p.name} status={p.status} />,
  });
  return null;
}
