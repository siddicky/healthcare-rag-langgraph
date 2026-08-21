"use client";

import { useEffect, useMemo, useRef } from "react";
import type { DataEnvelope } from "@/catalog/envelopes";
import { parseDataEnvelope } from "@/catalog/envelopes";
import { CatalogTree } from "@/catalog/render";
import { CalendarChangeCard } from "@/components/generative-ui/CalendarChangeCard";
import { DocumentIngestCard } from "@/components/generative-ui/DocumentIngestCard";
import { CalendarChangePayloadSchema } from "@/chat/model";
import {
  composeTreesForTurn,
  isAiMessage,
  isToolMessage,
  messageText,
  type TurnModel,
  type WireMessage,
} from "@/chat/model";
import { chatTelemetry } from "@/chat/stream";
import type { ResumePayload } from "@/chat/coachProtocol";
import type { UploadUi } from "@/chat/uploadFlow";
import { InterruptPanel, ReminderEnvelopeCards } from "./InterruptPanel";

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
  const text = messageText(message.content);
  if (text === "") return null;
  return (
    <div className="bubble-row assistant">
      <div className="avatar">N</div>
      <div className="bubble assistant">{text}</div>
    </div>
  );
}

function TurnView({ turn }: { turn: TurnModel }) {
  const humanText = turn.human !== null ? messageText(turn.human.content) : "";
  const trees = composeTreesForTurn(turn);
  const scopeId = turn.scopeId ?? "";
  return (
    <div>
      {humanText !== "" && (
        <div className="bubble-row human">
          <div className="bubble human">{humanText}</div>
        </div>
      )}
      {turn.messages.filter(isAiMessage).map((message) => (
        <AiBubble key={messageKey(message)} message={message} />
      ))}
      {trees.map(({ callId, tree }) => (
        <div className="widget-wrap" key={callId} data-testid="compose-tree">
          <CatalogTree tree={tree} envelopes={turn.envelopes} turnScopeId={scopeId} handlers={{}} />
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
}

export function MessageList({
  turns,
  pendingInterrupt,
  upload,
  busy,
  onApprove,
  latestAiMessageId,
  actionBar,
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
            <TurnView turn={turn} />
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
