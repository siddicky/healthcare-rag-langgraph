import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Markdown } from "@/components/generative-ui/Markdown";
import { MessageList } from "@/chat/components/MessageList";
import { buildTurns } from "@/chat/model";
import type { WireMessage } from "@/chat/model";

function uploadIdle() {
  return { phase: "idle" as const, info: { fileName: "", fileSizeLabel: "" }, detail: "", stage: "uploading" as const };
}

describe("Markdown messages", () => {
  it("renders headings, lists, code, tables, links with GFM", () => {
    const content =
      "# Hello\n\n- item\n\n```js\ncode\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n[link](https://example.com)";
    render(<Markdown content={content} />);
    expect(screen.getByRole("heading", { level: 1, name: "Hello" })).toBeInTheDocument();
    expect(screen.getByText("item")).toBeInTheDocument();
    // li is rendered; check list item text
    expect(screen.getByText("item").closest("li")).not.toBeNull();
    expect(screen.getByText("code")).toBeInTheDocument();
    const codeEl = screen.getByText("code");
    expect(codeEl.tagName.toLowerCase()).toBe("code");
    expect(screen.getByRole("table")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "link" });
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("falls back to plain text and escapes no-op markdown", () => {
    render(<Markdown content="plain hello" />);
    expect(screen.getByText("plain hello")).toBeInTheDocument();
  });

  it("AiBubble via MessageList renders markdown for streaming + complete WireMessage", async () => {
    const md = "# Hello\n- item\n```js\ncode\n```";
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "question" },
      { type: "ai", id: "a1", content: md },
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
    expect(screen.getByRole("heading", { level: 1, name: "Hello" })).toBeInTheDocument();
    expect(screen.getByText("item")).toBeInTheDocument();
    expect(screen.getByText("code")).toBeInTheDocument();
    // Ensure JSON envelope does NOT render as markdown (aiDisplayText gates it — AiBubble returns null)
    // Simulate a second render with an envelope AI message — it should not produce markdown
    // (covered by AiBubble null guard: component confirmation JSON never renders)
    const envelopeMessages: WireMessage[] = [
      { type: "human", id: "h2", content: "do" },
      { type: "ai", id: "a2", content: JSON.stringify({ component: "MemoryExtractionCard", data: { sourceLabel: "x", fields: [] } }) },
    ];
    const turns2 = buildTurns(envelopeMessages);
    const { container } = render(
      <MessageList
        turns={turns2}
        pendingInterrupt={null}
        upload={uploadIdle() as never}
        busy={false}
        onApprove={() => {}}
        latestAiMessageId={null}
      />,
    );
    // No assistant bubble rendered for pure JSON component card
    expect(container.querySelector(".bubble.assistant")).toBeNull();
  });

  it("re-renders incrementally for token-streaming deltas (messages mode)", () => {
    const partial = "# Hel";
    const full = "# Hello\n- item\n```js\ncode\n```";
    const { rerender } = render(<Markdown content={partial} />);
    expect(screen.getByRole("heading", { level: 1, name: "Hel" })).toBeInTheDocument();
    rerender(<Markdown content={full} />);
    expect(screen.getByRole("heading", { level: 1, name: "Hello" })).toBeInTheDocument();
    expect(screen.getByText("code")).toBeInTheDocument();
  });

  it("supports content as string via buildTurns envelope-preserving logic", () => {
    // Decomposed/merged turns still answer original query once — markdown renders that merged text
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "complex question" },
      { type: "ai", id: "a1", content: "## Subheading\n\nSome **bold** and *italic* text." },
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
    expect(screen.getByRole("heading", { level: 2, name: "Subheading" })).toBeInTheDocument();
    expect(screen.getByText("bold")).toBeInTheDocument();
    expect(screen.getByText("italic")).toBeInTheDocument();
  });
});
