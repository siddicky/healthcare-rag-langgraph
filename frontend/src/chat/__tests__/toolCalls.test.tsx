import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ToolCallCard, type ToolCallView } from "@/chat/components/ToolCallCard";
import { MessageList } from "@/chat/components/MessageList";
import { buildTurns } from "@/chat/model";
import { chatTelemetrySink } from "@/chat/stream";
import type { WireMessage } from "@/chat/model";

function makeCall(overrides: Partial<ToolCallView> & { name: string }): ToolCallView {
  return {
    id: overrides.id ?? "tc-1",
    callId: overrides.callId ?? overrides.id ?? "tc-1",
    name: overrides.name,
    args: overrides.args,
    input: overrides.input ?? overrides.args,
    output: overrides.output ?? overrides.result,
    result: overrides.result ?? overrides.output,
    status: overrides.status ?? "running",
    error: overrides.error,
    namespace: overrides.namespace ?? [],
  };
}

function wireMessagesForToolCalls(): WireMessage[] {
  return [
    { type: "human", id: "h1", content: "What does lipitor do?" },
    {
      type: "ai",
      id: "a1",
      content: "",
      tool_calls: [
        { id: "tc-med", name: "medical_lookup", args: { question: "What does lipitor do?" } },
        { id: "tc-copy", name: "copy_to_clipboard", args: { text: "copy this summary" } },
      ],
    } as unknown as WireMessage,
    { type: "tool", id: "t1", tool_call_id: "tc-med", content: "Lipitor is atorvastatin..." },
    { type: "tool", id: "t2", tool_call_id: "tc-copy", content: "copied" },
    { type: "ai", id: "a2", content: "Lipitor helps manage cholesterol." },
  ];
}

