"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useStream as useLangChainStream } from "@langchain/react";
import type { Client } from "@langchain/langgraph-sdk";
import { chatTelemetry } from "./stream";
import {
  CoachApiError,
  type ThreadStateProjection,
  type ThreadSummary,
} from "./coachApi";
import { classifyInterruptPayload } from "./model";
import {
  getRunStreamParams,
  SENTINEL_QUESTION,
  type RunStreamFixedParams,
  type RunStreamFixedParamsV2,
  type ResumePayload,
  type RunInput,
  type ThreadStatus,
} from "./coachProtocol";
import {
  buildTurns,
  containsEraseMarker,
  firstInterruptValue,
  isAiMessage,
  isHumanMessage,
  mergeMessages,
  regenerateEligibility,
  toWireMessages,
  type TurnModel,
  type WireMessage,
} from "./model";
import { runErasePhase2, type EraseOutcome } from "./erase";
import {
  applyUploadEvent,
  documentStage,
  formatFileSize,
  shouldPollStatus,
  type UploadUi,
} from "./uploadFlow";
import { clearThreadTitles, deriveTitle, getThreadTitle, setThreadTitle } from "./titles";
import { envelopesFromValues, treesFromValues } from "@/catalog/values";
import type { DataEnvelope } from "@/catalog/envelopes";

/**
 * The coach chat controller. Every network seam is injected through `deps`
 * so component tests drive the full UI from scripted fakes.
 */

export interface CoachApiBundle {
  createThread(): Promise<ThreadSummary>;
  searchThreads(options: { limit: number; offset: number; sortByIdAsc?: boolean }): Promise<ThreadSummary[]>;
  getThread(threadId: string): Promise<ThreadSummary>;
  deleteThread(threadId: string): Promise<void>;
  copyThread(threadId: string): Promise<ThreadSummary>;
  getThreadState(threadId: string): Promise<ThreadStateProjection>;
  getThreadHistory?(threadId: string): Promise<ThreadStateProjection[] | unknown[]>;
  postUpload(upload: { uploadId: string; threadId: string; file: File }): Promise<{ stage?: string }>;
  getUploadStatus(uploadId: string): Promise<{ stage?: string }>;
  postFeedback(feedback: { threadId: string; messageId: string; score: 1 | -1 }): Promise<void>;
}

export interface CoachStreamState {
  readonly messages?: unknown;
  readonly question?: string;
  readonly attachment_id?: string;
  /** Structured output via the values channel — typed but open for extension. */
  readonly todos?: unknown;
  readonly citations?: unknown;
  readonly metrics?: unknown;
  readonly [key: string]: unknown;
}

export type CoachSubmitOptions = (RunStreamFixedParams | RunStreamFixedParamsV2) & {
  readonly threadId: string;
  readonly onError?: (error: unknown) => void;
  readonly forkFrom?: string;
  readonly config?: { configurable?: { checkpoint_id?: string } };
};

function isForkV2Enabled(): boolean {
  return process.env.NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER === "v2";
}

export interface CoachStreamOptions {
  readonly client: Client;
  readonly assistantId: "coach";
  readonly threadId: string | null;
  readonly messagesKey: "messages";
  readonly optimistic: false;
  readonly onThreadId: (threadId: string) => void;
}

export interface ToolCallHandleView {
  readonly id: string;
  readonly callId?: string;
  readonly name: string;
  readonly args?: unknown;
  readonly input?: unknown;
  readonly output?: unknown;
  readonly result?: unknown;
  readonly status: string;
  readonly error?: string | null;
  readonly namespace?: readonly string[];
}

export interface CoachStreamHandle {
  readonly values: CoachStreamState;
  readonly messages: readonly unknown[];
  readonly toolCalls?: readonly ToolCallHandleView[];
  readonly interrupts: readonly unknown[];
  readonly interrupt: unknown;
  readonly isLoading: boolean;
  readonly isThreadLoading: boolean;
  readonly error: unknown;
  readonly threadId: string | null;
  submit(input: RunInput, options: CoachSubmitOptions): Promise<void>;
  respond(response: ResumePayload): Promise<void>;
  respondAll?(responses: ResumePayload[] | Record<string, ResumePayload>): Promise<void>;
  stop(): Promise<void>;
  getThread(): unknown;
}

export type CoachUseStream = (options: CoachStreamOptions) => CoachStreamHandle;

/** Headless clipboard tool — one safe client-side tool, fail-closed on unknown. */
const COPY_TOOL = {
  name: "copy_to_clipboard" as const,
  description: "Copy text to the member's clipboard (client-side)",
  schema: {
    type: "object" as const,
    properties: {
      text: { type: "string" as const, description: "Text to copy" },
    },
    required: ["text" as const],
    additionalProperties: false,
  },
};

