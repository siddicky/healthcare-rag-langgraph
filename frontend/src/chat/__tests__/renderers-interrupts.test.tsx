import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { createElement } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import type { InterruptEvent, InterruptRenderProps } from "@copilotkit/react-core/v2/headless";
import {
  interruptValueFromEvent,
  isValidResumePayload,
} from "@/chat/renderers/interrupts";
import { chatTelemetrySink } from "@/chat/stream";

/**
 * Tests for the useInterrupt handlers (plan todo 9). The hooks are captured
 * via module mock (same pattern as renderers-medical.test.tsx) and driven
 * directly: `enabled` gating on payload shape, rendered card + testids, and
 * the exact `{accept, fields?}` resume shapes perimeter._validate_resume
 * admits — every produced payload round-trips through the ported
 * isValidResumePayload guard.
 */

interface CapturedInterrupt {
  agentId: string | undefined;
  enabled: (event: InterruptEvent) => boolean;
  render: (props: {
    event: InterruptEvent;
    resolve: InterruptRenderProps["resolve"];
    cancel?: InterruptRenderProps["cancel"];
  }) => React.ReactElement;
}

let captured: CapturedInterrupt[];

beforeEach(async () => {
  captured = [];
  vi.resetModules();
  vi.doMock("@copilotkit/react-core/v2/headless", () => ({
    useInterrupt: (config: CapturedInterrupt) => {
      captured.push(config);
      return null;
    },
  }));
  const mod = await import("@/chat/renderers/interrupts");
  const { registerInterruptHandlers } = mod;
  render(createElement(registerInterruptHandlers));
});

afterEach(() => {
  vi.doUnmock("@copilotkit/react-core/v2/headless");
  vi.restoreAllMocks();
});

const CALENDAR_PAYLOAD = {
  eventLabel: "Friday check-in",
  fromLabel: "Not scheduled",
  toLabel: "Fri, Aug 28 · 8:00 AM UTC",
  reason: "Add this event to your schedule.",
  status: "pending",
};

const MEMORY_PAYLOAD = {
  sourceLabel: "intake-form.pdf",
  fields: [
    { key: "medication", label: "Medication", value: "Metformin", needsReview: false },
    { key: "dose", label: "Dose", value: "500mg", needsReview: true },
  ],
};

function legacyEvent(value: unknown): InterruptEvent {
  return { name: "on_interrupt", value };
}

/** AG-UI standard-interrupt envelope: LangGraph value rides metadata.value. */
function standardEvent(value: unknown): InterruptEvent {
  return {
    name: "on_interrupt",
    value: { id: "int-1", reason: "tool_call", metadata: { value } },
  };
}

function calendarConfig(): CapturedInterrupt {
  const config = captured.find((c) => c.enabled(legacyEvent(CALENDAR_PAYLOAD)));
  expect(config).toBeDefined();
  return config!;
}

function memoryConfig(): CapturedInterrupt {
  const config = captured.find((c) => c.enabled(legacyEvent(MEMORY_PAYLOAD)));
  expect(config).toBeDefined();
  return config!;
}

describe("registerInterruptHandlers wiring", () => {
  it("registers exactly two handlers scoped to agentId 'coach'", () => {
    expect(captured).toHaveLength(2);
    for (const config of captured) expect(config.agentId).toBe("coach");
  });
});

describe("enabled — payload-shape gating", () => {
  it("calendar handler admits only calendar-change payloads", () => {
    const config = calendarConfig();
    expect(config.enabled(legacyEvent(CALENDAR_PAYLOAD))).toBe(true);
    expect(config.enabled(standardEvent(CALENDAR_PAYLOAD))).toBe(true);
    expect(config.enabled(legacyEvent(MEMORY_PAYLOAD))).toBe(false);
    expect(config.enabled(legacyEvent({ nonsense: true }))).toBe(false);
    expect(config.enabled(legacyEvent("a string"))).toBe(false);
  });

  it("memory handler admits only memory-extraction payloads", () => {
    const config = memoryConfig();
    expect(config.enabled(legacyEvent(MEMORY_PAYLOAD))).toBe(true);
    expect(config.enabled(standardEvent(MEMORY_PAYLOAD))).toBe(true);
    expect(config.enabled(standardEvent(JSON.stringify(MEMORY_PAYLOAD)))).toBe(true);
    expect(config.enabled(legacyEvent(CALENDAR_PAYLOAD))).toBe(false);
    expect(config.enabled(legacyEvent(null))).toBe(false);
  });

  it("the two predicates are disjoint — at most one card per interrupt", () => {
    for (const payload of [CALENDAR_PAYLOAD, MEMORY_PAYLOAD, { x: 1 }, undefined]) {
      const hits = captured.filter((c) => c.enabled(legacyEvent(payload))).length;
      expect(hits).toBeLessThanOrEqual(1);
    }
  });
});