describe("ToolCallCard", () => {
  it("renders pending with spinner and truncated args, no raw JSON overflow", () => {
    const pending = makeCall({
      id: "tc-pending",
      name: "medical_lookup",
      args: { question: "a".repeat(800) },
      status: "running",
    });
    const { container } = render(<ToolCallCard call={pending} />);
    expect(screen.getByTestId("tool-call-card")).toBeInTheDocument();
    expect(screen.getByTestId("tool-call-card").dataset.status).toBe("pending");
    expect(screen.getByTestId("tool-call-pending")).toBeInTheDocument();
    const argsText = screen.getByTestId("tool-call-args").textContent ?? "";
    expect(argsText).toContain("…");
    expect(argsText.length).toBeLessThan(500);
    expect(argsText).toContain('"question"');
    expect(screen.queryByTestId("tool-call-result")).toBeNull();
    expect(screen.queryByTestId("tool-call-error")).toBeNull();
    const raw = container.textContent ?? "";
    expect(raw.length).toBeLessThan(1100);
  });

  it("pending→success shows result preview and check badge", () => {
    const pending = makeCall({ id: "tc-1", name: "medical_lookup", args: { question: "side effects" }, status: "running" });
    const { rerender } = render(<ToolCallCard call={pending} />);
    expect(screen.getByText("Running")).toBeInTheDocument();

    const success = makeCall({
      id: "tc-1",
      name: "medical_lookup",
      args: { question: "side effects" },
      status: "finished",
      output: "Lipitor side effects include muscle pain and liver enzyme changes. Detailed monograph excerpt here.",
    });
    rerender(<ToolCallCard call={success} />);
    expect(screen.getByTestId("tool-call-card").dataset.status).toBe("success");
    expect(screen.getByTestId("tool-call-result")).toBeInTheDocument();
    expect(screen.getByTestId("tool-call-result").textContent).toContain("Lipitor side effects");
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.queryByTestId("tool-call-pending")).toBeNull();
  });

  it("pending→error shows alert banner and no result", () => {
    const pending = makeCall({ id: "tc-2", name: "copy_to_clipboard", args: { text: "hello" }, status: "running" });
    const { rerender } = render(<ToolCallCard call={pending} />);
    expect(screen.getByText("Running")).toBeInTheDocument();

    const failed = makeCall({
      id: "tc-2",
      name: "copy_to_clipboard",
      args: { text: "hello" },
      status: "error",
      error: "Clipboard unavailable",
    });
    rerender(<ToolCallCard call={failed} />);
    expect(screen.getByTestId("tool-call-card").dataset.status).toBe("error");
    const banner = screen.getByTestId("tool-call-error");
    expect(banner).toBeInTheDocument();
    expect(banner.textContent).toContain("Clipboard unavailable");
    expect(banner.getAttribute("role")).toBe("alert");
    expect(screen.queryByTestId("tool-call-result")).toBeNull();
    expect(screen.queryByTestId("tool-call-pending")).toBeNull();
  });

  it("truncates very long result previews", () => {
    const call = makeCall({
      id: "tc-3",
      name: "query_lipitor",
      args: { q: "dosage" },
      status: "finished",
      output: "x".repeat(2000),
    });
    render(<ToolCallCard call={call} />);
    const result = screen.getByTestId("tool-call-result").textContent ?? "";
    expect(result.length).toBeLessThan(500);
    expect(result).toContain("…");
  });

  it("unknown tool renders generic card and emits telemetry without PII", () => {
    const spy = vi.fn();
    const prev = chatTelemetrySink.emit;
    chatTelemetrySink.emit = spy;
    const call = makeCall({
      id: "tc-unknown",
      name: "mystery_tool_xyz",
      args: { secret: "user@example.com" },
      status: "finished",
      output: "ok",
    });
    render(<ToolCallCard call={call} />);
    expect(screen.getByTestId("tool-call-card")).toBeInTheDocument();
    expect(screen.getByText(/Unknown tool/)).toBeInTheDocument();
    expect(spy).toHaveBeenCalled();
    const evt = spy.mock.calls[0]?.[0] as { kind: string; name?: string; detail?: string } | undefined;
    expect(evt?.kind).toBe("unknown_tool");
    expect(evt?.name).not.toContain("user@example.com");
    expect(evt?.detail).not.toContain("user@example.com");
    chatTelemetrySink.emit = prev;
  });

  it("uses labels for known tools and does not expose raw JSON blob", () => {
    const call = makeCall({
      id: "tc-4",
      name: "copy_to_clipboard",
      args: { text: "hello world" },
      status: "finished",
      output: "copied",
    });
    const { container } = render(<ToolCallCard call={call} />);
    expect(screen.getByText("Copy to clipboard")).toBeInTheDocument();
    expect(screen.getByText("copy_to_clipboard")).toBeInTheDocument();
    expect(container.textContent ?? "").not.toContain('"text": "hello world"'.repeat(2));
    const argsEl = screen.getByTestId("tool-call-args");
    expect(argsEl.textContent ?? "").toContain("hello world");
  });
});

