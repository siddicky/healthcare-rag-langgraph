import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { createElement } from "react";
import { render } from "@testing-library/react";
import type { z } from "zod";
import { copyToClipboardExecute } from "@/chat/useCoachStream";

/**
 * Tests for the useFrontendTool clipboard registration (plan todo 9).
 * The hook config is captured via module mock (same pattern as
 * renderers-medical.test.tsx); the handler under test is the REAL ported
 * `copyToClipboardExecute` — immediate copy, fallback chain, fail-closed
 * error strings that never echo the copied text.
 */

interface CapturedTool {
  name: string;
  description: string | undefined;
  parameters: z.ZodType<{ text: string }>;
  handler: (args: { text: string }) => Promise<string>;
  agentId: string | undefined;
}

let captured: CapturedTool[];

beforeEach(async () => {
  captured = [];
  vi.resetModules();
  vi.doMock("@copilotkit/react-core/v2/headless", () => ({
    useFrontendTool: (config: CapturedTool) => {
      captured.push(config);
    },
  }));
  const mod = await import("@/chat/renderers/clipboard");
  const { registerClipboardFrontendTool } = mod;
  render(createElement(registerClipboardFrontendTool));
});

afterEach(() => {
  vi.doUnmock("@copilotkit/react-core/v2/headless");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("registerClipboardFrontendTool wiring", () => {
  it("registers exactly one tool named copy_to_clipboard for the coach agent", () => {
    expect(captured).toHaveLength(1);
    expect(captured[0]!.name).toBe("copy_to_clipboard");
    expect(captured[0]!.agentId).toBe("coach");
  });

  it("parameters is a zod object schema requiring a string text arg", () => {
    const schema = captured[0]!.parameters;
    expect(schema.safeParse({ text: "hello" }).success).toBe(true);
    expect(schema.safeParse({}).success).toBe(false);
    expect(schema.safeParse({ text: 42 }).success).toBe(false);
    expect(schema.safeParse({ text: "x", extra: 1 })).toEqual({
      success: true,
      data: { text: "x" },
    });
  });
});

describe("handler = copyToClipboardExecute (ported semantics)", () => {
  it("copies immediately via navigator.clipboard and returns 'copied'", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const result = await captured[0]!.handler({ text: "hello world" });
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith("hello world");
    expect(result).toBe("copied");
  });

  it("falls back to the hidden-textarea execCommand path when navigator.clipboard rejects", async () => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    const execCommand = vi.fn().mockReturnValue(true);
    document.execCommand = execCommand as typeof document.execCommand;
    const result = await captured[0]!.handler({ text: "fallback text" });
    expect(execCommand).toHaveBeenCalledWith("copy");
    expect(result).toBe("copied");
  });

  it("fails closed with a stable error string that never echoes the copied text", async () => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    document.execCommand = vi.fn().mockReturnValue(false) as typeof document.execCommand;
    await expect(captured[0]!.handler({ text: "SECRET-PHI-CANARY" })).rejects.toThrow(
      "Clipboard unavailable",
    );
    try {
      await captured[0]!.handler({ text: "SECRET-PHI-CANARY" });
      expect.unreachable("handler should have thrown");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      expect(message).not.toContain("SECRET-PHI-CANARY");
    }
  });

  it("is the exact same function exported by useCoachStream — no divergent copy", async () => {
    // Dynamic import: same module instance the mocked registration captured.
    const { copyToClipboardExecute: ported } = await import("@/chat/useCoachStream");
    expect(captured[0]!.handler).toBe(ported);
  });
});
