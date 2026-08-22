"use client";

import { useState } from "react";

export interface ReminderSchedule {
  weekday: string;
  /** 24h "HH:MM", from the native time input. */
  time: string;
  timeLabel: string;
  /** e.g. "Every Monday at 8:00 AM" */
  label: string;
}

export interface ReminderCardProps {
  title: string;
  /** Human-readable recurrence — the display form of the underlying cron schedule, e.g. "Every Monday at 8:00 AM". */
  schedule: string;
  /** Next scheduled fire time, e.g. "Mon, Aug 24 · 8:00 AM". */
  nextRun?: string;
  /** Backing values for the edit form's weekday select. Default 'Monday'. */
  weekday?: string;
  /** Backing value for the edit form's native time input (24h "HH:MM"). Default '08:00'. */
  time?: string;
  active?: boolean;
  /** Denser row (no icon/edit/cancel, just title + schedule + toggle) for a reminders list. */
  compact?: boolean;
  onToggle?: (active: boolean) => void;
  onCancel?: () => void;
  /** Fired when the member edits the weekday/time via the built-in picker and saves. Not shown in compact mode. */
  onScheduleChange?: (schedule: ReminderSchedule) => void;
}

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] as const;

function formatTime(t: string): string {
  const [hStr, mStr] = t.split(":");
  const h = Number(hStr);
  const m = Number(mStr);
  const period = h >= 12 ? "PM" : "AM";
  const hh = ((h + 11) % 12) + 1;
  return `${hh}:${String(m).padStart(2, "0")} ${period}`;
}

function Toggle({ active, onToggle }: { active: boolean; onToggle?: (active: boolean) => void }) {
  return (
    <button
      aria-label={active ? "Pause reminder" : "Resume reminder"}
      onClick={() => {
        if (onToggle) onToggle(!active);
      }}
      style={{
        width: 36,
        height: 20,
        borderRadius: 100,
        border: "none",
        background: active ? "var(--carrot)" : "var(--rust-10)",
        position: "relative",
        cursor: "pointer",
        flexShrink: 0,
        padding: 0,
      }}
    >
      <span
        style={{
          position: "absolute",
          top: 2,
          left: active ? 18 : 2,
          width: 16,
          height: 16,
          borderRadius: "50%",
          background: "var(--white)",
          transition: "left 0.15s ease",
        }}
      />
    </button>
  );
}

export function ReminderCard({
  title,
  schedule,
  nextRun,
  weekday = "Monday",
  time = "08:00",
  active = true,
  compact = false,
  onToggle,
  onCancel,
  onScheduleChange,
}: ReminderCardProps) {
  const [editing, setEditing] = useState(false);
  const [draftWeekday, setDraftWeekday] = useState(weekday);
  const [draftTime, setDraftTime] = useState(time);

  function save() {
    const timeLabel = formatTime(draftTime);
    if (onScheduleChange) {
      onScheduleChange({
        weekday: draftWeekday,
        time: draftTime,
        timeLabel,
        label: `Every ${draftWeekday} at ${timeLabel}`,
      });
    }
    setEditing(false);
  }

  if (compact) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "10px 0",
          borderBottom: "1px solid var(--rust-10)",
          width: "100%",
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: "var(--rust)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {title}
          </div>
          <div style={{ fontSize: 12, color: "var(--camel)" }}>
            {schedule}
            {!active && " · Paused"}
          </div>
        </div>
        <Toggle active={active} onToggle={onToggle} />
      </div>
    );
  }

  return (
    <div className="card" style={{ maxWidth: 340 }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 12 }}>
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: 10,
            background: "var(--gold-20)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="var(--carrot-accessible)"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="13" r="8" />
            <path d="M12 9v4l3 2" />
            <path d="M9 2h6" />
          </svg>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: "var(--rust)" }}>{title}</div>
          {!editing && (
            <span className="tag" style={{ marginTop: 6, display: "inline-block" }}>
              {schedule}
            </span>
          )}
        </div>
        <Toggle active={active} onToggle={onToggle} />
      </div>

      {editing ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
          <div style={{ display: "flex", gap: 8 }}>
            <select
              className="form-select"
              value={draftWeekday}
              onChange={(e) => setDraftWeekday(e.target.value)}
              style={{ flex: 1, padding: "8px 10px", fontSize: 14 }}
            >
              {WEEKDAYS.map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
            <input
              type="time"
              className="form-input"
              value={draftTime}
              onChange={(e) => setDraftTime(e.target.value)}
              style={{ flex: 1, padding: "8px 10px", fontSize: 14 }}
            />
          </div>
          <div className="btn-group">
            <button className="btn btn-primary btn-sm" onClick={save}>
              Save time
            </button>
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => {
                setDraftWeekday(weekday);
                setDraftTime(time);
                setEditing(false);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          {nextRun && (
            <p style={{ margin: "0 0 12px", fontSize: 13, color: "var(--camel)" }}>
              Next: <b style={{ color: "var(--rust)" }}>{nextRun}</b>
              {!active && " · Paused"}
            </p>
          )}
          <div style={{ display: "flex", gap: 16 }}>
            <button
              onClick={() => setEditing(true)}
              style={{
                border: "none",
                background: "transparent",
                color: "var(--rust)",
                fontSize: 13,
                fontWeight: 600,
                cursor: "pointer",
                padding: 0,
              }}
            >
              Edit schedule
            </button>
            <button
              onClick={() => {
                if (onCancel) onCancel();
              }}
              style={{
                border: "none",
                background: "transparent",
                color: "var(--carrot-accessible)",
                fontSize: 13,
                fontWeight: 600,
                cursor: "pointer",
                padding: 0,
              }}
            >
              Cancel reminder
            </button>
          </div>
        </>
      )}
    </div>
  );
}