describe("MessageList toolCalls wiring", () => {
  it("shows only the final assistant output and hides the raw tool call", () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "I can't breathe" },
      { type: "ai", id: "a1", content: "Intermediate safety output" },
      {
        type: "ai",
        id: "a2",
        content: "",
        tool_calls: [{ id: "tc-med", name: "medical_lookup", args: { query: "urgent breathing guidance" } }],
      } as unknown as WireMessage,
      { type: "tool", id: "t1", tool_call_id: "tc-med", content: "Final emergency guidance" },
      { type: "ai", id: "a3", content: "Final emergency guidance" },
    ];
    const turns = buildTurns(messages);
    const toolCalls: ToolCallView[] = [
      makeCall({
        id: "tc-med",
        name: "medical_lookup",
        args: { query: "urgent breathing guidance" },
        status: "finished",
        output: "Final emergency guidance",
      }),
    ];

    render(
      <MessageList turns={turns} pendingInterrupt={null} upload={{ phase: "idle" }} busy={false} onApprove={() => {}} latestAiMessageId={null} toolCalls={toolCalls} />,
    );

    expect(screen.queryByText("Intermediate safety output")).toBeNull();
    expect(screen.getAllByText("Final emergency guidance")).toHaveLength(1);
    expect(screen.queryByTestId("tool-call-wrap")).toBeNull();
  });

  it("keeps the last visible assistant output when a trailing tool placeholder is empty", () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "Show my schedule" },
      { type: "ai", id: "a1", content: "Here is your current schedule." },
      {
        type: "ai",
        id: "a2",
        content: "",
        tool_calls: [{ id: "tc-schedule", name: "view_schedule", args: { month: "2026-08" } }],
      },
    ];

    render(
      <MessageList turns={buildTurns(messages)} pendingInterrupt={null} upload={{ phase: "idle" }} busy={false} onApprove={() => {}} latestAiMessageId={null} />,
    );

    expect(screen.getByText("Here is your current schedule.")).toBeInTheDocument();
    expect(screen.queryByTestId("tool-call-card")).toBeNull();
  });

  it("keeps stream.toolCalls out of the member transcript", () => {
    const messages = wireMessagesForToolCalls();
    const turns = buildTurns(messages);
    const toolCalls: ToolCallView[] = [
      makeCall({ id: "tc-med", name: "medical_lookup", args: { question: "What does lipitor do?" }, status: "finished", output: "Lipitor is atorvastatin..." }),
      makeCall({ id: "tc-copy", name: "copy_to_clipboard", args: { text: "copy this summary" }, status: "finished", output: "copied" }),
    ];
    const { container } = render(
      <MessageList turns={turns} pendingInterrupt={null} upload={{ phase: "idle" }} busy={false} onApprove={() => {}} latestAiMessageId={null} toolCalls={toolCalls} />,
    );
    expect(screen.queryByTestId("tool-call-card")).toBeNull();
    expect(screen.getByText("Lipitor helps manage cholesterol.")).toBeInTheDocument();
    expect(container.textContent ?? "").not.toContain('"tool_calls"');
    expect(container.textContent ?? "").not.toContain('"turn_scope_id"');
  });

  it("renders a completed envelope tool as generative UI without raw arguments", () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "log my waist" },
      {
        type: "ai",
        id: "a1",
        content: "",
        tool_calls: [{ id: "tc-waist", name: "log_metric", args: { metric: "waist", value: 82, unit: "cm" } }],
      } as unknown as WireMessage,
      {
        type: "tool",
        id: "t1",
        tool_call_id: "tc-waist",
        content: JSON.stringify({
          turn_scope_id: "scope-1",
          block_id: "trend:waist",
          data: { label: "Waist", value: "82", unit: "cm", points: [84, 82] },
          text: "Waist logged.",
        }),
      },
      { type: "ai", id: "a2", content: "Logged your waist." },
    ];

    render(
      <MessageList turns={buildTurns(messages)} pendingInterrupt={null} upload={{ phase: "idle" }} busy={false} onApprove={() => {}} latestAiMessageId={null} />,
    );

    expect(screen.getByTestId("log-metric-card")).toHaveTextContent("Waist");
    expect(screen.getByText("Logged your waist.")).toBeInTheDocument();
    expect(screen.queryByTestId("tool-call-card")).toBeNull();
    expect(screen.queryByText("waist", { exact: true })).toBeNull();
  });

  it("does not synthesize raw cards from WireMessage tool_calls", () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "hello" },
      {
        type: "ai",
        id: "a1",
        content: "",
        tool_calls: [{ id: "tc-fallback", name: "query_metformin", args: { q: "dose" } }],
      } as unknown as WireMessage,
    ];
    const turns = buildTurns(messages);
    render(<MessageList turns={turns} pendingInterrupt={null} upload={{ phase: "idle" }} busy={false} onApprove={() => {}} latestAiMessageId={null} />);
    expect(screen.queryByTestId("tool-call-card")).toBeNull();
    expect(screen.queryByText("Running")).toBeNull();
  });

  it("keeps pending and successful raw tool states hidden", () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "lookup" },
      {
        type: "ai",
        id: "a1",
        content: "",
        tool_calls: [{ id: "tc-live", name: "medical_lookup", args: { question: "interactions" } }],
      } as unknown as WireMessage,
    ];
    const turns = buildTurns(messages);
    const pending: ToolCallView[] = [makeCall({ id: "tc-live", name: "medical_lookup", args: { question: "interactions" }, status: "running" })];
    const { rerender } = render(
      <MessageList turns={turns} pendingInterrupt={null} upload={{ phase: "idle" }} busy={false} onApprove={() => {}} latestAiMessageId={null} toolCalls={pending} />,
    );
    expect(screen.queryByTestId("tool-call-pending")).toBeNull();

    const success: ToolCallView[] = [makeCall({ id: "tc-live", name: "medical_lookup", args: { question: "interactions" }, status: "finished", output: "No major interactions found." })];
    rerender(<MessageList turns={turns} pendingInterrupt={null} upload={{ phase: "idle" }} busy={false} onApprove={() => {}} latestAiMessageId={null} toolCalls={success} />);
    expect(screen.queryByTestId("tool-call-result")).toBeNull();
    expect(screen.queryByTestId("tool-call-pending")).toBeNull();
  });

  it("keeps pending and failed raw tool states hidden", () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "copy it" },
      {
        type: "ai",
        id: "a1",
        content: "",
        tool_calls: [{ id: "tc-err", name: "copy_to_clipboard", args: { text: "secret text" } }],
      } as unknown as WireMessage,
    ];
    const turns = buildTurns(messages);
    const pending: ToolCallView[] = [makeCall({ id: "tc-err", name: "copy_to_clipboard", args: { text: "secret text" }, status: "running" })];
    const { rerender } = render(
      <MessageList turns={turns} pendingInterrupt={null} upload={{ phase: "idle" }} busy={false} onApprove={() => {}} latestAiMessageId={null} toolCalls={pending} />,
    );
    expect(screen.queryByText("Running")).toBeNull();
    const failed: ToolCallView[] = [makeCall({ id: "tc-err", name: "copy_to_clipboard", args: { text: "secret text" }, status: "error", error: "Clipboard unavailable" })];
    rerender(<MessageList turns={turns} pendingInterrupt={null} upload={{ phase: "idle" }} busy={false} onApprove={() => {}} latestAiMessageId={null} toolCalls={failed} />);
    expect(screen.queryByTestId("tool-call-error")).toBeNull();
    expect(screen.queryByText("Clipboard unavailable")).toBeNull();
  });

  it("does not render raw tool envelope JSON as bubble text", () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "show trend" },
      {
        type: "ai",
        id: "a1",
        content: "",
        tool_calls: [{ id: "tc-ui", name: "compose_ui", args: { tree: [{ component: "Card", props: { text: "hi" } }] } }],
      } as unknown as WireMessage,
      { type: "tool", id: "t1", tool_call_id: "tc-ui", content: JSON.stringify({ turn_scope_id: "s", block_id: "trend:weight", data: { value: "1" }, text: "" }) },
    ];
    const turns = buildTurns(messages);
    const toolCalls: ToolCallView[] = [makeCall({ id: "tc-ui", name: "compose_ui", args: { tree: [] }, status: "finished", output: "ok" })];
    const { container } = render(
      <MessageList turns={turns} pendingInterrupt={null} upload={{ phase: "idle" }} busy={false} onApprove={() => {}} latestAiMessageId={null} toolCalls={toolCalls} />,
    );
    expect(container.textContent ?? "").not.toContain('"turn_scope_id"');
    expect(container.textContent ?? "").not.toContain('"component"');
    expect(screen.queryByTestId("tool-call-card")).toBeNull();
  });
});
