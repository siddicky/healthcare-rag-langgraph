import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageList } from "@/chat/components/MessageList";
import { ReasoningBlock } from "@/chat/components/ReasoningBlock";
import { buildTurns, messageReasoning } from "@/chat/model";
import type { WireMessage } from "@/chat/model";

function uploadIdle() {
  return { phase: "idle" as const, info: { fileName: "", fileSizeLabel: "" }, detail: "", stage: "uploading" as const };
}

describe("messageReasoning", () => {
  it("reads additional_kwargs.reasoning", () => {
    const msg = { type: "ai", id: "a1", content: "answer", additional_kwargs: { reasoning: "thinking..." } } as unknown as WireMessage;
    expect(messageReasoning(msg)).toBe("thinking...");
  });

  it("reads additional_kwargs.reasoning_content", () => {
    const msg = { type: "ai", id: "a1", content: "answer", additional_kwargs: { reasoning_content: "thought 2" } } as unknown as WireMessage;
    expect(messageReasoning(msg)).toBe("thought 2");
  });

  it("reads top-level reasoning_content", () => {
    const msg = { type: "ai", id: "a1", content: "answer", reasoning_content: "top level" } as unknown as WireMessage;
    expect(messageReasoning(msg)).toBe("top level");
  });

  it("reads top-level reasoning", () => {
    const msg = { type: "ai", id: "a1", content: "answer", reasoning: "direct reasoning" } as unknown as WireMessage;
    expect(messageReasoning(msg)).toBe("direct reasoning");
  });

  it("reads content array blocks with type reasoning", () => {
    const msg = {
      type: "ai",
      id: "a1",
      content: [{ type: "reasoning", text: "block thinking" }, { type: "text", text: "hello" }],
    } as unknown as WireMessage;
    expect(messageReasoning(msg)).toBe("block thinking");
  });

  it("returns null when absent or empty", () => {
    const msg = { type: "ai", id: "a1", content: "answer" } as unknown as WireMessage;
    expect(messageReasoning(msg)).toBeNull();
    const empty = { type: "ai", id: "a1", content: "answer", additional_kwargs: { reasoning: "   " } } as unknown as WireMessage;
    expect(messageReasoning(empty)).toBeNull();
    const empty2 = { type: "ai", id: "a1", content: [{ type: "reasoning", text: "  " }] } as unknown as WireMessage;
    expect(messageReasoning(empty2)).toBeNull();
  });

  it("handles both string content and block array gracefully", () => {
    const strMsg = { type: "ai", id: "a1", content: "plain answer", additional_kwargs: { reasoning: "r" } } as unknown as WireMessage;
    expect(messageReasoning(strMsg)).toBe("r");
    const blockMsg = {
      type: "ai",
      id: "a1",
      content: [{ type: "reasoning", reasoning: "nested" }],
    } as unknown as WireMessage;
    expect(messageReasoning(blockMsg)).toBe("nested");
  });
});

describe("ReasoningBlock", () => {
  it("collapses by default, expands on toggle, shows content", () => {
    render(<ReasoningBlock reasoning="thinking trace here" />);
    expect(screen.getByTestId("reasoning-block")).toBeInTheDocument();
    expect(screen.getByTestId("reasoning-toggle")).toHaveTextContent(/Show reasoning/);
    expect(screen.queryByTestId("reasoning-content")).toBeNull();
    fireEvent.click(screen.getByTestId("reasoning-toggle"));
    expect(screen.getByTestId("reasoning-toggle")).toHaveTextContent(/Hide reasoning/);
    expect(screen.getByTestId("reasoning-content")).toBeInTheDocument();
    expect(screen.getByTestId("reasoning-content").textContent).toContain("thinking trace here");
    fireEvent.click(screen.getByTestId("reasoning-toggle"));
    expect(screen.queryByTestId("reasoning-content")).toBeNull();
  });

  it("returns null for empty reasoning (no empty block)", () => {
    const { container } = render(<ReasoningBlock reasoning="   " />);
    expect(container.firstChild).toBeNull();
  });
});

