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
  readonly respondOptions?: { readonly interruptId?: string; readonly namespace?: readonly string[] };
};

export interface ScriptedCoachStream {
  readonly calls: StreamCall[];
  readonly client: Client;
  readonly useStream: CoachUseStream;
  readonly agent: {
    messages: WireMessage[];
    isRunning: boolean;
    run(call: StreamCall): Promise<void>;
    subscribe(subscriber: ScriptedAgentSubscriber): { unsubscribe(): void };
  };
  emitThreadId(threadId: string): void;
}

interface ScriptedAgentSubscriber {
  onMessagesChanged?(): void;
  onRunStarted?(): void;
  onRunFinalized?(): void;
  onInterrupt?(entries: unknown[]): void;
  onError?(error: unknown): void;
}

export function fakeAgent(
  responses: (call: StreamCall, callIndex: number) => Iterable<RunStreamPart> | AsyncIterable<RunStreamPart>,
): ScriptedCoachStream {
  const sdkThreadId = "33333333-3333-4333-8333-333333333333";
  const calls: StreamCall[] = [];
  const stopCalls: Array<{ cancel?: boolean }> = [];
  let disconnectCalls = 0;
  let serverRunAlive = false;
  let onThreadId: CoachStreamOptions["onThreadId"] = () => undefined;
  const subscribers = new Set<ScriptedAgentSubscriber>();
  let executeRun: (call: StreamCall) => Promise<void> = async () => undefined;
  const agent = {
    messages: [] as WireMessage[],
    isRunning: false,
    run: (call: StreamCall) => executeRun(call),
    subscribe: (subscriber: ScriptedAgentSubscriber) => {
      subscribers.add(subscriber);
      return { unsubscribe: () => subscribers.delete(subscriber) };
    },
  };
  const client = new Client({ apiUrl: "http://coach.test" });
  const useStream = (options: CoachStreamOptions): CoachStreamHandle => {
    onThreadId = options.onThreadId;
    const [messages, setMessages] = useState<WireMessage[]>([]);
    const [interrupts, setInterrupts] = useState<unknown[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [isThreadLoading, setIsThreadLoading] = useState(false);
    const [error, setError] = useState<unknown>(undefined);
    const messagesRef = useRef<WireMessage[]>([]);

    const run = async (call: StreamCall): Promise<void> => {
      const callIndex = calls.length;
      calls.push(call);
      serverRunAlive = true;
      const resumable = (call.options as unknown as Record<string, unknown>)?.streamResumable === true;
      if (resumable) serverRunAlive = true;
      setIsLoading(true);
      agent.isRunning = true;
      for (const subscriber of subscribers) subscriber.onRunStarted?.();
      setError(undefined);
      try {
        for await (const part of responses(call, callIndex)) {
          const next = applyStreamPart(messagesRef.current, part);
          if (part.event !== "__interrupt__") {
            messagesRef.current = next.messages;
            agent.messages = next.messages;
            setMessages(next.messages);
            for (const subscriber of subscribers) subscriber.onMessagesChanged?.();
          }
          if (next.interruptValue !== null) {
            const entries = [{ id: "interrupt-1", value: next.interruptValue }];
            setInterrupts(entries);
            for (const subscriber of subscribers) subscriber.onInterrupt?.(entries);
          }
        }
      } catch (streamError) {
        setError(streamError);
        for (const subscriber of subscribers) subscriber.onError?.(streamError);
        throw streamError;
      } finally {
        setIsLoading(false);
        agent.isRunning = false;
        serverRunAlive = false;
        for (const subscriber of subscribers) subscriber.onRunFinalized?.();
      }
    };
    executeRun = run;

    const stop = async (opts?: { cancel?: boolean }) => {
      stopCalls.push({ cancel: opts?.cancel });
      if (opts?.cancel === false) {
        setIsLoading(false);
        return;
      }
      serverRunAlive = false;
      setIsLoading(false);
    };

    const disconnect = async () => {
      disconnectCalls += 1;
      stopCalls.push({ cancel: false });
      setIsLoading(false);
    };

    return {
      values: { messages },
      messages,
      toolCalls: [] as never[],
      interrupts,
      interrupt: interrupts[0],
      isLoading,
      isThreadLoading,
      error,
      threadId: options.threadId,
      submit: (input: RunInput, submitOptions: CoachSubmitOptions) => {
        const threadId = options.threadId ?? submitOptions.threadId ?? sdkThreadId;
        if (options.threadId === null) options.onThreadId(threadId);
        return agent.run({ threadId, payload: { input }, options: submitOptions });
      },
      respond: (
        response: ResumePayload,
        respondOptions?: { readonly interruptId?: string; readonly namespace?: readonly string[] },
      ) => {
        const threadId = options.threadId;
        if (threadId === null) return Promise.resolve();
        setInterrupts([]);
        return agent.run({ threadId, payload: { command: { resume: response } }, respondOptions });
      },
      respondAll: (responses: ResumePayload[] | Record<string, unknown>) => {
        const threadId = options.threadId;
        if (threadId === null) return Promise.resolve();
        setInterrupts([]);
        const list: ResumePayload[] = Array.isArray(responses) ? (responses as ResumePayload[]) : Object.values(responses as Record<string, ResumePayload>);
        return (async () => {
          for (const r of list) {
            await agent.run({ threadId, payload: { command: { resume: r } } });
          }
        })();
      },
      stop: stop as unknown as CoachStreamHandle["stop"],
      disconnect: disconnect as unknown as CoachStreamHandle["disconnect"],
      getThread: () => {
        if (options.threadId !== null && serverRunAlive) {
          setIsThreadLoading(true);
          setTimeout(() => setIsThreadLoading(false), 0);
        }
        return { threadId: options.threadId } as unknown as ReturnType<CoachStreamHandle["getThread"]>;
      },
    } as unknown as CoachStreamHandle & { _stopCalls?: unknown; _disconnectCalls?: unknown };
  };
  const streamObj: ScriptedCoachStream & { stopCalls: typeof stopCalls; disconnectCalls: () => number; serverAlive: () => boolean } = {
    calls,
    client,
    useStream,
    agent,
    emitThreadId: (threadId: string) => onThreadId(threadId),
  } as unknown as ScriptedCoachStream & { stopCalls: typeof stopCalls; disconnectCalls: () => number; serverAlive: () => boolean };
  (streamObj as unknown as Record<string, unknown>).stopCalls = stopCalls;
  (streamObj as unknown as Record<string, unknown>).disconnectCalls = () => disconnectCalls;
  (streamObj as unknown as Record<string, unknown>).serverAlive = () => serverRunAlive;
  return streamObj;
}

export const fakeStream = fakeAgent;

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
