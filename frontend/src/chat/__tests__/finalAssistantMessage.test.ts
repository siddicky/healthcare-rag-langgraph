import { describe, expect, it } from "vitest";
import { selectFinalAssistantMessage } from "@/chat/finalAssistantMessage";
import type { WireMessage } from "@/chat/model";

describe("selectFinalAssistantMessage", () => {
  it("selects the newest visible assistant text", () => {
    const earlier: WireMessage = { type: "ai", id: "a1", content: "Earlier answer" };
    const newest: WireMessage = { type: "ai", id: "a2", content: "Newest answer" };

    expect(selectFinalAssistantMessage([earlier, newest])).toBe(newest);
  });

  it("skips a trailing empty tool placeholder", () => {
    const visible: WireMessage = { type: "ai", id: "a1", content: "Visible answer" };
    const placeholder: WireMessage = {
      type: "ai",
      id: "a2",
      content: "",
      tool_calls: [{ id: "tc-1", name: "view_schedule", args: { month: "2026-08" } }],
    };

    expect(selectFinalAssistantMessage([visible, placeholder])).toBe(visible);
  });

  it("selects reasoning-only assistant output", () => {
    const reasoning: WireMessage = {
      type: "ai",
      id: "a1",
      content: "",
      reasoning: "A concise explanation",
    };

    expect(selectFinalAssistantMessage([reasoning])).toBe(reasoning);
  });

  it("selects a memory confirmation card", () => {
    const confirmation: WireMessage = {
      type: "ai",
      id: "a1",
      content: JSON.stringify({
        component: "MemoryExtractionCard",
        data: {
          sourceLabel: "intake.pdf",
          fields: [
            {
              key: "medication",
              label: "Medication",
              value: "Lipitor",
              status: "saved",
            },
          ],
        },
      }),
    };

    expect(selectFinalAssistantMessage([confirmation])).toBe(confirmation);
  });

  it("falls back to the newest AI placeholder", () => {
    const earlier: WireMessage = { type: "ai", id: "a1", content: "" };
    const newest: WireMessage = { type: "ai", id: "a2", content: "" };

    expect(selectFinalAssistantMessage([earlier, newest])).toBe(newest);
  });

  it("returns undefined when there is no AI message", () => {
    const messages: WireMessage[] = [
      { type: "human", id: "h1", content: "Hello" },
      { type: "tool", id: "t1", tool_call_id: "tc-1", content: "Done" },
    ];

    expect(selectFinalAssistantMessage(messages)).toBeUndefined();
  });
});