async function copyToClipboardExecute(args: { text: string }): Promise<string> {
  const text = args.text;
  // Fail-closed on bad args without logging raw text
  if (typeof text !== "string") throw new Error("copy_to_clipboard: missing text");
  try {
    if (
      typeof navigator !== "undefined" &&
      navigator.clipboard &&
      typeof navigator.clipboard.writeText === "function"
    ) {
      await navigator.clipboard.writeText(text);
      return "copied";
    }
  } catch {
    // fall through to legacy fallback
  }
  // Fallback for test/jsdom or legacy browsers: hidden textarea + execCommand
  try {
    if (typeof document !== "undefined") {
      const el = document.createElement("textarea");
      el.value = text;
      el.setAttribute("readonly", "");
      el.style.position = "fixed";
      el.style.opacity = "0";
      document.body.appendChild(el);
      el.select();
      const ok = document.execCommand?.("copy");
      document.body.removeChild(el);
      if (ok) return "copied";
    }
  } catch {
    // fall through to error
  }
  throw new Error("Clipboard unavailable");
}

const HEADLESS_TOOLS = [
  {
    tool: COPY_TOOL,
    execute: copyToClipboardExecute,
  },
] as const;

// ---------------------------------------------------------------------------
// HITL helpers — singular + array fallback + headless filtering + fail-closed
// ---------------------------------------------------------------------------

function isHeadlessInterruptValue(value: unknown): boolean {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  // SDK headless shape: {type:"tool", tool_call:{name:"copy_to_clipboard",...}}  or {type:"tool", toolCall:...}
  if (v.type === "tool") {
    const tc = (v as Record<string, unknown>).tool_call ?? (v as Record<string, unknown>).toolCall;
    if (typeof tc === "object" && tc !== null && (tc as Record<string, unknown>).name === "copy_to_clipboard") {
      return true;
    }
  }
  return false;
}

function filterOutHeadlessToolInterrupts(local: readonly unknown[]): readonly unknown[] {
  return local.filter((entry) => {
    const value = (() => {
      if (typeof entry !== "object" || entry === null) return null;
      if ("value" in (entry as Record<string, unknown>)) return (entry as Record<string, unknown>).value;
      return entry;
    })();
    return !isHeadlessInterruptValue(value);
  });
}

function allRawInterrupts(stream: CoachStreamHandle): readonly unknown[] {
  const singular = (stream as unknown as { interrupt?: unknown }).interrupt;
  const arr = Array.isArray(stream.interrupts) ? (stream.interrupts as readonly unknown[]) : [];
  if (arr.length > 0) return arr;
  if (singular !== null && singular !== undefined) return [singular];
  return [];
}

function visibleInterruptEntries(stream: CoachStreamHandle): readonly unknown[] {
  return filterOutHeadlessToolInterrupts(allRawInterrupts(stream));
}

function visibleInterruptValues(stream: CoachStreamHandle): unknown[] {
  const entries = visibleInterruptEntries(stream);
  return entries.map((entry) => firstInterruptValue([entry] as unknown) ?? entry).filter((v) => v !== null) as unknown[];
}

function isValidResumePayload(value: unknown): value is ResumePayload {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (typeof v.accept !== "boolean") return false;
  if (v.fields !== undefined) {
    if (!Array.isArray(v.fields)) return false;
    for (const f of v.fields as unknown[]) {
      if (typeof f !== "object" || f === null) return false;
      const ff = f as Record<string, unknown>;
      if (typeof ff.key !== "string" || typeof ff.value !== "string") return false;
    }
  }
  return true;
}

export function useLangChainCoachStream(options: CoachStreamOptions): CoachStreamHandle {
  // Cast through unknown: HeadlessTool tuple typing is stricter than the
  // runtime accepts for CoachStreamState; the wire shape is checked below.
  return useLangChainStream<CoachStreamState>({
    client: options.client,
    assistantId: options.assistantId,
    threadId: options.threadId,
    messagesKey: options.messagesKey,
    optimistic: options.optimistic,
    onThreadId: options.onThreadId,
    tools: HEADLESS_TOOLS as unknown as Parameters<typeof useLangChainStream>[0]["tools"],
    onTool: (event) => {
      const detail =
        event.phase === "start"
          ? `${event.name} start len=${String((event.args as { text?: string } | undefined)?.text?.length ?? 0)}`
          : event.phase === "success"
            ? `${event.name} success`
            : `${event.name} error: ${event.error?.message ?? "unknown"}`;
      if (event.phase === "error" && (event.error?.message ?? "").includes("is not registered")) {
        chatTelemetry({ kind: "unknown_interrupt", detail });
        return;
      }
      if (event.phase === "error") {
        chatTelemetry({ kind: "unknown_interrupt", detail });
        return;
      }
      if (process.env.NODE_ENV !== "test") {
        chatTelemetry({ kind: "unknown_interrupt", detail: `[headless] ${detail}` });
      }
    },
  }) as unknown as CoachStreamHandle;
}

