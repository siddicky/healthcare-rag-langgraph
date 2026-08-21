import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * Forbidden-modes guard: the chat streams UPDATES-ONLY through the single
 * fixed run envelope — no token/typewriter streaming, no other stream mode
 * anywhere in the shipped chat sources.
 */

const CHAT_SOURCES = [
  "src/chat/coachProtocol.ts",
  "src/chat/coachApi.ts",
  "src/chat/coachClient.ts",
  "src/chat/model.ts",
  "src/chat/stream.ts",
  "src/chat/erase.ts",
  "src/chat/uploadFlow.ts",
  "src/chat/titles.ts",
  "src/chat/useCoachChat.ts",
  "src/chat/components/ChatShell.tsx",
  "src/chat/components/Composer.tsx",
  "src/chat/components/MessageList.tsx",
  "src/chat/components/ActionBar.tsx",
  "src/chat/components/ThreadSidebar.tsx",
  "src/chat/components/InterruptPanel.tsx",
  "src/app/chat/page.tsx",
];

function source(path: string): string {
  return readFileSync(resolve(process.cwd(), path), "utf8");
}

const BANNED_STREAM_MODES = [
  '"messages-tuple"',
  '"values"',
  '"checkpoints"',
  '"debug"',
  '"custom"',
  '"events"',
  '"tasks"',
  "'messages-tuple'",
  "'values'",
  "'checkpoints'",
  "'debug'",
  "'custom'",
  "'events'",
  "'tasks'",
];

describe("forbidden stream modes and token streaming (source grep)", () => {
  it("declares streamMode ONLY as updates (type and value), nowhere else", () => {
    let declarations = 0;
    for (const path of CHAT_SOURCES) {
      const text = source(path);
      const occurrences = text.match(/streamMode\s*:\s*[^,;\n]+/g) ?? [];
      for (const occurrence of occurrences) {
        expect(occurrence.replace(/\s+/g, ""), `${path}: ${occurrence}`).toBe('streamMode:["updates"]');
        declarations += 1;
      }
    }
    expect(declarations).toBeGreaterThanOrEqual(1);
  });

  it("never mentions a disallowed stream mode anywhere in the chat sources", () => {
    for (const path of CHAT_SOURCES) {
      const text = source(path);
      for (const banned of BANNED_STREAM_MODES) {
        expect(text.includes(banned), `${path} must not contain ${banned}`).toBe(false);
      }
    }
  });

  it("contains no token/typewriter streaming machinery", () => {
    for (const path of CHAT_SOURCES) {
      const text = source(path);
      for (const banned of ["typewriter", "tokenStream", "textChunk", "contentChunk", "onToken"]) {
        expect(text.includes(banned), `${path} must not contain ${banned}`).toBe(false);
      }
      expect(text.match(/streamMode[^;\n]*"messages"/), path).toBeNull();
    }
  });

  it("touches only member routes (no cron/assistant/store endpoint calls)", () => {
    const bannedRoute = /^["'`]\/(crons|assistants|store|threads\/count|runs\/wait)/;
    for (const path of CHAT_SOURCES) {
      const text = source(path);
      const routeCalls = text.match(/["'`](\/[a-z][^"'`\n]*)["'`]/g) ?? [];
      for (const call of routeCalls) {
        expect(bannedRoute.test(call), `${path} must not call ${call}`).toBe(false);
      }
    }
  });
});
