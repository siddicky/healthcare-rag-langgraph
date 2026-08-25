"use client";

import type { QueuedEntry } from "@/chat/useCoachStream";

export function QueueBar({
  queue,
  onCancel,
  onClear,
}: {
  queue: readonly QueuedEntry[];
  onCancel?: (id: string) => void;
  onClear?: () => void;
}) {
  if (queue.length === 0) return null;
  return (
    <div
      data-testid="queue-bar"
      role="status"
      aria-live="polite"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-xs, 8px)",
        padding: "var(--space-xs, 8px) var(--space-sm, 12px)",
        margin: "0 var(--space-sm, 12px)",
        background: "var(--gold-20, #fdf6e3)",
        border: "1px solid var(--birch, #d1c5b4)",
        borderRadius: "var(--border-radius-sm, 6px)",
        fontSize: 13,
        color: "var(--ink, #1a1a1a)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--space-xs, 8px)" }}>
        <span style={{ fontWeight: 600 }}>
          Queued {queue.length} {queue.length === 1 ? "message" : "messages"}
        </span>
        {onClear !== undefined && queue.length > 1 && (
          <button
            className="btn btn-secondary"
            data-testid="queue-clear"
            onClick={() => onClear()}
            style={{
              fontSize: 12,
              padding: "2px 8px",
              borderRadius: "var(--border-radius-sm, 6px)",
              border: "1px solid var(--birch, #d1c5b4)",
              background: "var(--white, #fff)",
            }}
          >
            Clear
          </button>
        )}
      </div>
      <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 4 }}>
        {queue.map((entry, idx) => (
          <li
            key={entry.id}
            data-testid="queue-entry"
            data-queue-id={entry.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-xs, 8px)",
              fontSize: 12,
              color: "var(--camel, #8a7a5a)",
            }}
          >
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                width: 18,
                height: 18,
                borderRadius: "50%",
                background: "var(--rust-10, #fef2f2)",
                border: "1px solid var(--birch, #d1c5b4)",
                fontSize: 10,
                fontWeight: 600,
                flexShrink: 0,
              }}
            >
              {idx + 1}
            </span>
            <span
              style={{
                flex: 1,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
              title={entry.input.question}
            >
              {entry.input.question === "Please review this document." && entry.input.attachment_id
                ? `Review document (${entry.input.attachment_id.slice(0, 8)}…)`
                : entry.input.question}
            </span>
            {onCancel !== undefined && (
              <button
                data-testid="queue-cancel"
                aria-label={`Cancel queued message ${idx + 1}`}
                onClick={() => onCancel(entry.id)}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "var(--camel, #8a7a5a)",
                  cursor: "pointer",
                  fontSize: 12,
                  padding: "0 4px",
                }}
              >
                ×
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
