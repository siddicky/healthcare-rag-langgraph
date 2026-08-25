"use client";

import { useState } from "react";
import type { ThreadHistory } from "@/chat/useCoachStream";

export interface TimeTravelProps {
  history: ThreadHistory[] | null;
  historyLoading?: boolean;
  selectedCheckpointId?: string | null;
  onTimeTravel: (checkpointId: string) => void;
  onFork: (checkpointId: string) => void;
  onFetchHistory?: () => void;
  busy?: boolean;
}

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}

function formatTime(value?: string): string {
  if (!value) return "—";
  try {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value;
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return value;
  }
}

function messagesCount(entry: ThreadHistory): number {
  const values = (entry as unknown as Record<string, unknown>).values as Record<string, unknown> | undefined;
  const msgs = values?.messages;
  if (Array.isArray(msgs)) return msgs.length;
  // fallback: check entry.messages directly (some fixtures store differently)
  const direct = (entry as unknown as Record<string, unknown>).messages;
  if (Array.isArray(direct)) return direct.length;
  return 0;
}

export function TimeTravel({
  history,
  historyLoading = false,
  selectedCheckpointId = null,
  onTimeTravel,
  onFork,
  onFetchHistory,
  busy = false,
}: TimeTravelProps) {
  // v1 guard: hidden when history is null (no route) — parent also gates by env, but belt-suspenders here
  if (history === null && !historyLoading) {
    // Still render collapsed shell with fetch affordance when v2 but empty? Spec says hidden when no history route / empty.
    // Return minimal to satisfy "v1 hidden (no history route)" — no panel when null.
    return null;
  }

  const entries = history ?? [];
  const isEmpty = entries.length === 0;
  const [open, setOpen] = useState(true);

  return (
    <section
      data-testid="time-travel-panel"
      aria-label="History"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-xs, 8px)",
        padding: "var(--space-sm, 16px)",
        margin: "0 var(--space-sm, 12px)",
        background: "var(--warm-white, #fdf8f0)",
        border: "1px solid var(--border-hairline, rgba(99,19,0,0.1))",
        borderRadius: "var(--border-radius-sm, 12px)",
        boxShadow: "var(--shadow-sm, 0 2px 12px rgba(99,19,0,0.06))",
      }}
    >
      <button
        data-testid="time-travel-toggle"
        aria-expanded={open}
        aria-controls="time-travel-list"
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          width: "100%",
          background: "transparent",
          border: "none",
          cursor: "pointer",
          padding: 0,
          fontFamily: "var(--font-headline, Quicksand, sans-serif)",
          fontSize: "var(--text-label, 14px)",
          fontWeight: 600,
          letterSpacing: "var(--tracking-label, 0.1em)",
          textTransform: "uppercase",
          color: "var(--text-ink, #631300)",
        }}
      >
        <span>History {entries.length > 0 ? `· ${entries.length}` : ""}</span>
        <span
          aria-hidden
          style={{
            display: "inline-block",
            transform: open ? "rotate(180deg)" : "rotate(0deg)",
            transition: "transform var(--transition-fast, 150ms) ease",
            fontSize: 12,
            color: "var(--text-muted, #9e4d14)",
          }}
        >
          ▾
        </span>
      </button>

      {open && (
        <>
          {onFetchHistory !== undefined && (
            <button
              data-testid="time-travel-refresh"
              onClick={() => onFetchHistory()}
              disabled={busy || historyLoading}
              style={{
                alignSelf: "flex-start",
                padding: "4px 10px",
                borderRadius: "var(--border-radius-pill, 999px)",
                border: "1px solid var(--birch, #faf3e3)",
                background: "var(--white, #fff)",
                color: "var(--text-muted, #9e4d14)",
                fontSize: 12,
                fontFamily: "var(--font-body)",
                cursor: busy || historyLoading ? "not-allowed" : "pointer",
                opacity: busy || historyLoading ? 0.6 : 1,
              }}
            >
              {historyLoading ? "Loading…" : "Refresh"}
            </button>
          )}

          <div
            id="time-travel-list"
            data-testid="time-travel-list"
            role="list"
            style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs, 8px)" }}
          >
            {isEmpty ? (
              <p
                data-testid="time-travel-empty"
                style={{
                  margin: 0,
                  fontFamily: "var(--font-body)",
                  fontSize: 13,
                  color: "var(--text-muted, #9e4d14)",
                }}
              >
                No checkpoints yet.
              </p>
            ) : (
              entries.map((entry) => {
                const checkpointId = entry.checkpoint_id;
                const parentId = entry.parent_checkpoint_id ?? null;
                const createdAt = entry.created_at;
                const ns = entry.checkpoint_ns;
                const count = messagesCount(entry);
                const isSelected = selectedCheckpointId !== null && selectedCheckpointId === checkpointId;
                return (
                  <div
                    key={checkpointId}
                    data-testid="time-travel-entry"
                    data-checkpoint-id={checkpointId}
                    data-selected={isSelected ? "true" : "false"}
                    role="listitem"
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: "6px",
                      padding: "10px 12px",
                      borderRadius: "var(--border-radius-sm, 12px)",
                      border: `1px solid ${isSelected ? "var(--gold, #eda94f)" : "var(--border-hairline, rgba(99,19,0,0.1))"}`,
                      background: isSelected ? "var(--gold-20, rgba(237,169,79,0.2))" : "var(--surface-card, #fff)",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--space-xs, 8px)" }}>
                      <span
                        title={checkpointId}
                        style={{
                          fontFamily: "var(--font-body)",
                          fontSize: 12,
                          fontWeight: 600,
                          color: "var(--text-ink, #631300)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {shortId(checkpointId)}
                      </span>
                      <span
                        style={{
                          fontFamily: "var(--font-body)",
                          fontSize: 11,
                          color: "var(--text-muted, #9e4d14)",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {formatTime(createdAt)}
                      </span>
                    </div>

                    <div
                      style={{
                        display: "flex",
                        flexWrap: "wrap",
                        gap: "var(--space-xs, 8px)",
                        fontFamily: "var(--font-body)",
                        fontSize: 11,
                        color: "var(--text-muted, #9e4d14)",
                      }}
                    >
                      <span data-testid="time-travel-count">{count} msgs</span>
                      {parentId !== null && parentId !== undefined && (
                        <span title={String(parentId)} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 140 }}>
                          parent {shortId(String(parentId))}
                        </span>
                      )}
                      {ns !== undefined && ns !== "" && (
                        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 120 }}>
                          ns {String(ns)}
                        </span>
                      )}
                    </div>

                    <div style={{ display: "flex", gap: "var(--space-xs, 8px)", marginTop: 2 }}>
                      <button
                        data-testid="time-travel-view-btn"
                        aria-label={`View checkpoint ${shortId(checkpointId)}`}
                        onClick={() => onTimeTravel(checkpointId)}
                        disabled={busy}
                        style={{
                          flex: 1,
                          padding: "6px 10px",
                          borderRadius: "var(--border-radius-pill, 999px)",
                          border: "1px solid var(--birch, #faf3e3)",
                          background: "var(--white, #fff)",
                          color: "var(--text-ink, #631300)",
                          fontFamily: "var(--font-body)",
                          fontSize: 12,
                          fontWeight: 600,
                          cursor: busy ? "not-allowed" : "pointer",
                          opacity: busy ? 0.6 : 1,
                        }}
                      >
                        View
                      </button>
                      <button
                        data-testid="time-travel-fork-btn"
                        aria-label={`Fork from ${shortId(checkpointId)}`}
                        onClick={() => onFork(checkpointId)}
                        disabled={busy}
                        style={{
                          flex: 1,
                          padding: "6px 10px",
                          borderRadius: "var(--border-radius-pill, 999px)",
                          border: "1px solid var(--carrot, #ea492a)",
                          background: "var(--carrot, #ea492a)",
                          color: "var(--white, #fff)",
                          fontFamily: "var(--font-body)",
                          fontSize: 12,
                          fontWeight: 600,
                          cursor: busy ? "not-allowed" : "pointer",
                          opacity: busy ? 0.6 : 1,
                        }}
                      >
                        Fork
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </>
      )}
    </section>
  );
}
