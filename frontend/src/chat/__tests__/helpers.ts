import { vi } from "vitest";
import type { CoachChatDeps, CoachApiBundle, CoachStreamBundle } from "@/chat/useCoachChat";
import type { RunStreamPart } from "@/chat/coachApi";
import type { ThreadSummary } from "@/chat/coachApi";
import type { ResumePayload, RunInput } from "@/chat/coachProtocol";

export function thread(id: string, overrides: Partial<ThreadSummary> = {}): ThreadSummary {
  return {
    thread_id: id,
    status: "idle",
    updated_at: `2026-08-2${id.length}T10:00:00Z`,
    ...overrides,
  };
}

export function fakeApi(overrides: Partial<CoachApiBundle> = {}): CoachApiBundle {
  return {
    createThread: vi.fn(async () => thread("11111111-1111-4111-8111-111111111111")),
    searchThreads: vi.fn(async () => [] as ThreadSummary[]),
    getThread: vi.fn(async (id: string) => thread(id)),
    deleteThread: vi.fn(async () => undefined),
    copyThread: vi.fn(async () => thread("22222222-2222-4222-8222-222222222222")),
    getThreadState: vi.fn(async () => ({ values: {}, interrupts: [] })),
    postUpload: vi.fn(async () => ({ stage: "done" })),
    getUploadStatus: vi.fn(async () => ({ stage: "done" })),
    postFeedback: vi.fn(async () => undefined),
    ...overrides,
  };
}

export type StreamCall = { threadId: string; payload: { input: RunInput } | { command: { resume: ResumePayload } } };

export function fakeStream(
  responses: (call: StreamCall, callIndex: number) => RunStreamPart[],
): CoachStreamBundle & { calls: StreamCall[] } {
  const calls: StreamCall[] = [];
  return {
    calls,
    streamRun: (threadId, payload) => {
      const callIndex = calls.length;
      calls.push({ threadId, payload });
      const parts = responses({ threadId, payload }, callIndex);
      return (async function* (): AsyncGenerator<RunStreamPart> {
        for (const part of parts) yield part;
      })();
    },
  };
}

export function emptyStream(): CoachStreamBundle & { calls: StreamCall[] } {
  return fakeStream(() => []);
}

export function fakeDeps(
  api: Partial<CoachApiBundle> = {},
  stream: CoachStreamBundle & { calls: StreamCall[] } = emptyStream(),
): CoachChatDeps & { api: CoachApiBundle; stream: CoachStreamBundle & { calls: StreamCall[] } } {
  return {
    api: fakeApi(api),
    stream,
    auth: { signOut: vi.fn(async () => undefined) },
    sleep: vi.fn(async () => undefined),
    newUploadId: () => "00000000-0000-4000-8000-000000000001",
    poll: { erase: { pollMs: 0, maxPolls: 5 }, upload: { pollMs: 0, maxPolls: 5 } },
  };
}

export function envelopeString(blockId: string, data: unknown, scope = "scope-1"): string {
  return JSON.stringify({ turn_scope_id: scope, block_id: blockId, data, text: "envelope" });
}

export function aiMessage(content: string, id: string, extra: Record<string, unknown> = {}) {
  return { type: "ai", id, content, ...extra };
}

export function humanMessage(content: string, id: string) {
  return { type: "human", id, content };
}

export function toolMessage(content: string, id: string, callId: string, status?: string) {
  return { type: "tool", id, content, tool_call_id: callId, ...(status !== undefined ? { status } : {}) };
}

export function updatesPart(node: string, messages: unknown[]): RunStreamPart {
  return { event: "updates", data: { [node]: { messages } } };
}

export function interruptPart(value: unknown): RunStreamPart {
  return { event: "__interrupt__", data: [{ value }] };
}
