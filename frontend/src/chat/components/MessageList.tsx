"use client";

import { Fragment, useEffect, useMemo, useRef } from "react";
import type { DataEnvelope } from "@/catalog/envelopes";
import { parseDataEnvelope } from "@/catalog/envelopes";
import type { DispatchHandlers } from "@/catalog/dispatch";
import { CatalogTree } from "@/catalog/render";
import { CalendarChangeCard } from "@/components/generative-ui/CalendarChangeCard";
import { DocumentIngestCard } from "@/components/generative-ui/DocumentIngestCard";
import { Markdown } from "@/components/generative-ui/Markdown";
import { MemoryExtractionCard } from "@/components/generative-ui/MemoryExtractionCard";
import { ReminderCard } from "@/components/generative-ui/ReminderCard";
import { CalendarChangePayloadSchema } from "@/chat/model";
import {
  aiDisplayText,
  composeTreesForTurn,
  isAiMessage,
  isToolMessage,
  messageText,
  parseComponentCard,
  parseMemoryConfirmation,
  parseReminderDelivery,
  reminderActionTurn,
  type TurnModel,
  type WireMessage,
} from "@/chat/model";
import { chatTelemetry } from "@/chat/stream";
import type { ResumePayload } from "@/chat/coachProtocol";
import type { UploadUi } from "@/chat/uploadFlow";
import { InterruptPanel, ReminderEnvelopeCards } from "./InterruptPanel";
import { ToolCallCard, type ToolCallView } from "./ToolCallCard";

/**
 * A calendar-change confirmation DATA envelope —
 * `{card: {eventLabel, fromLabel, toLabel, reason, status}, schedule}` under
 * block `calendar-change:<op_id>` — renders the fixed-contract card with its
 * outcome state.
 */
function CalendarChangeEnvelope({ envelope }: { envelope: DataEnvelope }) {
  const data = envelope.data;
  const card =
    typeof data === "object" && data !== null && "card" in data
      ? CalendarChangePayloadSchema.safeParse((data as Record<string, unknown>).card)
      : { success: false as const };
  if (!card.success) {
    chatTelemetry({ kind: "unknown_interrupt", detail: envelope.block_id });
    return null;
  }
  return (
    <div className="widget-wrap">
      <CalendarChangeCard
        eventLabel={card.data.eventLabel}
        fromLabel={card.data.fromLabel}
        toLabel={card.data.toLabel}
        reason={card.data.reason}
        status={card.data.status ?? "confirmed"}
      />
    </div>
  );
}

function ToolEnvelopeCards({ message }: { message: WireMessage }) {
  const envelope = parseDataEnvelope(message.content);
  if (envelope === null) return null;
  if (envelope.block_id.startsWith("calendar-change:")) {
    return <CalendarChangeEnvelope envelope={envelope} />;
  }
  if (envelope.block_id === "reminders:list") {
    return <ReminderEnvelopeCards data={envelope.data} />;
  }
  return null;
}

function AiBubble({ message }: { message: WireMessage }) {
  const text = aiDisplayText(message.content);
  if (text === "") return null;
  return (
    <div className="bubble-row assistant">
      <div className="avatar">N</div>
      <div className="bubble assistant">
        <Markdown content={text} />
      </div>
    </div>
  );
}

/** Stream-surface cards riding an AI message (fixed-contract, not composable). */
function AiMessageCards({ message, onReminderAction }: { message: WireMessage; onReminderAction?: (text: string) => void }) {
  const confirmation = parseMemoryConfirmation(message.content);
  if (confirmation !== null) {
    return (
      <div className="widget-wrap" data-testid="memory-confirmation">
        <MemoryExtractionCard sourceLabel={confirmation.data.sourceLabel} resolvedFields={confirmation.data.fields} />
      </div>
    );
  }
  const delivery = parseReminderDelivery(message.content);
  if (delivery !== null) {
    if (delivery.card === null) {
      chatTelemetry({ kind: "unknown_interrupt", detail: "reminder-delivery" });
      return null;
    }
    const card = delivery.card;
    const dispatch = onReminderAction ?? (() => {});
    return (
      <div className="widget-wrap" data-testid="reminder-card">
        <ReminderCard
          title={card.title}
          schedule={card.schedule}
          nextRun={card.nextRun}
          weekday={card.weekday}
          time={card.time}
          active={card.active}
          onToggle={(next) => dispatch(reminderActionTurn(next ? "resume" : "pause", card.title))}
          onScheduleChange={(schedule) => dispatch(reminderActionTurn("move", card.title, schedule))}
          onCancel={() => dispatch(reminderActionTurn("cancel", card.title))}
        />
      </div>
    );
  }
  const componentCard = parseComponentCard(message.content);
  if (componentCard !== null) {
    chatTelemetry({ kind: "unknown_interrupt", detail: componentCard.component });
  }
  return null;
}

