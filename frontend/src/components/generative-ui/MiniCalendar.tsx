"use client";

export interface CalendarHighlight {
  /** Day of month, 1-based. */
  date: number;
  type: "injection" | "checkin" | "today";
}

export interface MiniCalendarProps {
  monthLabel: string;
  /** 0=Sunday, matches the leading blank cells before day 1. */
  firstWeekday?: number;
  daysInMonth?: number;
  highlights?: CalendarHighlight[];
  onSelectDate?: (date: number) => void;
}

const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"] as const;

export function MiniCalendar({
  monthLabel,
  firstWeekday = 0,
  daysInMonth = 30,
  highlights = [],
  onSelectDate,
}: MiniCalendarProps) {
  const highlightMap = new Map(highlights.map((h) => [h.date, h]));
  const cells: (number | null)[] = [
    ...Array.from({ length: firstWeekday }, () => null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];
  const colors: Record<CalendarHighlight["type"], string> = {
    injection: "var(--carrot)",
    checkin: "var(--gold)",
    today: "var(--rust)",
  };
  return (
    <div className="card" style={{ maxWidth: 320 }}>
      <div className="label" style={{ marginBottom: 10 }}>
        {monthLabel}
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(7,1fr)",
          gap: 4,
          fontSize: 11,
          color: "var(--camel)",
          marginBottom: 4,
        }}
      >
        {WEEKDAYS.map((w, i) => (
          <div key={i} style={{ textAlign: "center" }}>
            {w}
          </div>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(7,1fr)", gap: 4 }}>
        {cells.map((day, i) => {
          const h = day !== null ? highlightMap.get(day) : undefined;
          return (
            <button
              key={i}
              disabled={day === null}
              onClick={() => {
                if (day !== null && onSelectDate) onSelectDate(day);
              }}
              style={{
                aspectRatio: "1",
                border: "none",
                borderRadius: "50%",
                fontSize: 12,
                cursor: day !== null ? "pointer" : "default",
                background: h ? colors[h.type] : "transparent",
                color: h ? "var(--white)" : day !== null ? "var(--rust)" : "transparent",
                fontWeight: h ? 700 : 400,
              }}
            >
              {day ?? ""}
            </button>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 14, marginTop: 12, fontSize: 11, color: "var(--camel)" }}>
        <span>
          <span
            style={{
              display: "inline-block",
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "var(--carrot)",
              marginRight: 4,
            }}
          />
          Injection
        </span>
        <span>
          <span
            style={{
              display: "inline-block",
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "var(--gold)",
              marginRight: 4,
            }}
          />
          Check-in
        </span>
      </div>
    </div>
  );
}