describe("MessageList reasoning wiring", () => {
  it("renders collapsible block when AI message has additional_kwargs.reasoning", async () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "question" },
      {
        type: "ai",
        id: "a1",
        content: "Final answer with markdown",
        additional_kwargs: { reasoning: "step 1: consider..." },
      } as unknown as WireMessage,
    ];
    const turns = buildTurns(messages);
    render(
      <MessageList
        turns={turns}
        pendingInterrupt={null}
        upload={uploadIdle() as never}
        busy={false}
        onApprove={() => {}}
        latestAiMessageId={null}
      />,
    );
    expect(screen.getByTestId("reasoning-block")).toBeInTheDocument();
    expect(screen.getByTestId("reasoning-toggle")).toBeInTheDocument();
    expect(screen.queryByTestId("reasoning-content")).toBeNull();
    fireEvent.click(screen.getByTestId("reasoning-toggle"));
    expect(screen.getByTestId("reasoning-content").textContent).toContain("step 1: consider...");
    expect(screen.getByText("Final answer with markdown")).toBeInTheDocument();
  });

  it("does not render block when reasoning absent", () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "question" },
      { type: "ai", id: "a1", content: "Answer without reasoning" },
    ];
    const turns = buildTurns(messages);
    render(
      <MessageList
        turns={turns}
        pendingInterrupt={null}
        upload={uploadIdle() as never}
        busy={false}
        onApprove={() => {}}
        latestAiMessageId={null}
      />,
    );
    expect(screen.queryByTestId("reasoning-block")).toBeNull();
    expect(screen.getByText("Answer without reasoning")).toBeInTheDocument();
  });

  it("renders reasoning block even when content array contains reasoning blocks", () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "question" },
      {
        type: "ai",
        id: "a1",
        content: [
          { type: "reasoning", text: "block reasoning" },
          { type: "text", text: "Final answer" },
        ],
      } as unknown as WireMessage,
    ];
    const turns = buildTurns(messages);
    render(
      <MessageList
        turns={turns}
        pendingInterrupt={null}
        upload={uploadIdle() as never}
        busy={false}
        onApprove={() => {}}
        latestAiMessageId={null}
      />,
    );
    expect(screen.getByTestId("reasoning-block")).toBeInTheDocument();
  });

  it("degenerates gracefully: empty reasoning string yields no block", () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "q" },
      { type: "ai", id: "a1", content: "answer", additional_kwargs: { reasoning: "   " } } as unknown as WireMessage,
    ];
    const turns = buildTurns(messages);
    render(
      <MessageList
        turns={turns}
        pendingInterrupt={null}
        upload={uploadIdle() as never}
        busy={false}
        onApprove={() => {}}
        latestAiMessageId={null}
      />,
    );
    expect(screen.queryByTestId("reasoning-block")).toBeNull();
  });

  it("reasoning block appears above markdown content in DOM order", () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "q" },
      { type: "ai", id: "a1", content: "the answer", additional_kwargs: { reasoning: "r" } } as unknown as WireMessage,
    ];
    const turns = buildTurns(messages);
    const { container } = render(
      <MessageList
        turns={turns}
        pendingInterrupt={null}
        upload={uploadIdle() as never}
        busy={false}
        onApprove={() => {}}
        latestAiMessageId={null}
      />,
    );
    const bubble = container.querySelector(".bubble.assistant");
    expect(bubble).not.toBeNull();
    const inner = bubble?.innerHTML ?? "";
    const reasoningIdx = inner.indexOf("reasoning-block");
    const markdownIdx = inner.indexOf("md-root");
    expect(reasoningIdx).toBeGreaterThan(-1);
    expect(markdownIdx).toBeGreaterThan(-1);
    expect(reasoningIdx).toBeLessThan(markdownIdx);
  });
});