// Re-export for tests / external wiring
export { COPY_TOOL, copyToClipboardExecute, HEADLESS_TOOLS };

export interface CoachSignOut {
  signOut(): Promise<unknown>;
}

export interface CoachStreamDeps {
  api: CoachApiBundle;
  client: Client;
  useStream: CoachUseStream;
  auth: CoachSignOut;
  sleep(ms: number): Promise<void>;
  newUploadId(): string;
  poll: { erase: { pollMs: number; maxPolls: number }; upload: { pollMs: number; maxPolls: number } };
}

export type CoachChatDeps = CoachStreamDeps;

export type EraseUi = { status: "idle" } | { status: "running"; note: string } | { status: "done" };

export interface FeedbackUi {
  sent: Record<string, 1 | -1>;
  failed: Record<string, true>;
}

const SIDEBAR_PAGE_SIZE = 50;
const MISSING_THREAD_MESSAGE = "That conversation is no longer available. Start a new one.";

function isMissingThreadError(error: unknown): boolean {
  if (error instanceof CoachApiError) {
    if (error.status !== 404) return false;
    if (error.message.includes("Checkpoint not found")) return false;
    return true;
  }
  if (error instanceof Error && "status" in error && (error as { status: number }).status === 404) {
    if (error.message.includes("Checkpoint not found")) return false;
    return true;
  }
  return false;
}

