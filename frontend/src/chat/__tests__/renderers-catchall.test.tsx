import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { CatchAllToolCard } from "@/chat/renderers/catch-all";
import { chatTelemetrySink } from "@/chat/stream";

/**
 * Fail-closed catch-all renderer (plan todo 8): renders tool NAME + status
 * pill + error surface ONLY. Never raw args, never raw result (PHI posture).
 */

const ARGS_CANARY = "CANARY-ARGS-9f2b7c";
const RESULT_CANARY = "CANARY-RESULT-1a4e8d";

function renderUnknown() {
  return render(
    <CatchAllToolCard
      name="definitely_not_a_tool"
      status="complete"
      result={RESULT_CANARY}
      parameters={{ secret: ARGS_CANARY }}
    />,
  );
}

describe("CatchAllToolCard", () => {
  let spy: ReturnType<typeof vi.fn>;
  let prev: typeof chatTelemetrySink.emit;

  beforeEach(() => {
    spy = vi.fn();
    prev = chatTelemetrySink.emit;
    chatTelemetrySink.emit = spy as unknown as typeof chatTelemetrySink.emit;
  });

  afterEach(() => {
    chatTelemetrySink.emit = prev;
  });

  it("renders name + status card with preserved testids and attributes", () => {
    const { container } = renderUnknown();
    const wrap = screen.getByTestId("tool-call-wrap");
    expect(wrap).toBeInTheDocument();
    const card = screen.getByTestId("tool-call-card");
    expect(card).toBeInTheDocument();
    expect(card.getAttribute("data-tool")).toBe("definitely_not_a_tool");
    expect(card.getAttribute("data-status")).toBe("success");
    // The tool NAME is the only payload surface.
    expect(card.textContent).toContain("definitely_not_a_tool");
    expect(container.textContent ?? "").not.toContain(ARGS_CANARY);
    expect(container.textContent ?? "").not.toContain(RESULT_CANARY);
  });

  it("fail-closed on unknown tool: zero args/result echo of a planted canary", () => {
    const { container } = render(
      <CatchAllToolCard
        name="mystery_tool"
        status="executing"
        parameters={{
          nested: { deeply: [ARGS_CANARY, { evenDeeper: RESULT_CANARY }] },
        }}
      />,
    );
    const html = container.innerHTML;
    expect(html).not.toContain(ARGS_CANARY);
    expect(html).not.toContain(RESULT_CANARY);
    expect(screen.queryByTestId("tool-call-args")).toBeNull();
    expect(screen.queryByTestId("tool-call-result")).toBeNull();
  });

  it("emits exactly one structured unknown_tool telemetry event", () => {
    renderUnknown();
    expect(spy).toHaveBeenCalledTimes(1);
    const evt = spy.mock.calls[0]?.[0] as { kind: string; name?: string };
    expect(evt.kind).toBe("unknown_tool");
    expect(evt.name).toBe("definitely_not_a_tool");
  });

  it("telemetry never carries args or results", () => {
    renderUnknown();
    const serialized = JSON.stringify(spy.mock.calls);
    expect(serialized).not.toContain(ARGS_CANARY);
    expect(serialized).not.toContain(RESULT_CANARY);
  });

  it("does NOT fire unknown_tool telemetry for known coach tools", () => {
    render(<CatchAllToolCard name="log_metric" status="executing" parameters={{ value: 80 }} />);
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByTestId("tool-call-card").getAttribute("data-tool")).toBe("log_metric");
  });

  it("maps inProgress/executing to pending and complete to success", () => {
    const { rerender } = render(<CatchAllToolCard name="x_tool" status="inProgress" />);
    expect(screen.getByTestId("tool-call-card").getAttribute("data-status")).toBe("pending");
    rerender(<CatchAllToolCard name="x_tool" status="executing" />);
    expect(screen.getByTestId("tool-call-card").getAttribute("data-status")).toBe("pending");
    rerender(<CatchAllToolCard name="x_tool" status="complete" result="ok" />);
    expect(screen.getByTestId("tool-call-card").getAttribute("data-status")).toBe("success");
  });

  it("error surface shows the error message but still never args/results", () => {
    const { container } = render(
      <CatchAllToolCard
        name="broken_tool"
        status="complete"
        error="Upstream unavailable"
        parameters={{ a: ARGS_CANARY }}
        result={RESULT_CANARY}
      />,
    );
    const banner = screen.getByTestId("tool-call-error");
    expect(banner.getAttribute("role")).toBe("alert");
    expect(banner.textContent).toContain("Upstream unavailable");
    expect(container.textContent ?? "").not.toContain(ARGS_CANARY);
    expect(container.textContent ?? "").not.toContain(RESULT_CANARY);
  });
});

describe("registerCatchAllRenderer wiring", () => {
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

  it("registers exactly one wildcard renderer", async () => {
    const { registerCatchAllRenderer } = await import("@/chat/renderers/catch-all");
    const RegisterCatchAll = registerCatchAllRenderer;
    render(<RegisterCatchAll />);
    expect(captured).toHaveLength(1);
    expect(captured[0]?.name).toBe("*");
  });

  it("the wildcard render output is fail-closed for an unknown tool", async () => {
    const { registerCatchAllRenderer } = await import("@/chat/renderers/catch-all");
    const RegisterCatchAll = registerCatchAllRenderer;
    render(<RegisterCatchAll />);
    const wildcard = captured[0]!;
    const { container } = render(
      wildcard.render({
        name: "totally_unknown",
        toolCallId: "t9",
        status: "complete",
        result: RESULT_CANARY,
        parameters: { secret: ARGS_CANARY },
      }),
    );
    expect(screen.getByTestId("tool-call-card").getAttribute("data-tool")).toBe("totally_unknown");
    expect(container.textContent ?? "").not.toContain(ARGS_CANARY);
    expect(container.textContent ?? "").not.toContain(RESULT_CANARY);
  });
});
