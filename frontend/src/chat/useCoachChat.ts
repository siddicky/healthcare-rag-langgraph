"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ThreadSummary, ThreadStateProjection } from "./coachApi";
import type { RunStreamPart } from "./coachApi";
import {
  SENTINEL_QUESTION,
  type ResumePayload,
  type RunInput,
  type ThreadStatus,
} from "./coachProtocol";
import {
  buildTurns,
  containsEraseMarker,
  firstInterruptValue,
  isAiMessage,
  mergeMessages,
  regenerateEligibility,
  toWireMessages,
  type TurnModel,
  type WireMessage,
} from "./model";
import { runErasePhase2, type EraseOutcome } from "./erase";
import { consumeRunStream } from "./stream";
import {
  applyUploadEvent,
  documentStage,
  formatFileSize,
  shouldPollStatus,
  type UploadUi,
} from "./uploadFlow";
import { clearThreadTitles, deriveTitle, getThreadTitle, setThreadTitle } from "./titles";

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
  postUpload(upload: { uploadId: string; threadId: string; file: File }): Promise<{ stage?: string }>;
  getUploadStatus(uploadId: string): Promise<{ stage?: string }>;
  postFeedback(feedback: { threadId: string; messageId: string; score: 1 | -1 }): Promise<void>;
}

export interface CoachStreamBundle {
  streamRun(
    threadId: string,
    payload: { input: RunInput } | { command: { resume: ResumePayload } },
  ): AsyncIterable<RunStreamPart>;
}

export interface CoachSignOut {
  signOut(): Promise<unknown>;
}

export interface CoachChatDeps {
  api: CoachApiBundle;
  stream: CoachStreamBundle;
  auth: CoachSignOut;
  sleep(ms: number): Promise<void>;
  newUploadId(): string;
  poll: { erase: { pollMs: number; maxPolls: number }; upload: { pollMs: number; maxPolls: number } };
}

export type EraseUi = { status: "idle" } | { status: "running"; note: string } | { status: "done" };

export interface FeedbackUi {
  sent: Record<string, 1 | -1>;
  failed: Record<string, true>;
}

const SIDEBAR_PAGE_SIZE = 50;

export function useCoachChat(deps: CoachChatDeps) {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<WireMessage[]>([]);
  const [pendingInterrupt, setPendingInterrupt] = useState<unknown | null>(null);
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
  const mountedRef = useRef(true);

  messagesRef.current = messages;
  activeThreadRef.current = activeThreadId;
  uploadRef.current = upload;

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
          setPendingInterrupt(null);
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
    [commitMessages, deps.api, deps.poll.erase, deps.sleep, refreshThreads],
  );

  const maybeStartErase = useCallback(
    (current: readonly WireMessage[], threadId: string | null): void => {
      if (threadId === null) return;
      if (!containsEraseMarker(current)) return;
      void startErasePhase2(threadId);
    },
    [startErasePhase2],
  );

  const loadThreadState = useCallback(
    async (threadId: string): Promise<void> => {
      const projected = await deps.api.getThreadState(threadId);
      const wire = toWireMessages(projected.values.messages);
      commitMessages(wire);
      setPendingInterrupt(firstInterruptValue(projected.interrupts));
      setActiveThreadId(threadId);
      maybeStartErase(wire, threadId);
    },
    [commitMessages, deps.api, maybeStartErase],
  );

  const runStream = useCallback(
    async (
      threadId: string,
      payload: { input: RunInput } | { command: { resume: ResumePayload } },
    ): Promise<void> => {
      if (busyRef.current) return;
      busyRef.current = true;
      setBusy(true);
      setError(null);
      try {
        const parts = deps.stream.streamRun(threadId, payload);
        await consumeRunStream(parts, messagesRef.current, (delta) => {
          commitMessages(delta.messages);
          if (delta.interruptValue !== null) setPendingInterrupt(delta.interruptValue);
        });
        maybeStartErase(messagesRef.current, threadId);
      } catch (streamError) {
        if (mountedRef.current) {
          setError(
            streamError instanceof Error
              ? streamError.message
              : "That message didn't go through. Please try again.",
          );
        }
      } finally {
        busyRef.current = false;
        if (mountedRef.current) setBusy(false);
      }
    },
    [commitMessages, deps.stream, maybeStartErase],
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
      setPendingInterrupt(null);
      await runStream(threadId, { command: { resume } });
    },
    [runStream],
  );

  const waitTerminalThen = useCallback(
    async (threadId: string, action: () => Promise<void>): Promise<boolean> => {
      for (let attempt = 0; attempt < deps.poll.erase.maxPolls; attempt += 1) {
        if (attempt > 0) await deps.sleep(deps.poll.erase.pollMs);
        const thread = await deps.api.getThread(threadId);
        if (thread.status !== "busy") {
          await action();
          return true;
        }
      }
      return false;
    },
    [deps.api, deps.poll.erase, deps.sleep],
  );

  const regenerate = useCallback(async (): Promise<void> => {
    const threadId = activeThreadRef.current;
    if (threadId === null || busyRef.current) return;
    const gate = regenerateEligibility(buildTurns(messagesRef.current), {
      hasPendingInterrupt: pendingInterrupt !== null,
      attachmentPending: uploadRef.current.phase === "staged" && uploadRef.current.stage === "done",
    });
    if (!gate.eligible || gate.question === null) return;
    const question = gate.question;
    await waitTerminalThen(threadId, () => send(question));
  }, [pendingInterrupt, send, waitTerminalThen]);

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
    setPendingInterrupt(null);
    setUpload({ phase: "idle" });
    setError(null);
  }, [commitMessages]);

  const selectThread = useCallback(
    async (threadId: string): Promise<void> => {
      if (busyRef.current) return;
      try {
        await loadThreadState(threadId);
      } catch (stateError) {
        setError(
          stateError instanceof Error ? stateError.message : "Couldn't open that conversation.",
        );
      }
    },
    [loadThreadState],
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
    turns,
    pendingInterrupt,
    busy,
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
    regenerate,
    branch,
    sendFeedback,
    newConversation,
    selectThread,
    removeThread,
    signOut,
    dismissError: () => setError(null),
  };
}
