import { vi } from "vitest";
import { useRef, useState } from "react";
import { Client } from "@langchain/langgraph-sdk";
import type {
  CoachApiBundle,
  CoachStreamDeps,
  CoachStreamHandle,
  CoachStreamOptions,
  CoachSubmitOptions,
  CoachUseStream,
} from "@/chat/useCoachStream";
import type { RunStreamPart } from "@/chat/coachApi";
import type { ThreadSummary } from "@/chat/coachApi";
import type { ResumePayload, RunInput } from "@/chat/coachProtocol";
import { applyStreamPart } from "@/chat/stream";
import type { WireMessage } from "@/chat/model";

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

export type StreamCall = {
  readonly threadId: string;
  readonly payload: { input: RunInput } | { command: { resume: ResumePayload } };
  readonly options?: CoachSubmitOptions;
};

export interface ScriptedCoachStream {
  readonly calls: StreamCall[];
  readonly client: Client;
  readonly useStream: CoachUseStream;
}

export function fakeStream(
  responses: (call: StreamCall, callIndex: number) => Iterable<RunStreamPart> | AsyncIterable<RunStreamPart>,
): ScriptedCoachStream {
  const calls: StreamCall[] = [];
  const client = new Client({ apiUrl: "http://coach.test" });
  const useStream = (options: CoachStreamOptions): CoachStreamHandle => {
    const [messages, setMessages] = useState<WireMessage[]>([]);
    const [interrupts, setInterrupts] = useState<unknown[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<unknown>(undefined);
    const messagesRef = useRef<WireMessage[]>([]);

    const run = async (call: StreamCall): Promise<void> => {
      const callIndex = calls.length;
      calls.push(call);
      setIsLoading(true);
      setError(undefined);
      try {
        for await (const part of responses(call, callIndex)) {
          const next = applyStreamPart(messagesRef.current, part);
          messagesRef.current = next.messages;
          setMessages(next.messages);
          if (next.interruptValue !== null) setInterrupts([{ value: next.interruptValue }]);
        }
      } catch (streamError) {
        setError(streamError);
        throw streamError;
      } finally {
        setIsLoading(false);
      }
    };

    return {
      values: { messages },
      messages,
      toolCalls: [] as never[],
      interrupts,
      interrupt: interrupts[0],
      isLoading,
      isThreadLoading: false,
      error,
      threadId: options.threadId,
      submit: (input, submitOptions) =>
        run({ threadId: submitOptions.threadId, payload: { input }, options: submitOptions }),
      respond: (response) => {
        const threadId = options.threadId;
        if (threadId === null) return Promise.resolve();
        setInterrupts([]);
        return run({ threadId, payload: { command: { resume: response } } });
      },
      stop: async () => setIsLoading(false),
      getThread: () => ({ threadId: options.threadId }),
    };
  };
  return {
    calls,
    client,
    useStream,
  };
}

export function emptyStream(): ScriptedCoachStream {
  return fakeStream(() => []);
}

export function fakeDeps(
  api: Partial<CoachApiBundle> = {},
  stream: ScriptedCoachStream = emptyStream(),
): CoachStreamDeps & { readonly api: CoachApiBundle } {
  return {
    api: fakeApi(api),
    client: stream.client,
    useStream: stream.useStream,
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
