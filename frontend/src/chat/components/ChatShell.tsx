"use client";

import { useRef } from "react";
import type { DispatchActionId, DispatchHandlers } from "@/catalog/dispatch";
import { isAiMessage } from "@/chat/model";
import { OPENERS, UPLOAD_OPENER } from "@/chat/coachProtocol";
import { useCoachStream, type CoachStreamDeps } from "@/chat/useCoachStream";
import { ActionBar } from "./ActionBar";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { ThreadSidebar } from "./ThreadSidebar";

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

  const started =
    chat.turns.length > 0 || chat.pendingInterrupt !== null || chat.upload.phase !== "idle";

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
            {chat.error}
          </div>
        )}

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
            pendingInterrupt={chat.pendingInterrupt}
            pendingInterrupts={(chat as unknown as { pendingInterrupts?: unknown[] }).pendingInterrupts ?? (chat.pendingInterrupt !== null ? [chat.pendingInterrupt] : [])}
            upload={chat.upload}
            busy={chat.busy}
            onApprove={(resume) => void chat.approveInterrupt(resume)}
            onApproveAll={(resumes) => void (chat as unknown as { approveInterrupts?: (rs: typeof resumes) => Promise<void> }).approveInterrupts?.(resumes)}
            latestAiMessageId={latestAiMessageId}
            onReminderAction={(text) => void chat.send(text)}
            dispatchHandlers={dispatchHandlers}
            toolCalls={chat.toolCalls as unknown as import("./ToolCallCard").ToolCallView[]}
            actionBar={
              <ActionBar
                showRegenerate={chat.regenerateGate.eligible}
                feedbackSent={feedbackSent}
                feedbackFailed={feedbackFailed}
                disabled={chat.busy}
                onRegenerate={() => void chat.regenerate()}
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
