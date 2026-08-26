import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  MedicalLookupBubble,
  RememberFactCard,
  ReminderToolCard,
  ChangeScheduleCard,
} from "@/chat/renderers/medical";
import { chatTelemetrySink } from "@/chat/stream";

/**
 * Tests for the medical_lookup + named coach tool renderers (plan todo 8).
 * The medical answer is SAFETY-CRITICAL: it must render the relayed tool
 * result VERBATIM through the shared Markdown component — never paraphrased.
 */

const ANSWER = "Take Metformin with meals to reduce stomach upset.";

describe("MedicalLookupBubble (medical_lookup renderer)", () => {
  it("renders the completed answer byte-verbatim in a .bubble.assistant", () => {
    const { container } = render(<MedicalLookupBubble status="complete" result={ANSWER} />);
    const bubble = container.querySelector(".bubble.assistant");
    expect(bubble).not.toBeNull();
    // Byte-verbatim: rendered text === tool result content exactly.
    expect(bubble?.textContent).toBe(ANSWER);
    expect(screen.getByTestId("medical-answer")).toBeInTheDocument();
    // Mirrors AiBubble's Markdown rendering path.
    expect(bubble?.querySelector(".md-root")).not.toBeNull();
  });

  it("renders markdown structure without paraphrasing content", () => {
    const md = "**Take with food.**\n\n- Start low\n- Titrate slowly";
    const { container } = render(<MedicalLookupBubble status="complete" result={md} />);
    const bubble = container.querySelector(".bubble.assistant");
    expect(bubble?.querySelector(".md-strong")).not.toBeNull();
    expect(bubble?.querySelector(".md-ul")).not.toBeNull();
    const text = bubble?.textContent ?? "";
    expect(text).toContain("Take with food.");
    expect(text).toContain("Start low");
    expect(text).toContain("Titrate slowly");
  });

  it("shows a pending state while running and no bubble yet", () => {
    const { container } = render(<MedicalLookupBubble status="inProgress" />);
    expect(container.querySelector(".bubble.assistant")).toBeNull();
    expect(screen.getByTestId("medical-lookup-pending")).toBeInTheDocument();
  });

  it("keeps the pending state on executing with no result", () => {
    const { container } = render(<MedicalLookupBubble status="executing" result={undefined} />);
    expect(container.querySelector(".bubble.assistant")).toBeNull();
    expect(screen.getByTestId("medical-lookup-pending")).toBeInTheDocument();
  });
});

describe("RememberFactCard", () => {
  it("pending shows the running pill and never echoes args", () => {
    const { container } = render(
      <RememberFactCard name="remember_fact" status="executing" parameters={{ fact: "SECRET-FACT-CANARY" }} />,
    );
    expect(screen.getByTestId("tool-call-card").dataset.status).toBe("pending");
    expect(screen.getByText("Saving note")).toBeInTheDocument();
    expect(container.textContent ?? "").not.toContain("SECRET-FACT-CANARY");
  });

  it("complete shows Done without echoing the stored fact", () => {
    const { container } = render(
      <RememberFactCard name="remember_fact" status="complete" result="Saved" parameters={{ fact: "SECRET-FACT-CANARY" }} />,
    );
    expect(screen.getByTestId("tool-call-card").dataset.status).toBe("success");
    expect(container.textContent ?? "").not.toContain("SECRET-FACT-CANARY");
  });

  it("error surfaces the error message only", () => {
    render(<RememberFactCard name="remember_fact" status="complete" error="Store unavailable" />);
    const banner = screen.getByTestId("tool-call-error");
    expect(banner.getAttribute("role")).toBe("alert");
    expect(banner.textContent).toContain("Store unavailable");
  });
});

