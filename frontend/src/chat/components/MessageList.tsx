"use client";

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type { DataEnvelope } from "@/catalog/envelopes";
import { parseDataEnvelope } from "@/catalog/envelopes";
import type { DispatchHandlers } from "@/catalog/dispatch";
import { CatalogTree } from "@/catalog/render";
import {
  envelopesFromValues,
  syntheticTreesFromStructuredValues,
  treesFromValues,
} from "@/catalog/values";
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
  messageReasoning,
  messageText,
  parseComponentCard,
  parseMemoryConfirmation,
  parseReminderDelivery,
  reminderActionTurn,
  type TurnModel,
  type WireMessage,
} from "@/chat/model";
import { ReasoningBlock } from "./ReasoningBlock";
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
  const reasoning = messageReasoning(message);
  const text = aiDisplayText(message.content);
  const hasReasoning = reasoning !== null && reasoning.trim() !== "";
  const hasText = text !== "";
  if (!hasReasoning && !hasText) return null;
  return (
    <div className="bubble-row assistant">
      <div className="avatar">N</div>
      <div className="bubble assistant">
        {hasReasoning && <ReasoningBlock reasoning={reasoning!} />}
        {hasText && <Markdown content={text} />}
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
  valuesEnvelopes,
  onEditTurn,
  busy,
}: {
  turn: TurnModel;
  onReminderAction?: (text: string) => void;
  dispatchHandlers?: DispatchHandlers;
  toolCalls?: readonly ToolCallView[];
  valuesEnvelopes?: readonly DataEnvelope[];
  onEditTurn?: (turnKey: string, newText: string, checkpointId: string) => void;
  busy?: boolean;
}) {
  const humanText = turn.human !== null ? messageText(turn.human.content) : "";
  const trees = composeTreesForTurn(turn);
  const scopeId = turn.scopeId ?? "";
  const turnToolCalls = toolCallsForTurn(turn, toolCalls);
  const combinedEnvelopes = valuesEnvelopes !== undefined && valuesEnvelopes.length > 0 ? [...turn.envelopes, ...valuesEnvelopes] : turn.envelopes;
  const canEdit = onEditTurn !== undefined && turn.human !== null && process.env.NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER === "v2";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(humanText);
  useEffect(() => {
    setDraft(humanText);
  }, [humanText]);
  return (
    <div>
      {humanText !== "" && (
        <div className="bubble-row human" style={{ alignItems: "center", gap: "var(--space-xs, 8px)" }}>
          <div className="bubble human">{editing ? draft : humanText}</div>
          {canEdit && !editing && (
            <button
              className="msg-action-btn"
              title="Edit and resubmit"
              aria-label="Edit and resubmit"
              disabled={busy}
              data-testid="turn-edit-btn"
              onClick={() => {
                setDraft(humanText);
                setEditing(true);
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" />
                <path d="M18.5 2.5a2.12 2.12 0 013 3L12 15l-4 1 1-4 9.5-9.5z" />
              </svg>
            </button>
          )}
        </div>
      )}
      {canEdit && editing && (
        <div className="widget-wrap" data-testid="turn-edit-panel" style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs, 8px)" }}>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label="Edit message"
            data-testid="turn-edit-input"
            rows={2}
            style={{
              width: "100%",
              borderRadius: "var(--border-radius-sm, 6px)",
              border: "1px solid var(--birch, #d1c5b4)",
              padding: "var(--space-xs, 8px)",
              fontFamily: "var(--font-body)",
              resize: "vertical",
            }}
          />
          <div style={{ display: "flex", gap: "var(--space-xs, 8px)" }}>
            <button
              className="btn btn-primary"
              disabled={busy || draft.trim() === ""}
              data-testid="turn-edit-submit"
              onClick={() => {
                const next = draft.trim();
                if (next === "") return;
                onEditTurn?.(turn.key, next, "");
                setEditing(false);
              }}
            >
              Resubmit
            </button>
            <button
              className="btn btn-secondary"
              data-testid="turn-edit-cancel"
              onClick={() => {
                setEditing(false);
                setDraft(humanText);
              }}
            >
              Cancel
            </button>
          </div>
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
          <CatalogTree tree={tree} envelopes={combinedEnvelopes} turnScopeId={scopeId} handlers={dispatchHandlers ?? {}} />
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
  pendingInterrupts?: readonly unknown[] | null;
  upload: UploadUi;
  busy: boolean;
  onApprove: (resume: ResumePayload) => void;
  onApproveAll?: (resumes: ResumePayload[]) => void;
  latestAiMessageId: string | null;
  actionBar?: React.ReactNode;
  /** Full-mode ReminderCard actions dispatch new chat turns in the member's phrasing. */
  onReminderAction?: (text: string) => void;
  /** Handlers for composed-tree dispatch ids (catalog Button actions). */
  dispatchHandlers?: DispatchHandlers;
  toolCalls?: readonly ToolCallView[];
  /** Structured state via the values channel — forwarded from useCoachStream's stream.values. */
  values?: Record<string, unknown> | null;
  valuesEnvelopes?: readonly DataEnvelope[];
  valuesTrees?: readonly unknown[];
  onEditTurn?: (turnKey: string, newText: string, checkpointId: string) => void;
}

export function MessageList({
  turns,
  pendingInterrupt,
  pendingInterrupts,
  upload,
  busy,
  onApprove,
  onApproveAll,
  latestAiMessageId,
  actionBar,
  onReminderAction,
  dispatchHandlers,
  toolCalls,
  values,
  valuesEnvelopes,
  valuesTrees,
  onEditTurn,
}: MessageListProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const rendered = useMemo(() => turns.map((turn) => turn.key).join("|"), [turns]);

  useEffect(() => {
    if (scrollRef.current !== null) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [rendered, pendingInterrupt, upload.phase, pendingInterrupts]);

  const interrupts = pendingInterrupts !== undefined && pendingInterrupts !== null
    ? (pendingInterrupts.length > 0 ? pendingInterrupts : pendingInterrupt !== null ? [pendingInterrupt] : [])
    : pendingInterrupt !== null ? [pendingInterrupt] : [];

  const catalogData = useMemo(() => values ?? {}, [values]);
  const derivedValuesEnvelopes = useMemo(() => {
    if (valuesEnvelopes !== undefined) return valuesEnvelopes;
    return envelopesFromValues(catalogData as Record<string, unknown>);
  }, [valuesEnvelopes, catalogData]);
  const lastScopeId = turns.length > 0 ? (turns[turns.length - 1]?.scopeId ?? "__values__") : "__values__";
  const syntheticEnvelopes = useMemo(
    () => envelopesFromValues(catalogData as Record<string, unknown>, lastScopeId),
    [catalogData, lastScopeId],
  );
  const allValuesEnvelopes = useMemo(() => {
    const merged = [...derivedValuesEnvelopes, ...syntheticEnvelopes];
    const seen = new Set<string>();
    return merged.filter((e) => {
      const k = `${e.turn_scope_id}::${e.block_id}`;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }, [derivedValuesEnvelopes, syntheticEnvelopes]);
  const derivedValuesTrees = useMemo(() => {
    if (valuesTrees !== undefined) return valuesTrees;
    return treesFromValues(catalogData as Record<string, unknown>);
  }, [valuesTrees, catalogData]);
  const syntheticTrees = useMemo(
    () => syntheticTreesFromStructuredValues(catalogData as Record<string, unknown>, lastScopeId),
    [catalogData, lastScopeId],
  );
  const allValuesTrees = useMemo(
    () => (derivedValuesTrees.length > 0 ? derivedValuesTrees : syntheticTrees),
    [derivedValuesTrees, syntheticTrees],
  );

  const hasInterrupts = interrupts.length > 0;
  const hasValuesContent = allValuesTrees.length > 0 || allValuesEnvelopes.length > 0;

  if (turns.length === 0 && !hasInterrupts && upload.phase === "idle" && !hasValuesContent) {
    return null;
  }
  return (
    <div className="thread-scroll" ref={scrollRef}>
      <div className="thread-inner">
        {turns.map((turn, index) => (
          <div key={turn.key}>
            <TurnView turn={turn} onReminderAction={onReminderAction} dispatchHandlers={dispatchHandlers} toolCalls={toolCalls} valuesEnvelopes={allValuesEnvelopes} onEditTurn={onEditTurn} busy={busy} />
            {index === turns.length - 1 && latestAiMessageId !== null && actionBar}
          </div>
        ))}
        {allValuesTrees.length > 0 && (
          <div data-testid="values-catalog">
            {allValuesTrees.map((tree, idx) => {
              const scopeFromTree = (() => {
                try {
                  const obj = tree as Record<string, unknown>;
                  const props = (obj.props ?? {}) as Record<string, unknown>;
                  for (const v of Object.values(props)) {
                    if (typeof v === "object" && v !== null && "__ref" in (v as Record<string, unknown>)) {
                      const ref = (v as Record<string, unknown>).__ref as Record<string, unknown>;
                      if (typeof ref.turn_scope_id === "string") return ref.turn_scope_id as string;
                    }
                    if (typeof v === "object" && v !== null && "action" in (v as Record<string, unknown>)) {
                      continue;
                    }
                  }
                } catch {
                  // ignore
                }
                return lastScopeId;
              })();
              return (
                <div className="widget-wrap" key={`values-${idx}`} data-testid="values-compose-tree">
                  <CatalogTree tree={tree} envelopes={allValuesEnvelopes} turnScopeId={scopeFromTree} handlers={dispatchHandlers ?? {}} />
                </div>
              );
            })}
          </div>
        )}
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
        {hasInterrupts && (
          <>
            {interrupts.map((value, idx) => (
              <InterruptPanel
                key={idx}
                value={value}
                onApprove={onApprove}
                disabled={busy}
              />
            ))}
            {interrupts.length > 1 && onApproveAll !== undefined && (
              <div className="widget-wrap" data-testid="interrupt-approve-all">
                <button
                  className="btn btn-primary"
                  onClick={() => onApproveAll(interrupts.map(() => ({ accept: true })))}
                  disabled={busy}
                >
                  Approve all
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
