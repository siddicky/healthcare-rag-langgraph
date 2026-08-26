"use client";

import { useRef } from "react";
import type { DispatchActionId, DispatchHandlers } from "@/catalog/dispatch";
import { isAiMessage } from "@/chat/model";
import { OPENERS, UPLOAD_OPENER } from "@/chat/coachProtocol";
import { useCoachStream, type CoachStreamDeps } from "@/chat/useCoachStream";
import { isHistoryBranchUiEnabled } from "@/chat/featureGates";
import { CoachInterruptRenderer } from "@/chat/renderers";
import { ActionBar } from "./ActionBar";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { QueueBar } from "./QueueBar";
import { ThreadSidebar } from "./ThreadSidebar";
import { TimeTravel } from "./TimeTravel";

/** Composed-tree Button dispatches become NEW natural-language turns. */
const DISPATCH_ACTION_TURNS: Partial<Record<DispatchActionId, string>> = {
  log_weight: "Log today's weight",
  log_injection: "Log my injection",
  view_schedule: "What's on my calendar this month?",
  change_schedule: "I want to change one of my scheduled events",
  set_reminder: "Set a reminder for me",
  cancel_reminder: "Cancel one of my reminders",
};

export function ChatShell({
  deps,
  email,
  onSignedOut,
}: {
  deps: CoachStreamDeps;
  email: string;
  onSignedOut: () => void;
}) {
  const chat = useCoachStream(deps);
  const openerFileRef = useRef<HTMLInputElement | null>(null);

  const dispatchHandlers: DispatchHandlers = {
    ...Object.fromEntries(
      Object.entries(DISPATCH_ACTION_TURNS).map(([action, turn]) => [
        action,
        () => {
          if (turn !== undefined) void chat.send(turn);
        },
      ]),
    ),
    upload_document: () => openerFileRef.current?.click(),
    confirm: () => {
      if (chat.pendingInterrupt !== null) void chat.approveInterrupt({ accept: true });
    },
    decline: () => {
      if (chat.pendingInterrupt !== null) void chat.approveInterrupt({ accept: false });
    },
  };

  const aiMessages = chat.turns.flatMap((turn) => turn.messages.filter(isAiMessage));
  const latestAi = aiMessages.length > 0 ? aiMessages[aiMessages.length - 1] : undefined;
  const latestAiMessageId = latestAi?.id ?? null;
  const feedbackKey =
    chat.activeThreadId !== null && latestAiMessageId !== null
      ? `${chat.activeThreadId}:${latestAiMessageId}`
      : null;
  const feedbackSent = feedbackKey !== null ? (chat.feedback.sent[feedbackKey] ?? null) : null;
  const feedbackFailed = feedbackKey !== null ? chat.feedback.failed[feedbackKey] === true : false;

  const valuesHasStructured =
    ((chat as unknown as { valuesEnvelopes?: readonly unknown[] }).valuesEnvelopes?.length ?? 0) > 0 ||
    ((chat as unknown as { valuesTrees?: readonly unknown[] }).valuesTrees?.length ?? 0) > 0 ||
    Object.keys((chat as unknown as { values?: Record<string, unknown> }).values ?? {}).some(
      (k) => !["messages", "question", "attachment_id"].includes(k) && (chat as unknown as { values?: Record<string, unknown> }).values?.[k] !== undefined,
    );
  const started =
    chat.turns.length > 0 || chat.pendingInterrupt !== null || chat.upload.phase !== "idle" || valuesHasStructured;

  const isV2 = process.env.NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER === "v2";
  const interruptsRenderedByTransport = chat.interruptsRenderedByTransport === true;
  const historyUi = isHistoryBranchUiEnabled();
  const history = (chat as unknown as { history?: import("@/chat/useCoachStream").ThreadHistory[] | null }).history ?? null;
  const historyLoading = (chat as unknown as { historyLoading?: boolean }).historyLoading === true;
  const selectedCheckpointId = (chat as unknown as { selectedCheckpointId?: string | null }).selectedCheckpointId ?? null;
  const showHistory = isV2 && historyUi && history !== null && history.length > 0;

  return (
    <div className="app">
      <ThreadSidebar
        threads={chat.threads}
        activeThreadId={chat.activeThreadId}
        email={email}
        threadTitle={chat.threadTitle}
        onNewConversation={chat.newConversation}
        onSelectThread={(threadId) => void chat.selectThread(threadId)}
        onDeleteThread={(threadId) => void chat.removeThread(threadId)}
        onSignOut={() => {
          void chat.signOut();
          onSignedOut();
        }}
      />
      <div className="main">
        <div className="topbar">
          <div className="coach-pill">
            Talking with <b>Nymble AI Coach</b>
          </div>
          <div className="spacer" />
        </div>

        {chat.erase.status === "running" && (
          <div className="banner banner-erase" role="status">
            Erasing your saved data — one moment…
          </div>
        )}
        {chat.erase.status === "done" && (
          <div className="banner banner-erase" role="status">
            All saved data erased.
          </div>
        )}
        {chat.error !== null && (
          <div className="banner banner-error" role="alert">
            <span>{chat.error}</span>
            {(chat as unknown as { wasDisconnected?: boolean }).wasDisconnected === true && (
              <button
                data-testid="reconnect-button"
                className="btn btn-secondary btn-sm"
                onClick={() => {
                  const tid = (chat as unknown as { activeThreadId: string | null }).activeThreadId;
                  const rejoin = (chat as unknown as { rejoin?: (t: string) => void }).rejoin;
                  if (tid !== null && rejoin) rejoin(tid);
                }}
                style={{
                  marginLeft: "var(--space-sm)",
                  padding: "6px 12px",
                  fontSize: 13,
                  borderRadius: "var(--border-radius-pill)",
                  border: "1px solid var(--carrot)",
                  background: "var(--white)",
                  color: "var(--carrot-accessible)",
                }}
              >
                Reconnect
              </button>
            )}
          </div>
        )}
        {(() => {
          const isThreadLoading = (chat as unknown as { isThreadLoading?: boolean }).isThreadLoading === true;
          const wasDisconnected = (chat as unknown as { wasDisconnected?: boolean }).wasDisconnected === true;
          const isLoading = (chat as unknown as { isLoading?: boolean }).isLoading === true;
          const streamError = (chat as unknown as { streamError?: unknown }).streamError;
          const showReconnecting = wasDisconnected && (isThreadLoading || (isLoading && streamError == null && chat.error == null));
          if (!showReconnecting) return null;
          return (
            <div
              data-testid="reconnecting-banner"
              role="status"
              aria-live="polite"
              className="banner"
              style={{
                background: "var(--gold-20)",
                border: "1px solid var(--border-gold)",
                color: "var(--rust)",
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
              }}
            >
              <span
                aria-hidden
                style={{
                  width: 14,
                  height: 14,
                  borderRadius: "50%",
                  border: "2px solid var(--camel)",
                  borderTopColor: "transparent",
                  display: "inline-block",
                  animation: "ds-spin 0.8s linear infinite",
                }}
              />
              Reconnecting...
            </div>
          );
        })()}

        {!started ? (
          <div className="empty-state">
            <div className="wordmark">
              nym<span>ble</span>
            </div>
            <h1>Nymble Coach</h1>
            <p>Message your coach anytime — real support, not another app to open.</p>
            <div className="openers">
              {OPENERS.map((opener) => (
                <button
                  className="opener"
                  key={opener}
                  onClick={() => {
                    if (opener === UPLOAD_OPENER) {
                      openerFileRef.current?.click();
                    } else {
                      void chat.send(opener);
                    }
                  }}
                >
                  {opener}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <MessageList
            turns={chat.turns}
            pendingInterrupt={interruptsRenderedByTransport ? null : chat.pendingInterrupt}
            pendingInterrupts={
              interruptsRenderedByTransport
                ? []
                : (chat as unknown as { pendingInterrupts?: unknown[] }).pendingInterrupts ??
                  (chat.pendingInterrupt !== null ? [chat.pendingInterrupt] : [])
            }
            upload={chat.upload}
            busy={chat.busy}
            onApprove={(resume) => void chat.approveInterrupt(resume)}
            onApproveAll={(resumes) => void (chat as unknown as { approveInterrupts?: (rs: typeof resumes) => Promise<void> }).approveInterrupts?.(resumes)}
            latestAiMessageId={latestAiMessageId}
            interruptSurface={
              interruptsRenderedByTransport ? <CoachInterruptRenderer /> : null
            }
            hasTransportInterrupt={
              interruptsRenderedByTransport && chat.pendingInterrupt !== null
            }
            onReminderAction={(text) => void chat.send(text)}
            dispatchHandlers={dispatchHandlers}
            toolCalls={chat.toolCalls as unknown as import("./ToolCallCard").ToolCallView[]}
            values={(chat as unknown as { values?: Record<string, unknown> }).values ?? (chat as unknown as { catalogValues?: Record<string, unknown> }).catalogValues}
            valuesEnvelopes={(chat as unknown as { valuesEnvelopes?: readonly import("@/catalog/envelopes").DataEnvelope[] }).valuesEnvelopes}
            valuesTrees={(chat as unknown as { valuesTrees?: readonly unknown[] }).valuesTrees}
            onEditTurn={
              historyUi
                ? (turnKey, newText, checkpointId) =>
                    void (chat as unknown as { editAndResubmit?: (k: string, t: string, c: string) => Promise<void> }).editAndResubmit?.(turnKey, newText, checkpointId)
                : undefined
            }
            actionBar={
              <ActionBar
                showRegenerate={chat.regenerateGate.eligible}
                feedbackSent={feedbackSent}
                feedbackFailed={feedbackFailed}
                disabled={chat.busy}
                onRegenerate={() => void chat.regenerate()}
                canBranch={historyUi}
                onBranch={() => void chat.branch()}
                onFeedback={(score) => void chat.sendFeedback(score)}
              />
            }
          />
        )}

        <input
          ref={openerFileRef}
          type="file"
          accept="application/pdf,image/jpeg,image/png"
          aria-label="Attach my intake form"
          hidden
          data-testid="opener-attach-input"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file !== undefined) void chat.attach(file);
          }}
        />
        {(chat as unknown as { queue?: readonly import("@/chat/useCoachStream").QueuedEntry[] }).queue !== undefined &&
          (chat as unknown as { queue: readonly import("@/chat/useCoachStream").QueuedEntry[] }).queue.length > 0 && (
            <QueueBar
              queue={(chat as unknown as { queue: readonly import("@/chat/useCoachStream").QueuedEntry[] }).queue}
              onCancel={(id) => (chat as unknown as { cancelQueued?: (id: string) => void }).cancelQueued?.(id)}
              onClear={() => (chat as unknown as { clearQueue?: () => void }).clearQueue?.()}
            />
          )}
        {showHistory && (
          <TimeTravel
            history={history}
            historyLoading={historyLoading}
            selectedCheckpointId={selectedCheckpointId}
            busy={chat.busy}
            onTimeTravel={(checkpointId) => void (chat as unknown as { timeTravel?: (id: string) => Promise<void> }).timeTravel?.(checkpointId)}
            onFork={(checkpointId) =>
              void (chat as unknown as { resumeFromCheckpoint?: (id: string, input: import("@/chat/coachProtocol").RunInput) => Promise<boolean> }).resumeFromCheckpoint?.(checkpointId, {
                question: "Continue from this checkpoint",
              })
            }
            onFetchHistory={() => void (chat as unknown as { fetchHistory?: () => Promise<unknown> }).fetchHistory?.()}
          />
        )}
        <Composer
          disabled={chat.busy}
          attachmentReady={chat.upload.phase === "staged" && chat.upload.stage === "done"}
          onSend={(text) => void chat.send(text)}
          onAttach={(file) => void chat.attach(file)}
        />
      </div>
    </div>
  );
}