describe("ReminderToolCard (create/edit/cancel reminder)", () => {
  it("pending shows a running card, not the confirmed reminder visual", () => {
    render(
      <ReminderToolCard
        name="create_reminder"
        status="inProgress"
        parameters={{ title: "Friday check-in", weekday: "friday", time: "08:00" }}
      />,
    );
    expect(screen.getByTestId("tool-call-card").dataset.status).toBe("pending");
    expect(screen.getByText("Creating reminder")).toBeInTheDocument();
    expect(screen.queryByTestId("reminder-tool-confirmed")).toBeNull();
  });

  it("complete reuses ReminderCard visuals as the confirmed state", () => {
    render(
      <ReminderToolCard
        name="create_reminder"
        status="complete"
        result="ok"
        parameters={{ title: "Friday check-in", weekday: "friday", time: "08:00" }}
      />,
    );
    expect(screen.getByTestId("reminder-tool-confirmed")).toBeInTheDocument();
    expect(screen.getByText("Friday check-in")).toBeInTheDocument();
    expect(screen.getByText("Every Friday at 8:00 AM")).toBeInTheDocument();
  });

  it("edit_reminder complete uses the target field for the title", () => {
    render(
      <ReminderToolCard
        name="edit_reminder"
        status="complete"
        result="ok"
        parameters={{ target: "Lab draw", time: "14:30" }}
      />,
    );
    expect(screen.getByTestId("reminder-tool-confirmed")).toBeInTheDocument();
    expect(screen.getByText("Lab draw")).toBeInTheDocument();
  });

  it("cancel_reminder complete shows the canceled outcome without ReminderCard controls", () => {
    render(
      <ReminderToolCard
        name="cancel_reminder"
        status="complete"
        result="ok"
        parameters={{ target: "Old reminder" }}
      />,
    );
    expect(screen.getByTestId("reminder-tool-canceled")).toBeInTheDocument();
    expect(screen.getByText(/canceled/i)).toBeInTheDocument();
  });

  it("fail-closed on malformed args: pending label still renders, no crash, no raw JSON", () => {
    const { container } = render(
      <ReminderToolCard name="create_reminder" status="complete" result="ok" parameters={{ weird: true }} />,
    );
    expect(container.textContent ?? "").not.toContain('"weird"');
    expect(container.textContent ?? "").not.toContain("{");
  });

  it("error surfaces the error message only", () => {
    render(
      <ReminderToolCard
        name="create_reminder"
        status="complete"
        error="Reminder not scheduled: the title did not pass privacy checks."
        parameters={{ title: "x" }}
      />,
    );
    const banner = screen.getByTestId("tool-call-error");
    expect(banner.textContent).toContain("privacy checks");
  });
});

describe("ChangeScheduleCard (pre-interrupt running state)", () => {
  it("running shows the Updating schedule card and points at the interrupt", () => {
    render(<ChangeScheduleCard name="change_schedule" status="executing" parameters={{ whatever: 1 }} />);
    expect(screen.getByTestId("tool-call-card").dataset.status).toBe("pending");
    expect(screen.getByText("Updating schedule")).toBeInTheDocument();
    expect(screen.getByTestId("change-schedule-pending")).toBeInTheDocument();
  });

  it("complete resolves to success without echoing schedule args", () => {
    const { container } = render(
      <ChangeScheduleCard name="change_schedule" status="complete" result="done" parameters={{ secret: "SCHED-CANARY" }} />,
    );
    expect(screen.getByTestId("tool-call-card").dataset.status).toBe("success");
    expect(container.textContent ?? "").not.toContain("SCHED-CANARY");
  });
});

describe("registerMedicalRenderers wiring", () => {
  let captured: Array<{ name: string; render: (props: unknown) => React.ReactElement }>;

  beforeEach(async () => {
    captured = [];
    vi.resetModules();
    vi.doMock("@copilotkit/react-core/v2/headless", () => ({
      useRenderTool: (config: { name: string; render: (props: unknown) => React.ReactElement }) => {
        captured.push(config);
      },
    }));
  });

  afterEach(() => {
    vi.doUnmock("@copilotkit/react-core/v2/headless");
    vi.restoreAllMocks();
  });

  it("registers exactly the canonical named tools, no invented names", async () => {
    const { registerMedicalRenderers } = await import("@/chat/renderers/medical");
    const RegisterMedical = registerMedicalRenderers;
    render(<RegisterMedical />);
    const names = captured.map((c) => c.name).sort();
    expect(names).toEqual(
      ["cancel_reminder", "change_schedule", "create_reminder", "edit_reminder", "medical_lookup", "remember_fact"].sort(),
    );
  });

  it("the medical_lookup registration renders the verbatim bubble", async () => {
    const { registerMedicalRenderers } = await import("@/chat/renderers/medical");
    const RegisterMedical = registerMedicalRenderers;
    render(<RegisterMedical />);
    const med = captured.find((c) => c.name === "medical_lookup");
    expect(med).toBeDefined();
    const { container } = render(med!.render({ name: "medical_lookup", toolCallId: "t1", status: "complete", result: ANSWER }));
    expect(container.querySelector(".bubble.assistant")?.textContent).toBe(ANSWER);
  });

  it("no telemetry is emitted for known named tools", async () => {
    const spy = vi.fn();
    const prev = chatTelemetrySink.emit;
    chatTelemetrySink.emit = spy;
    try {
      const { registerMedicalRenderers } = await import("@/chat/renderers/medical");
      const RegisterMedical = registerMedicalRenderers;
    render(<RegisterMedical />);
      const remember = captured.find((c) => c.name === "remember_fact");
      render(remember!.render({ name: "remember_fact", toolCallId: "t2", status: "complete", result: "ok" }));
      expect(spy).not.toHaveBeenCalled();
    } finally {
      chatTelemetrySink.emit = prev;
    }
  });
});