export function useCoachStream(deps: CoachStreamDeps) {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<WireMessage[]>([]);
  const [pendingInterrupt, setPendingInterrupt] = useState<unknown | null>(null);
  const [pendingInterrupts, setPendingInterrupts] = useState<unknown[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [upload, setUpload] = useState<UploadUi>({ phase: "idle" });
  const [erase, setErase] = useState<EraseUi>({ status: "idle" });
  const [feedback, setFeedback] = useState<FeedbackUi>({ sent: {}, failed: {} });
  const [initializing, setInitializing] = useState(true);

  const messagesRef = useRef<WireMessage[]>([]);
  const busyRef = useRef(false);
  const activeThreadRef = useRef<string | null>(null);
  const eraseStartedRef = useRef(false);
  const uploadRef = useRef<UploadUi>({ phase: "idle" });
  const pendingInterruptRef = useRef<unknown | null>(null);
  const pendingInterruptsRef = useRef<unknown[]>([]);
  const mountedRef = useRef(true);

  const stream = deps.useStream({
    client: deps.client,
    assistantId: "coach",
    threadId: activeThreadId,
    messagesKey: "messages",
    optimistic: false,
    onThreadId: (threadId) => {
      activeThreadRef.current = threadId;
      setActiveThreadId(threadId);
    },
  });

  messagesRef.current = messages;
  activeThreadRef.current = activeThreadId;
  uploadRef.current = upload;
  pendingInterruptRef.current = pendingInterrupt;
  pendingInterruptsRef.current = pendingInterrupts;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const commitMessages = useCallback((next: WireMessage[]) => {
    messagesRef.current = next;
    if (mountedRef.current) setMessages(next);
  }, []);

  const commitPendingInterrupt = useCallback((value: unknown | null): void => {
    pendingInterruptRef.current = value;
    if (mountedRef.current) setPendingInterrupt(value);
  }, []);

  const commitPendingInterrupts = useCallback((values: unknown[]): void => {
    pendingInterruptsRef.current = values;
    if (mountedRef.current) setPendingInterrupts(values);
    const first = values.length > 0 ? values[0] ?? null : null;
    pendingInterruptRef.current = first;
    if (mountedRef.current) setPendingInterrupt(first);
  }, []);

  useEffect(() => {
    const projected = mergeMessages(
      toWireMessages(stream.values.messages),
      toWireMessages(stream.messages),
    ).filter((message) => !isHumanMessage(message));
    if (projected.length > 0) {
      commitMessages(mergeMessages(messagesRef.current, projected));
    }
    const interrupt = (stream as unknown as { interrupt?: unknown }).interrupt ?? stream.interrupts?.[0] ?? null;
    const values = visibleInterruptValues(stream);
    if (values.length > 0) {
      const known = values.filter((v) => classifyInterruptPayload(v).kind !== "unknown");
      if (known.length > 0) {
        commitPendingInterrupts(known);
      } else {
        chatTelemetry({ kind: "unknown_interrupt" });
        commitPendingInterrupts([]);
      }
      return;
    }
    if (interrupt !== null) {
      const rawValue = firstInterruptValue([interrupt] as unknown) ?? interrupt;
      const unwrapped = typeof rawValue === "object" && rawValue !== null && "value" in (rawValue as Record<string, unknown>)
        ? (rawValue as Record<string, unknown>).value
        : rawValue;
      if (isHeadlessInterruptValue(unwrapped)) return;
      const kind = classifyInterruptPayload(unwrapped);
      if (kind.kind === "unknown") {
        chatTelemetry({ kind: "unknown_interrupt" });
        return;
      }
      commitPendingInterrupt(unwrapped);
      commitPendingInterrupts([unwrapped]);
    }
  }, [commitMessages, commitPendingInterrupt, commitPendingInterrupts, stream.interrupt, stream.interrupts, stream.messages, stream.values.messages]);

  const synthesizedFromMessages = useMemo(() => synthesizeToolCallsFromMessages(messages), [messages]);
  const toolCalls: readonly ToolCallHandleView[] = useMemo(() => {
    const live = (stream.toolCalls ?? []) as readonly ToolCallHandleView[];
    if (live.length > 0) return live;
    return synthesizedFromMessages;
  }, [stream.toolCalls, synthesizedFromMessages]);

  const catalogValues = useMemo(() => (stream.values ?? {}) as CoachStreamState, [stream.values]);
  const valuesEnvelopes: readonly DataEnvelope[] = useMemo(
    () => envelopesFromValues(catalogValues as Record<string, unknown>),
    [catalogValues],
  );
  const valuesTrees: readonly unknown[] = useMemo(
    () => treesFromValues(catalogValues as Record<string, unknown>),
    [catalogValues],
  );
  const turns: TurnModel[] = useMemo(() => buildTurns(messages), [messages]);

  const threadTitle = useCallback(
    (threadId: string): string => getThreadTitle(threadId) ?? "Conversation",
    [],
  );

  const refreshThreads = useCallback(async (): Promise<ThreadSummary[]> => {
    const collected: ThreadSummary[] = [];
    let offset = 0;
    for (;;) {
      const page = await deps.api.searchThreads({ limit: SIDEBAR_PAGE_SIZE, offset });
      collected.push(...page);
      if (page.length < SIDEBAR_PAGE_SIZE) break;
      offset += page.length;
    }
    if (mountedRef.current) setThreads(collected);
    return collected;
  }, [deps.api]);

  const recoverMissingThread = useCallback(
    async (threadId: string): Promise<void> => {
      clearThreadTitles([threadId]);
      if (activeThreadRef.current === threadId) {
        activeThreadRef.current = null;
        setActiveThreadId(null);
        commitMessages([]);
        commitPendingInterrupts([]);
        setUpload({ phase: "idle" });
      }
      await refreshThreads();
      if (mountedRef.current) setError(MISSING_THREAD_MESSAGE);
    },
    [commitMessages, commitPendingInterrupts, refreshThreads],
  );

  useEffect(() => {
    if (stream.error === undefined || stream.error === null) return;
    if (isMissingThreadError(stream.error)) {
      const threadId = activeThreadRef.current;
      if (threadId !== null) void recoverMissingThread(threadId);
      return;
    }
    setError(
      stream.error instanceof Error
        ? stream.error.message
        : "That message didn't go through. Please try again.",
    );
  }, [recoverMissingThread, stream.error]);

  const startErasePhase2 = useCallback(
    async (threadId: string): Promise<void> => {
      if (eraseStartedRef.current) return;
      eraseStartedRef.current = true;
      setErase({ status: "running", note: "Erasing your saved data…" });
      const seenIds: string[] = [];
      const pageIds = async (limit: number, offset: number): Promise<string[]> => {
        const result = await deps.api.searchThreads({ limit, offset, sortByIdAsc: true });
        const ids = result.map((thread) => thread.thread_id);
        seenIds.push(...ids);
        return ids;
      };
      let outcome: EraseOutcome;
      try {
        outcome = await runErasePhase2(
          {
            getThreadStatus: (id) =>
              deps.api.getThread(id).then((thread) => thread.status as ThreadStatus),
            searchPage: pageIds,
            deleteThread: (id) => deps.api.deleteThread(id),
            sleep: deps.sleep,
          },
          threadId,
          deps.poll.erase,
        );
      } catch (error) {
        outcome = {
          phase: "failed",
          note: error instanceof Error ? error.message : "Erasure failed.",
          remaining: [],
        };
      }
      if (outcome.phase === "done") {
        clearThreadTitles([...new Set(seenIds)]);
        await refreshThreads();
        if (mountedRef.current) {
          setActiveThreadId(null);
          commitMessages([]);
          commitPendingInterrupts([]);
          setUpload({ phase: "idle" });
          setErase({ status: "done" });
          setError(null);
        }
        eraseStartedRef.current = false;
        return;
      }
      eraseStartedRef.current = false;
      if (mountedRef.current) {
        setErase({ status: "idle" });
        setError(
          outcome.phase === "waiting"
            ? "Your erase request is still processing. It will finish when you return."
            : `Erasure paused: ${outcome.note ?? "a thread could not be deleted"}. Reconnect to retry.`,
        );
      }
    },
    [commitMessages, commitPendingInterrupts, deps.api, deps.poll.erase, deps.sleep, refreshThreads],
  );

  const maybeStartErase = useCallback(
    (current: readonly WireMessage[], threadId: string | null): void => {
      if (threadId === null) return;
      if (!containsEraseMarker(current)) return;
      void startErasePhase2(threadId);
    },
    [startErasePhase2],
  );

  useEffect(() => {
    maybeStartErase(messages, activeThreadId);
  }, [activeThreadId, maybeStartErase, messages]);

  const loadThreadState = useCallback(
    async (threadId: string): Promise<void> => {
      const projected = await deps.api.getThreadState(threadId);
      const wire = toWireMessages(projected.values.messages);
      commitMessages(wire);
      const raw = Array.isArray(projected.interrupts) ? projected.interrupts : [];
      const filteredEntries = filterOutHeadlessToolInterrupts(raw as readonly unknown[]);
      const values = (filteredEntries as unknown[]).map((e) => firstInterruptValue([e] as unknown) ?? e).filter((v) => v !== null) as unknown[];
      const known = values.filter((v) => classifyInterruptPayload(v).kind !== "unknown");
      if (values.length !== known.length && values.length > 0) chatTelemetry({ kind: "unknown_interrupt" });
      if (known.length > 0) commitPendingInterrupts(known);
      else commitPendingInterrupts([]);
      setActiveThreadId(threadId);
      maybeStartErase(wire, threadId);
    },
    [commitMessages, commitPendingInterrupts, deps.api, maybeStartErase],
  );

  const waitForTerminal = useCallback(
    async (threadId: string): Promise<boolean> => {
      for (let attempt = 0; attempt < deps.poll.erase.maxPolls; attempt += 1) {
        if (attempt > 0) await deps.sleep(deps.poll.erase.pollMs);
        const thread = await deps.api.getThread(threadId);
        if (thread.status !== "busy") return true;
      }
      return false;
    },
    [deps.api, deps.poll.erase, deps.sleep],
  );

  const runStream = useCallback(
    async (
      threadId: string,
      payload: { input: RunInput } | { command: { resume: ResumePayload } },
    ): Promise<boolean> => {
      if (busyRef.current) return false;
      busyRef.current = true;
      setBusy(true);
      setError(null);
      try {
        if (!(await waitForTerminal(threadId))) {
          if (mountedRef.current) {
            setError("Your previous request is still finishing. Please try again.");
          }
          return false;
        }
        if ("input" in payload) {
          await stream.submit(payload.input, {
            ...getRunStreamParams(),
            threadId,
            onError: (error) => {
              if (error instanceof Error) setError(error.message);
            },
          });
        } else {
          await stream.respond(payload.command.resume);
        }
        maybeStartErase(messagesRef.current, threadId);
        return true;
      } catch (streamError) {
        if (isMissingThreadError(streamError)) {
          await recoverMissingThread(threadId);
        } else if (mountedRef.current) {
          setError(
            streamError instanceof Error
              ? streamError.message
              : "That message didn't go through. Please try again.",
          );
        }
        return false;
      } finally {
        busyRef.current = false;
        if (mountedRef.current) setBusy(false);
      }
    },
    [maybeStartErase, recoverMissingThread, stream, waitForTerminal],
  );

  const ensureThread = useCallback(async (): Promise<string> => {
    const existing = activeThreadRef.current;
    if (existing !== null) return existing;
    const created = await deps.api.createThread();
    activeThreadRef.current = created.thread_id;
    setActiveThreadId(created.thread_id);
    await refreshThreads();
    return created.thread_id;
  }, [deps.api, refreshThreads]);

  const send = useCallback(
    async (text: string): Promise<void> => {
      const question = text.trim();
      if (question === "" || busyRef.current) return;
      const threadId = await ensureThread();
      const echo: WireMessage = {
        type: "human",
        id: `local-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        content: question,
      };
      commitMessages(mergeMessages(messagesRef.current, [echo]));
      if (getThreadTitle(threadId) === null) setThreadTitle(threadId, deriveTitle(question));
      const attachmentId =
        uploadRef.current.phase === "staged" && uploadRef.current.stage === "done"
          ? uploadRef.current.info.uploadId
          : undefined;
      const input: RunInput =
        attachmentId === undefined
          ? { question }
          : { question: SENTINEL_QUESTION, attachment_id: attachmentId };
      if (attachmentId !== undefined) setUpload(applyUploadEvent(uploadRef.current, { kind: "consumed" }));
      await runStream(threadId, { input });
    },
    [ensureThread, runStream],
  );

  const attach = useCallback(
    async (file: File): Promise<void> => {
      if (busyRef.current || uploadRef.current.phase === "inflight") return;
      let threadId = activeThreadRef.current;
      if (threadId === null) threadId = await ensureThread();
      const info = {
        uploadId: deps.newUploadId(),
        threadId,
        fileName: file.name,
        fileSizeLabel: formatFileSize(file.size),
      };
      let uploadState: UploadUi = applyUploadEvent(uploadRef.current, { kind: "started", info });
      setUpload(uploadState);
      try {
        const posted = await deps.api.postUpload({ uploadId: info.uploadId, threadId, file });
        uploadState = applyUploadEvent(uploadState, { kind: "stage", stage: posted.stage });
        setUpload(uploadState);
      } catch (uploadError) {
        uploadState = applyUploadEvent(uploadState, {
          kind: "error",
          detail: uploadError instanceof Error ? uploadError.message : "Upload failed.",
        });
        setUpload(uploadState);
        return;
      }
      for (let attempt = 0; attempt < deps.poll.upload.maxPolls && shouldPollStatus(uploadState); attempt += 1) {
        await deps.sleep(deps.poll.upload.pollMs);
        if (!shouldPollStatus(uploadState)) break;
        try {
          const status = await deps.api.getUploadStatus(info.uploadId);
          uploadState = applyUploadEvent(uploadState, { kind: "stage", stage: status.stage });
          setUpload(uploadState);
        } catch (statusError) {
          uploadState = applyUploadEvent(uploadState, {
            kind: "error",
            detail: statusError instanceof Error ? statusError.message : "Upload status failed.",
          });
          setUpload(uploadState);
          return;
        }
      }
      if (shouldPollStatus(uploadState)) {
        uploadState = applyUploadEvent(uploadState, {
          kind: "error",
          detail: "Upload timed out. Please try again.",
        });
        setUpload(uploadState);
      }
    },
    [deps.api, deps.newUploadId, deps.poll.upload, deps.sleep, ensureThread],
  );

  const approveInterrupt = useCallback(
    async (resume: ResumePayload): Promise<void> => {
      const threadId = activeThreadRef.current;
      if (threadId === null || busyRef.current) return;
      if (!isValidResumePayload(resume)) {
        chatTelemetry({ kind: "unknown_interrupt", detail: "malformed resume" });
        return;
      }
      const current = pendingInterruptRef.current;
      const currentAll = pendingInterruptsRef.current;
      commitPendingInterrupts([]);
      const clean = await runStream(threadId, { command: { resume } });
      if (!clean && current !== null) {
        commitPendingInterrupts(currentAll.length > 0 ? currentAll : [current]);
      }
    },
    [commitPendingInterrupts, runStream],
  );

  const approveInterrupts = useCallback(
    async (resumes: ResumePayload[]): Promise<void> => {
      const threadId = activeThreadRef.current;
      if (threadId === null || busyRef.current) return;
      const valid = resumes.filter(isValidResumePayload);
      if (valid.length !== resumes.length) {
        chatTelemetry({ kind: "unknown_interrupt", detail: "malformed resume in batch" });
        if (valid.length === 0) return;
      }
      const currentAll = pendingInterruptsRef.current;
      commitPendingInterrupts([]);
      let clean = false;
      const streamAny = stream as unknown as { respondAll?: (rs: ResumePayload[]) => Promise<void>; respond: (r: ResumePayload) => Promise<void> };
      if (typeof streamAny.respondAll === "function" && valid.length > 1) {
        try {
          if (busyRef.current) return;
          busyRef.current = true;
          if (mountedRef.current) setBusy(true);
          setError(null);
          if (!(await waitForTerminal(threadId))) {
            if (mountedRef.current) setError("Your previous request is still finishing. Please try again.");
            clean = false;
          } else {
            await streamAny.respondAll(valid);
            clean = true;
          }
        } catch (e) {
          if (mountedRef.current) setError(e instanceof Error ? e.message : "That message didn't go through. Please try again.");
          clean = false;
        } finally {
          busyRef.current = false;
          if (mountedRef.current) setBusy(false);
        }
      } else {
        // sequential fallback — first resume via runStream (which handles waitForTerminal), rest via direct respond
        let first = true;
        clean = true;
        for (const r of valid) {
          const ok = first ? await runStream(threadId, { command: { resume: r } }) : await (async () => {
            try { await stream.respond(r); return true; } catch { return false; }
          })();
          if (!ok) clean = false;
          first = false;
        }
      }
      if (!clean && currentAll.length > 0) commitPendingInterrupts(currentAll);
    },
    [commitPendingInterrupts, runStream, stream, waitForTerminal],
  );

  const forkFromCheckpoint = useCallback(
    async (checkpointId: string, input: RunInput): Promise<boolean> => {
      const threadId = activeThreadRef.current;
      if (threadId === null || busyRef.current) return false;
      if (!isForkV2Enabled()) {
        if (mountedRef.current) setError("Branching requires v2 stream mode.");
        return false;
      }
      const trimmed = checkpointId.trim();
      if (trimmed === "") {
        if (mountedRef.current) setError("That message couldn't be retried. Please try again.");
        return false;
      }
      busyRef.current = true;
      setBusy(true);
      setError(null);
      try {
        if (!(await waitForTerminal(threadId))) {
          if (mountedRef.current) setError("Your previous request is still finishing. Please try again.");
          return false;
        }
        const question = input.question;
        if (question !== SENTINEL_QUESTION && question.trim() !== "") {
          const echo: WireMessage = {
            type: "human",
            id: `local-${Date.now()}-${Math.random().toString(36).slice(2)}`,
            content: question,
          };
          commitMessages(mergeMessages(messagesRef.current, [echo]));
          if (getThreadTitle(threadId) === null) setThreadTitle(threadId, deriveTitle(question));
        }
        const attachmentId = (input as RunInput).attachment_id;
        const submitInput: RunInput =
          attachmentId !== undefined ? { question: input.question, attachment_id: attachmentId } : { question: input.question };
        await stream.submit(submitInput, {
          ...getRunStreamParams(),
          threadId,
          forkFrom: trimmed,
          onError: (error) => {
            if (error instanceof Error) setError(error.message);
          },
        } as CoachSubmitOptions & { forkFrom: string });
        maybeStartErase(messagesRef.current, threadId);
        return true;
      } catch (streamError) {
        if (isMissingThreadError(streamError) || (streamError instanceof CoachApiError && (streamError as CoachApiError).status === 404)) {
          if (mountedRef.current) setError(streamError instanceof Error ? streamError.message : "That message couldn't be retried. Please try again.");
        } else if (mountedRef.current) {
          setError(streamError instanceof Error ? streamError.message : "That message didn't go through. Please try again.");
        }
        return false;
      } finally {
        busyRef.current = false;
        if (mountedRef.current) setBusy(false);
      }
    },
    [commitMessages, maybeStartErase, stream, waitForTerminal],
  );

  const regenerate = useCallback(
    async (checkpointIdOverride?: string): Promise<void> => {
      const threadId = activeThreadRef.current;
      if (threadId === null || busyRef.current) return;
      const gate = regenerateEligibility(buildTurns(messagesRef.current), {
        hasPendingInterrupt: pendingInterrupt !== null,
        attachmentPending: uploadRef.current.phase === "staged" && uploadRef.current.stage === "done",
      });
      if (!gate.eligible || gate.question === null) return;
      const question = gate.question;
      if (isForkV2Enabled() && deps.api.getThreadHistory !== undefined) {
        let checkpointId = checkpointIdOverride ?? null;
        if (checkpointId === null) {
          try {
            const history = (await deps.api.getThreadHistory(threadId)) as unknown[] as Record<string, unknown>[];
            if (Array.isArray(history) && history.length > 0) {
              const latest = history[0] as Record<string, unknown>;
              const parent = (latest.parent_checkpoint_id as string) ?? null;
              const current = (latest.checkpoint_id as string) ?? null;
              checkpointId = parent ?? current;
            }
          } catch (historyError) {
            if (historyError instanceof CoachApiError && historyError.status === 404) {
              if (mountedRef.current) setError(historyError.message);
              return;
            }
            checkpointId = null;
          }
        }
        if (checkpointId !== null && checkpointId.trim() !== "") {
          const ok = await forkFromCheckpoint(checkpointId, { question });
          if (ok) return;
          return;
        }
      }
      if (await waitForTerminal(threadId)) await send(question);
    },
    [deps.api, forkFromCheckpoint, pendingInterrupt, send, waitForTerminal],
  );

  const editAndResubmit = useCallback(
    async (turnKey: string, newText: string, checkpointId: string): Promise<void> => {
      void turnKey;
      const text = newText.trim();
      if (text === "" || busyRef.current) return;
      const threadId = activeThreadRef.current;
      if (threadId === null) return;
      if (!isForkV2Enabled()) {
        if (mountedRef.current) setError("Editing requires v2 stream mode.");
        return;
      }
      let targetCheckpoint = checkpointId.trim();
      if (targetCheckpoint === "" && deps.api.getThreadHistory !== undefined) {
        try {
          const history = (await deps.api.getThreadHistory(threadId)) as unknown[] as Record<string, unknown>[];
          if (Array.isArray(history) && history.length > 0) {
            const latest = history[0] as Record<string, unknown>;
            targetCheckpoint = ((latest.parent_checkpoint_id as string) ?? (latest.checkpoint_id as string) ?? "").trim();
          }
        } catch {
          targetCheckpoint = "";
        }
      }
      if (targetCheckpoint === "") {
        if (mountedRef.current) setError("That message couldn't be retried. Please try again.");
        return;
      }
      await forkFromCheckpoint(targetCheckpoint, { question: text });
    },
    [deps.api, forkFromCheckpoint],
  );

  const branch = useCallback(async (): Promise<void> => {
    const threadId = activeThreadRef.current;
    if (threadId === null || busyRef.current) return;
    const copied = await deps.api.copyThread(threadId);
    const title = getThreadTitle(threadId);
    if (title !== null) setThreadTitle(copied.thread_id, title);
    await refreshThreads();
    await loadThreadState(copied.thread_id);
  }, [deps.api, loadThreadState, refreshThreads]);

  const sendFeedback = useCallback(
    async (score: 1 | -1): Promise<void> => {
      const threadId = activeThreadRef.current;
      if (threadId === null) return;
      const aiMessages = messagesRef.current.filter(isAiMessage);
      const latest = aiMessages.length > 0 ? aiMessages[aiMessages.length - 1] : undefined;
      const messageId = latest?.id;
      if (messageId === undefined) return;
      const key = `${threadId}:${messageId}`;
      try {
        await deps.api.postFeedback({ threadId, messageId, score });
        if (mountedRef.current) setFeedback((f) => ({ ...f, sent: { ...f.sent, [key]: score } }));
      } catch {
        if (mountedRef.current) setFeedback((f) => ({ ...f, failed: { ...f.failed, [key]: true } }));
      }
    },
    [deps.api],
  );

  const newConversation = useCallback((): void => {
    setActiveThreadId(null);
    activeThreadRef.current = null;
    commitMessages([]);
    commitPendingInterrupts([]);
    setUpload({ phase: "idle" });
    setError(null);
  }, [commitMessages, commitPendingInterrupts]);

  const selectThread = useCallback(
    async (threadId: string): Promise<void> => {
      if (busyRef.current) return;
      try {
        await loadThreadState(threadId);
      } catch (stateError) {
        if (isMissingThreadError(stateError)) {
          await recoverMissingThread(threadId);
        } else {
          setError(
            stateError instanceof Error ? stateError.message : "Couldn't open that conversation.",
          );
        }
      }
    },
    [loadThreadState, recoverMissingThread],
  );

  const removeThread = useCallback(
    async (threadId: string): Promise<void> => {
      await deps.api.deleteThread(threadId);
      clearThreadTitles([threadId]);
      if (activeThreadRef.current === threadId) newConversation();
      await refreshThreads();
    },
    [deps.api, newConversation, refreshThreads],
  );

  const signOut = useCallback(async (): Promise<void> => {
    await deps.auth.signOut();
  }, [deps.auth]);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const listed = await refreshThreads();
        if (!active) return;
        const latest = listed[0];
        if (latest !== undefined) {
          await loadThreadState(latest.thread_id);
        }
      } catch {
        if (active) setError("Couldn't load your conversations.");
      } finally {
        if (active) setInitializing(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const gate = regenerateEligibility(turns, {
    hasPendingInterrupt: pendingInterrupt !== null,
    attachmentPending: upload.phase === "staged" && upload.stage === "done",
  });

  return {
    threads,
    activeThreadId,
    messages,
    turns,
    toolCalls,
    pendingInterrupt,
    pendingInterrupts,
    busy: busy || stream.isLoading,
    isLoading: stream.isLoading,
    error,
    upload,
    uploadStage: documentStage(upload),
    erase,
    feedback,
    initializing,
    regenerateGate: gate,
    threadTitle,
    send,
    attach,
    approveInterrupt,
    approveInterrupts,
    respondAll: approveInterrupts,
    regenerate,
    branch,
    forkFromCheckpoint,
    editAndResubmit,
    sendFeedback,
    newConversation,
    selectThread,
    removeThread,
    signOut,
    dismissError: () => setError(null),
    values: catalogValues,
    catalogValues,
    valuesEnvelopes,
    valuesTrees,
  };
}

function synthesizeToolCallsFromMessages(messages: readonly WireMessage[]): ToolCallHandleView[] {
  const toolMessages = new Map<string, WireMessage>();
  for (const m of messages) {
    if (m.type === "tool" && typeof m.tool_call_id === "string") toolMessages.set(m.tool_call_id, m);
  }
  const calls: ToolCallHandleView[] = [];
  for (const m of messages) {
    if (m.type !== "ai" || !Array.isArray(m.tool_calls)) continue;
    for (const tc of m.tool_calls) {
      const correlated = toolMessages.get(tc.id);
      let status = "running";
      let output: unknown = null;
      let error: string | undefined = undefined;
      if (correlated !== undefined) {
        if (correlated.status === "error") {
          status = "error";
          error = typeof correlated.content === "string" ? correlated.content : JSON.stringify(correlated.content);
        } else {
          status = "finished";
          output = correlated.content;
        }
      }
      calls.push({
        id: tc.id,
        callId: tc.id,
        name: tc.name,
        args: tc.args,
        input: tc.args,
        output,
        status,
        error,
        namespace: [],
      });
    }
  }
  return calls;
}