function synthesizeToolCallsForTurn(turn: TurnModel): ToolCallView[] {
  const toolById = new Map<string, WireMessage>();
  for (const m of turn.messages) {
    if (isToolMessage(m) && typeof m.tool_call_id === "string") toolById.set(m.tool_call_id, m);
  }
  const calls: ToolCallView[] = [];
  for (const m of turn.messages) {
    if (!isAiMessage(m) || !Array.isArray(m.tool_calls)) continue;
    for (const tc of m.tool_calls) {
      const correlated = toolById.get(tc.id);
      let status = "running";
      let output: unknown = null;
      let error: string | undefined;
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

function toolCallsForTurn(turn: TurnModel, all: readonly ToolCallView[] | undefined): ToolCallView[] {
  if (all !== undefined && all.length > 0) {
    const ids = new Set<string>();
    for (const m of turn.messages) {
      if (Array.isArray(m.tool_calls)) for (const tc of m.tool_calls) ids.add(tc.id);
      if (isToolMessage(m) && typeof m.tool_call_id === "string") ids.add(m.tool_call_id);
    }
    if (ids.size === 0) return [];
    return all.filter((c) => ids.has(c.id) || (c.callId !== undefined && ids.has(c.callId)));
  }
  return synthesizeToolCallsForTurn(turn);
}

function TurnView({
  turn,
  onReminderAction,
  dispatchHandlers,
  toolCalls,
}: {
  turn: TurnModel;
  onReminderAction?: (text: string) => void;
  dispatchHandlers?: DispatchHandlers;
  toolCalls?: readonly ToolCallView[];
}) {
  const humanText = turn.human !== null ? messageText(turn.human.content) : "";
  const trees = composeTreesForTurn(turn);
  const scopeId = turn.scopeId ?? "";
  const turnToolCalls = toolCallsForTurn(turn, toolCalls);
  return (
    <div>
      {humanText !== "" && (
        <div className="bubble-row human">
          <div className="bubble human">{humanText}</div>
        </div>
      )}
      {turn.messages.filter(isAiMessage).map((message) => (
        <Fragment key={messageKey(message)}>
          <AiBubble message={message} />
          <AiMessageCards message={message} onReminderAction={onReminderAction} />
        </Fragment>
      ))}
      {trees.map(({ callId, tree }) => (
        <div className="widget-wrap" key={callId} data-testid="compose-tree">
          <CatalogTree tree={tree} envelopes={turn.envelopes} turnScopeId={scopeId} handlers={dispatchHandlers ?? {}} />
        </div>
      ))}
      {turnToolCalls.map((call) => (
        <div className="widget-wrap" key={call.id} data-testid="tool-call-wrap">
          <ToolCallCard call={call} />
        </div>
      ))}
      {turn.messages.filter(isToolMessage).map((message) => (
        <ToolEnvelopeCards key={messageKey(message)} message={message} />
      ))}
    </div>
  );
}

function messageKey(message: WireMessage): string {
  return message.id ?? message.tool_call_id ?? `${message.type}:${messageText(message.content)}`;
}

export interface MessageListProps {
  turns: TurnModel[];
  pendingInterrupt: unknown | null;
  upload: UploadUi;
  busy: boolean;
  onApprove: (resume: ResumePayload) => void;
  latestAiMessageId: string | null;
  actionBar?: React.ReactNode;
  /** Full-mode ReminderCard actions dispatch new chat turns in the member's phrasing. */
  onReminderAction?: (text: string) => void;
  /** Handlers for composed-tree dispatch ids (catalog Button actions). */
  dispatchHandlers?: DispatchHandlers;
  toolCalls?: readonly ToolCallView[];
}

export function MessageList({
  turns,
  pendingInterrupt,
  upload,
  busy,
  onApprove,
  latestAiMessageId,
  actionBar,
  onReminderAction,
  dispatchHandlers,
  toolCalls,
}: MessageListProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const rendered = useMemo(() => turns.map((turn) => turn.key).join("|"), [turns]);

  useEffect(() => {
    if (scrollRef.current !== null) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [rendered, pendingInterrupt, upload.phase]);

  if (turns.length === 0 && pendingInterrupt === null && upload.phase === "idle") {
    return null;
  }
  return (
    <div className="thread-scroll" ref={scrollRef}>
      <div className="thread-inner">
        {turns.map((turn, index) => (
          <div key={turn.key}>
            <TurnView turn={turn} onReminderAction={onReminderAction} dispatchHandlers={dispatchHandlers} toolCalls={toolCalls} />
            {index === turns.length - 1 && latestAiMessageId !== null && actionBar}
          </div>
        ))}
        {upload.phase !== "idle" && upload.phase !== "failed" && (
          <div className="widget-wrap" data-testid="document-ingest">
            <DocumentIngestCard
              fileName={upload.info.fileName}
              fileSizeLabel={upload.info.fileSizeLabel}
              stage={upload.phase === "inflight" ? "uploading" : upload.stage}
            />
          </div>
        )}
        {upload.phase === "failed" && (
          <p className="inline-note" data-testid="upload-error">
            Upload failed: {upload.detail}
          </p>
        )}
        {pendingInterrupt !== null && (
          <InterruptPanel value={pendingInterrupt} onApprove={onApprove} disabled={busy} />
        )}
      </div>
    </div>
  );
}
