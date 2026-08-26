import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  LogInjectionToolView,
  LogMetricToolView,
  ReminderListToolView,
  ViewScheduleToolView,
} from "@/chat/renderers/envelope-tools";
import { chatTelemetrySink, type ChatTelemetryEvent } from "@/chat/stream";

const SCOPE = "scope-1";

function envelope(blockId: string, data: unknown): string {
  return JSON.stringify({ turn_scope_id: SCOPE, block_id: blockId, data, text: "ok" });
}

const TREND_DATA = {
  label: "Weight",
  value: "182.4",
  unit: "kg",
  delta: "-2.0 kg",
  deltaGood: true,
  points: [189, 188, 186.5, 185, 184, 183, 182.4],
};

const INJECTION_DATA = {
  medicationName: "Ozempic",
  doseLabel: "0.25 mg",
  days: [
    { date: "2026-08-24", status: "logged" },
    { date: "2026-08-26", status: "upcoming" },
    { date: "2026-08-28", status: "due" },
  ],
  nextDoseLabel: "Friday",
};

const CALENDAR_DATA = {
  monthLabel: "August 2026",
  firstWeekday: 6,
  daysInMonth: 31,
  highlights: [{ date: 24, type: "injection" }, { date: 25, type: "today" }],
};

const REMINDERS_DATA = {
  items: [
    { reminder_id: "r-1", title: "Weekly weight log", scheduleLabel: "Every Monday at 8:00 AM", active: true },
    { reminder_id: "r-2", title: "Hydration nudge", scheduleLabel: "Every day at 12:00 PM", active: false, nextRun: "Mon, Aug 24" },
  ],
};

describe("envelope tool renderers (copilotkit)", () => {
  let chatEvents: ChatTelemetryEvent[];

  beforeEach(() => {
    chatEvents = [];
    vi.spyOn(chatTelemetrySink, "emit").mockImplementation((event) => {
      chatEvents.push(event);
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("log_metric -> TrendCard", () => {
    it("renders the trend card from the tool's own envelope", () => {
      render(<LogMetricToolView status="complete" result={envelope("trend:weight", TREND_DATA)} />);
      expect(screen.getByTestId("log-metric-card")).toBeInTheDocument();
      expect(screen.getByText("Weight")).toBeInTheDocument();
      expect(screen.getByText("182.4")).toBeInTheDocument();
      expect(screen.getByText("-2.0 kg")).toBeInTheDocument();
      expect(document.querySelector("svg polyline")).toBeTruthy();
    });

    it("shows the shimmer while in progress", () => {
      render(<LogMetricToolView status="inProgress" result={undefined} />);
      expect(screen.getByTestId("tool-call-pending")).toBeInTheDocument();
      expect(screen.queryByTestId("log-metric-card")).toBeNull();
    });

    it("renders nothing + chatTelemetry for a malformed envelope", () => {
      const { container } = render(
        <LogMetricToolView status="complete" result={envelope("trend:weight", { label: 42 })} />,
      );
      expect(container).toBeEmptyDOMElement();
      expect(chatEvents).toEqual([
        expect.objectContaining({ kind: "unknown_tool", name: "log_metric" }),
      ]);
    });

    it("renders nothing + chatTelemetry when the block id does not match the tool", () => {
      const { container } = render(
        <LogMetricToolView status="complete" result={envelope("weekstrip:injection", INJECTION_DATA)} />,
      );
      expect(container).toBeEmptyDOMElement();
      expect(chatEvents[0]).toMatchObject({ kind: "unknown_tool", name: "log_metric" });
    });
  });

  describe("log_injection -> InjectionTracker (sparse week-strip adapter)", () => {
    it("renders an exactly-seven Monday-first strip from sparse days", () => {
      render(<LogInjectionToolView status="complete" result={envelope("weekstrip:injection", INJECTION_DATA)} />);
      expect(screen.getByTestId("log-injection-card")).toBeInTheDocument();
      expect(screen.getByText("Ozempic")).toBeInTheDocument();
      expect(screen.getByText("0.25 mg")).toBeInTheDocument();
      expect(screen.getAllByText(/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)$/)).toHaveLength(7);
      expect(document.body.textContent ?? "").not.toContain("turn_scope_id");
    });

    it("renders nothing + chatTelemetry for invalid day entries", () => {
      const { container } = render(
        <LogInjectionToolView
          status="complete"
          result={envelope("weekstrip:injection", { ...INJECTION_DATA, days: [{ date: 42, status: "logged" }] })}
        />,
      );
      expect(container).toBeEmptyDOMElement();
      expect(chatEvents[0]).toMatchObject({ kind: "unknown_tool", name: "log_injection" });
    });
  });

  describe("view_schedule -> MiniCalendar", () => {
    it("renders the month grid with highlights", () => {
      render(<ViewScheduleToolView status="complete" result={envelope("calendar:2026-08", CALENDAR_DATA)} />);
      expect(screen.getByTestId("view-schedule-card")).toBeInTheDocument();
      expect(screen.getByText("August 2026")).toBeInTheDocument();
      expect(screen.getAllByText("24").length).toBeGreaterThan(0);
    });

    it("renders nothing + chatTelemetry for out-of-range calendar data", () => {
      const { container } = render(
        <ViewScheduleToolView
          status="complete"
          result={envelope("calendar:2026-08", { ...CALENDAR_DATA, firstWeekday: 9 })}
        />,
      );
      expect(container).toBeEmptyDOMElement();
      expect(chatEvents[0]).toMatchObject({ kind: "unknown_tool", name: "view_schedule" });
    });
  });

  describe("reminder tools -> compact ReminderCard list", () => {
    it("renders read-only compact rows from the reminders:list envelope", () => {
      render(
        <ReminderListToolView toolName="create_reminder" status="complete" result={envelope("reminders:list", REMINDERS_DATA)} />,
      );
      expect(screen.getByTestId("reminder-list")).toBeInTheDocument();
      expect(screen.getByText("Weekly weight log")).toBeInTheDocument();
      expect(screen.getByText("Every Monday at 8:00 AM")).toBeInTheDocument();
      expect(screen.getByText("Hydration nudge")).toBeInTheDocument();
      expect(screen.getByText("Every day at 12:00 PM · Paused")).toBeInTheDocument();
    });

    it("renders nothing + chatTelemetry for a non-envelope result", () => {
      const { container } = render(
        <ReminderListToolView toolName="cancel_reminder" status="complete" result="plain text result" />,
      );
      expect(container).toBeEmptyDOMElement();
      expect(chatEvents[0]).toMatchObject({ kind: "unknown_tool", name: "cancel_reminder" });
    });
  });
});
