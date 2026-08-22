import type { CSSProperties } from "react";
import type { StripStatus } from "@/catalog/weekstrip";

export interface InjectionDay {
  label: string;
  /** 'logged' (filled carrot + check), 'due' (dashed outline — today's or overdue dose), 'today' (gold ring, not yet due), 'upcoming' (empty), 'muted' (no data for this slot — sparse-to-seven filler). */
  status: StripStatus;
}

export interface InjectionTrackerProps {
  medicationName: string;
  doseLabel: string;
  /** Exactly 7 entries, Monday first. */
  days: InjectionDay[];
  nextDoseLabel?: string;
}

export function InjectionTracker({
  medicationName,
  doseLabel,
  days = [],
  nextDoseLabel,
}: InjectionTrackerProps) {
  return (
    <div className="card" style={{ maxWidth: 380 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: 12,
        }}
      >
        <div>
          <div className="label" style={{ marginBottom: 4 }}>
            Injection tracker
          </div>
          <div style={{ fontFamily: "var(--font-headline)", fontSize: 20, color: "var(--rust)" }}>
            {medicationName}
          </div>
        </div>
        <span className="tag">{doseLabel}</span>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
        {days.map((d) => {
          const bg =
            d.status === "logged" ? "var(--carrot)" : d.status === "due" ? "var(--white)" : "transparent";
          const border =
            d.status === "due"
              ? "2px dashed var(--carrot-accessible)"
              : d.status === "today"
                ? "2px solid var(--gold)"
                : "1px solid var(--rust-10)";
          const color = d.status === "logged" ? "var(--white)" : "var(--rust)";
          const columnStyle: CSSProperties =
            d.status === "muted" ? { opacity: 0.35 } : {};
          return (
            <div
              key={d.label}
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 6,
                flex: 1,
                ...columnStyle,
              }}
            >
              <div
                style={{
                  width: 36,
                  height: 36,
                  borderRadius: "50%",
                  background: bg,
                  border,
                  color,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 13,
                  fontWeight: 600,
                }}
              >
                {d.status === "logged" ? "✓" : ""}
              </div>
              <span style={{ fontSize: 11, color: "var(--camel)" }}>{d.label}</span>
            </div>
          );
        })}
      </div>
      {nextDoseLabel && (
        <p style={{ marginTop: 14, marginBottom: 0, fontSize: 13, color: "var(--camel)" }}>
          Next dose: <b style={{ color: "var(--carrot-accessible)" }}>{nextDoseLabel}</b>
        </p>
      )}
    </div>
  );
}
