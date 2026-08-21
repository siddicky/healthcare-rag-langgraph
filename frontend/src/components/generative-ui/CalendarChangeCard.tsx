"use client";

export interface CalendarChangeCardProps {
  /** e.g. "Friday check-in" */
  eventLabel: string;
  /** Current scheduled time, shown struck through. */
  fromLabel: string;
  /** Proposed new time, shown bold. */
  toLabel: string;
  /** Optional one-line context for why the change is proposed. */
  reason?: string;
  /** 'pending' shows Confirm/Decline buttons (the human-in-the-loop interrupt); 'confirmed'/'declined' shows the resolved outcome instead. */
  status?: "pending" | "confirmed" | "declined";
  onConfirm?: () => void;
  onDecline?: () => void;
}

export function CalendarChangeCard({
  eventLabel,
  fromLabel,
  toLabel,
  reason,
  status = "pending",
  onConfirm,
  onDecline,
}: CalendarChangeCardProps) {
  const resolved = status !== "pending";
  return (
    <div className="card card-elevated" style={{ maxWidth: 360 }}>
      <span className="label" style={{ marginBottom: 4 }}>
        Schedule change requested
      </span>
      <div style={{ fontSize: 16, fontWeight: 600, color: "var(--rust)", marginBottom: 10 }}>{eventLabel}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: reason ? 8 : 16 }}>
        <span style={{ fontSize: 14, color: "var(--camel)", textDecoration: "line-through" }}>{fromLabel}</span>
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="var(--carrot)"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ flexShrink: 0 }}
        >
          <path d="M5 12h14M13 6l6 6-6 6" />
        </svg>
        <span style={{ fontSize: 15, color: "var(--rust)", fontWeight: 700 }}>{toLabel}</span>
      </div>
      {reason && <p style={{ margin: "0 0 16px", fontSize: 13, color: "var(--camel)" }}>{reason}</p>}
      {resolved ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 13,
            fontWeight: 600,
            color: status === "confirmed" ? "var(--success)" : "var(--camel)",
          }}
        >
          {status === "confirmed" ? "✓ Confirmed" : "Declined — keeping the original time"}
        </div>
      ) : (
        <div className="btn-group">
          <button
            className="btn btn-primary btn-sm"
            onClick={() => {
              if (onConfirm) onConfirm();
            }}
          >
            Confirm change
          </button>
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => {
              if (onDecline) onDecline();
            }}
          >
            Keep original time
          </button>
        </div>
      )}
    </div>
  );
}