describe("interruptValueFromEvent", () => {
  it("passes legacy payloads through untouched", () => {
    expect(interruptValueFromEvent(legacyEvent(CALENDAR_PAYLOAD))).toEqual(CALENDAR_PAYLOAD);
  });

  it("unwraps metadata.value off a standard Interrupt envelope", () => {
    expect(interruptValueFromEvent(standardEvent(MEMORY_PAYLOAD))).toEqual(MEMORY_PAYLOAD);
  });

  it("falls back to .value when metadata is absent on an envelope-shaped value", () => {
    const event = legacyEvent({ id: "i", reason: "r", value: CALENDAR_PAYLOAD });
    expect(interruptValueFromEvent(event)).toEqual(CALENDAR_PAYLOAD);
  });
});

describe("calendar-change handler rendering + resolve shapes", () => {
  it("renders CalendarChangeCard status=pending inside data-testid=interrupt-card", () => {
    const element = calendarConfig().render({
      event: legacyEvent(CALENDAR_PAYLOAD),
      resolve: vi.fn(),
    });
    const { container } = render(element);
    expect(screen.getByTestId("interrupt-card")).toBeInTheDocument();
    expect(container.textContent).toContain("Schedule change requested");
    expect(container.textContent).toContain("Friday check-in");
    expect(screen.getByRole("button", { name: "Confirm change" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep original time" })).toBeInTheDocument();
  });

  it("Confirm resolves exactly {accept: true}", () => {
    const resolve = vi.fn();
    render(calendarConfig().render({ event: legacyEvent(CALENDAR_PAYLOAD), resolve }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm change" }));
    expect(resolve).toHaveBeenCalledTimes(1);
    expect(resolve).toHaveBeenCalledWith({ accept: true });
    expect(isValidResumePayload(resolve.mock.calls[0]![0])).toBe(true);
  });

  it("Decline resolves exactly {accept: false}", () => {
    const resolve = vi.fn();
    render(calendarConfig().render({ event: legacyEvent(CALENDAR_PAYLOAD), resolve }));
    fireEvent.click(screen.getByRole("button", { name: "Keep original time" }));
    expect(resolve).toHaveBeenCalledWith({ accept: false });
    expect(isValidResumePayload(resolve.mock.calls[0]![0])).toBe(true);
  });

  it("resolves identically through the standard-interrupt envelope", () => {
    const resolve = vi.fn();
    render(calendarConfig().render({ event: standardEvent(CALENDAR_PAYLOAD), resolve }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm change" }));
    expect(resolve).toHaveBeenCalledWith({ accept: true });
  });
});

describe("memory-extraction handler rendering + resolve shapes", () => {
  it("renders MemoryExtractionCard with editable fields inside data-testid=interrupt-card", () => {
    const element = memoryConfig().render({
      event: legacyEvent(MEMORY_PAYLOAD),
      resolve: vi.fn(),
    });
    const { container } = render(element);
    expect(screen.getByTestId("interrupt-card")).toBeInTheDocument();
    expect(container.textContent).toContain("intake-form.pdf");
    expect(container.textContent).toContain("Metformin");
    expect(screen.getByRole("button", { name: "Save to profile" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Discard" })).toBeInTheDocument();
  });

  it("renders extracted fields when AG-UI serializes metadata.value", () => {
    const element = memoryConfig().render({
      event: standardEvent(JSON.stringify(MEMORY_PAYLOAD)),
      resolve: vi.fn(),
    });
    render(element);
    expect(screen.getByTestId("interrupt-card")).toHaveTextContent("intake-form.pdf");
    expect(screen.getByTestId("interrupt-card")).toHaveTextContent("500mg");
  });

  it("Save resolves {accept: true, fields:[{key,value}]} with member edits applied", () => {
    const resolve = vi.fn();
    render(memoryConfig().render({ event: legacyEvent(MEMORY_PAYLOAD), resolve }));
    fireEvent.click(screen.getByRole("button", { name: "Edit Dose" }));
    const doseInput = screen.getByDisplayValue("500mg") as HTMLInputElement;
    fireEvent.change(doseInput, { target: { value: "850mg" } });
    fireEvent.blur(doseInput);
    fireEvent.click(screen.getByRole("button", { name: "Save to profile" }));
    expect(resolve).toHaveBeenCalledTimes(1);
    const payload = resolve.mock.calls[0]![0];
    expect(payload).toEqual({
      accept: true,
      fields: [
        { key: "medication", value: "Metformin" },
        { key: "dose", value: "850mg" },
      ],
    });
    // Perimeter shape: fields carry ONLY {key, value} — no label/needsReview leak.
    expect(isValidResumePayload(payload)).toBe(true);
  });

  it("Discard resolves exactly {accept: false}", () => {
    const resolve = vi.fn();
    render(memoryConfig().render({ event: legacyEvent(MEMORY_PAYLOAD), resolve }));
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(resolve).toHaveBeenCalledWith({ accept: false });
    expect(isValidResumePayload(resolve.mock.calls[0]![0])).toBe(true);
  });
});

describe("fail-closed + perimeter round-trip", () => {
  it("unknown payloads are admitted by NO handler and emit telemetry when forced into a render", async () => {
    // Same fresh module instance the handlers use (vi.resetModules ran in beforeEach).
    const { chatTelemetrySink } = await import("@/chat/stream");
    const spy = vi.fn();
    const prev = chatTelemetrySink.emit;
    chatTelemetrySink.emit = spy;
    try {
      for (const config of captured) {
        expect(config.enabled(legacyEvent({ weird: { deep: true } }))).toBe(false);
        const element = config.render({ event: legacyEvent({ weird: 1 }), resolve: vi.fn() });
        const { container } = render(element);
        expect(screen.queryByTestId("interrupt-card")).toBeNull();
        expect(container.textContent).not.toContain('"weird"');
      }
      expect(spy).toHaveBeenCalled();
    } finally {
      chatTelemetrySink.emit = prev;
    }
  });

  it("EVERY resolve payload across both cards satisfies isValidResumePayload (perimeter shape)", () => {
    const payloads: unknown[] = [];
    const collect: InterruptRenderProps["resolve"] = (payload) => {
      payloads.push(payload);
      return Promise.resolve(undefined);
    };
    const click = (
      config: CapturedInterrupt,
      event: InterruptEvent,
      button: string,
    ): void => {
      const view = render(config.render({ event, resolve: collect }));
      fireEvent.click(screen.getByRole("button", { name: button }));
      view.unmount();
    };
    const calendar = calendarConfig();
    const memory = memoryConfig();
    click(calendar, legacyEvent(CALENDAR_PAYLOAD), "Confirm change");
    click(calendar, legacyEvent(CALENDAR_PAYLOAD), "Keep original time");
    click(memory, legacyEvent(MEMORY_PAYLOAD), "Save to profile");
    click(memory, legacyEvent(MEMORY_PAYLOAD), "Discard");
    expect(payloads).toHaveLength(4);
    for (const payload of payloads) {
      expect(isValidResumePayload(payload)).toBe(true);
      const record = payload as Record<string, unknown>;
      expect(Object.keys(record).every((k) => k === "accept" || k === "fields")).toBe(true);
    }
  });

  it("isValidResumePayload rejects malformed resumes the way the perimeter does", () => {
    expect(isValidResumePayload({ accept: "yes" })).toBe(false);
    expect(isValidResumePayload({})).toBe(false);
    expect(isValidResumePayload({ accept: true, fields: [{ key: "k" }] })).toBe(false);
    expect(isValidResumePayload({ accept: true, fields: [{ key: "k", value: 3 }] })).toBe(false);
    expect(isValidResumePayload(null)).toBe(false);
    expect(isValidResumePayload({ accept: true })).toBe(true);
    expect(isValidResumePayload({ accept: true, fields: [] })).toBe(true);
  });
});
