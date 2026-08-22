import { z } from "zod";
import { parseDataEnvelope, type DataEnvelope } from "@/catalog/envelopes";
import { ERASE_MARKER_NAME, SENTINEL_QUESTION } from "./coachProtocol";

/**
 * The chat message model: LangChain-serialized messages off the wire
 * (stream updates and the projected latest state share this shape), split
 * into HumanMessage-bounded turns for rendering.
 */

export interface WireToolCall {
  id: string;
  name: string;
  args?: unknown;
}

export interface WireMessage {
  id?: string;
  type?: string;
  name?: string | null;
  content?: unknown;
  tool_calls?: WireToolCall[];
  tool_call_id?: string;
  status?: string;
}

/** Text content of a wire message: plain string or text blocks. */
export function messageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const parts: string[] = [];
    for (const block of content) {
      if (
        typeof block === "object" &&
        block !== null &&
        "text" in block &&
        typeof (block as Record<string, unknown>).text === "string"
      ) {
        parts.push((block as Record<string, unknown>).text as string);
      }
    }
    return parts.join("");
  }
  return "";
}

function isWireMessage(value: unknown): value is WireMessage {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.type !== "string") return false;
  if (candidate.id !== undefined && typeof candidate.id !== "string") return false;
  if (candidate.tool_call_id !== undefined && typeof candidate.tool_call_id !== "string") return false;
  return true;
}

export function toWireMessages(values: unknown): WireMessage[] {
  if (!Array.isArray(values)) return [];
  return values.filter(isWireMessage);
}

/** Stable identity for merge dedupe (finalize re-projects the whole channel). */
export function messageKey(message: WireMessage): string {
  if (typeof message.id === "string" && message.id !== "") return `id:${message.id}`;
  if (typeof message.tool_call_id === "string" && message.tool_call_id !== "") {
    return `tc:${message.tool_call_id}:${message.type ?? ""}`;
  }
  return `h:${message.type ?? ""}:${messageText(message.content)}`;
}

export function isHumanMessage(message: WireMessage): boolean {
  return message.type === "human";
}

export function isAiMessage(message: WireMessage): boolean {
  return message.type === "ai";
}

export function isToolMessage(message: WireMessage): boolean {
  return message.type === "tool";
}

/** The v19 erase-confirmation marker (AI message with the fixed name). */
export function isEraseMarker(message: WireMessage): boolean {
  return isAiMessage(message) && message.name === ERASE_MARKER_NAME;
}

export function containsEraseMarker(messages: readonly WireMessage[]): boolean {
  return messages.some(isEraseMarker);
}

/** Merge streamed/re-read messages into an insertion-ordered, id-deduped list. */
export function mergeMessages(
  current: readonly WireMessage[],
  incoming: readonly WireMessage[],
): WireMessage[] {
  const merged = new Map<string, WireMessage>();
  for (const message of current) merged.set(messageKey(message), message);
  for (const message of incoming) {
    const key = messageKey(message);
    const existing = merged.get(key);
    merged.set(key, existing === undefined ? message : { ...existing, ...message });
  }
  return [...merged.values()];
}

export interface TurnModel {
  key: string;
  /** The turn's user message — a local echo (id `local:*`) or the scrubbed wire copy from latest state. */
  human: WireMessage | null;
  /** ai/tool messages that follow the user message, in order. */
  messages: WireMessage[];
  envelopes: DataEnvelope[];
  scopeId: string | null;
}

export function buildTurns(messages: readonly WireMessage[]): TurnModel[] {
  const turns: TurnModel[] = [];
  let current: TurnModel | null = null;

  for (const message of messages) {
    if (isHumanMessage(message)) {
      current = {
        key: `human:${messageKey(message)}`,
        human: message,
        messages: [],
        envelopes: [],
        scopeId: null,
      };
      turns.push(current);
      continue;
    }
    if (current === null) {
      current = { key: "prefix", human: null, messages: [], envelopes: [], scopeId: null };
      turns.push(current);
    }
    current.messages.push(message);
    if (isToolMessage(message)) {
      const envelope = parseDataEnvelope(message.content);
      if (envelope !== null) {
        current.envelopes.push(envelope);
        if (current.scopeId === null) current.scopeId = envelope.turn_scope_id;
      }
    }
  }
  return turns;
}

/** The turn window's own user text (never a global "last question"). */
export function turnQuestion(turn: TurnModel): string | null {
  if (turn.human === null) return null;
  const text = messageText(turn.human.content).trim();
  return text === "" ? null : text;
}

export function turnHasToolActivity(turn: TurnModel): boolean {
  if (turn.messages.some(isToolMessage)) return true;
  return turn.messages.some(
    (message) => Array.isArray(message.tool_calls) && message.tool_calls.length > 0,
  );
}

/**
 * compose_ui trees render from the AI tool call AFTER its correlated
 * successful ToolMessage (ToolMessages carry no args — the AI call is the
 * source; an error ToolMessage suppresses the tree).
 */
export function composeTreesForTurn(turn: TurnModel): { callId: string; tree: unknown }[] {
  const trees: { callId: string; tree: unknown }[] = [];
  for (const message of turn.messages) {
    if (!isAiMessage(message) || !Array.isArray(message.tool_calls)) continue;
    for (const call of message.tool_calls) {
      if (call.name !== "compose_ui") continue;
      const correlated = turn.messages.find(
        (candidate) =>
          isToolMessage(candidate) &&
          candidate.tool_call_id === call.id &&
          candidate.status !== "error",
      );
      if (correlated === undefined) continue;
      const args = call.args;
      const tree =
        typeof args === "object" && args !== null && "tree" in args
          ? (args as Record<string, unknown>).tree
          : null;
      if (tree === null) continue;
      trees.push({ callId: call.id, tree });
    }
  }
  return trees;
}

export interface RegenerateGateInput {
  hasPendingInterrupt: boolean;
  attachmentPending: boolean;
}

export interface RegenerateGate {
  eligible: boolean;
  /** The turn window's own user message — resent verbatim when eligible. */
  question: string | null;
}

/**
 * Regenerate eligibility, evaluated on the LATEST completed assistant turn's
 * HumanMessage-bounded window ONLY: no tool calls, no ToolMessages, no
 * interrupt, no erasure marker, no document turn. Both the eligibility and
 * the resent question derive from the SAME window — an older safe turn can
 * never replay a later mutating question.
 */
export function regenerateEligibility(
  turns: readonly TurnModel[],
  input: RegenerateGateInput,
): RegenerateGate {
  const latest = turns.length > 0 ? turns[turns.length - 1] : undefined;
  if (latest === undefined) return { eligible: false, question: null };
  const question = turnQuestion(latest);
  if (question === null || question === SENTINEL_QUESTION) {
    return { eligible: false, question: null };
  }
  if (!latest.messages.some(isAiMessage)) return { eligible: false, question: null };
  if (turnHasToolActivity(latest)) return { eligible: false, question: null };
  if (latest.messages.some(isEraseMarker)) return { eligible: false, question: null };
  if (input.hasPendingInterrupt || input.attachmentPending) {
    return { eligible: false, question: null };
  }
  return { eligible: true, question };
}

// ---------------------------------------------------------------------------
// Interrupt payloads (fixed-contract cards). The server guarantees at most
// ONE interrupt per run; unknown payload shapes render nothing + telemetry.
// ---------------------------------------------------------------------------

export const CalendarChangePayloadSchema = z.object({
  eventLabel: z.string(),
  fromLabel: z.string(),
  toLabel: z.string(),
  reason: z.string().optional(),
  status: z.enum(["pending", "confirmed", "declined"]).optional(),
});

export const ExtractedFieldSchema = z.object({
  key: z.string(),
  label: z.string(),
  value: z.string(),
  needsReview: z.boolean().optional(),
});

export const MemoryExtractionPayloadSchema = z.object({
  sourceLabel: z.string(),
  fields: z.array(ExtractedFieldSchema),
});

export type CalendarChangePayload = z.infer<typeof CalendarChangePayloadSchema>;
export type MemoryExtractionPayload = z.infer<typeof MemoryExtractionPayloadSchema>;
export type ExtractedField = z.infer<typeof ExtractedFieldSchema>;

export type InterruptPayloadKind =
  | { kind: "calendar-change"; card: CalendarChangePayload }
  | { kind: "memory-extraction"; payload: MemoryExtractionPayload }
  | { kind: "unknown" };

export function classifyInterruptPayload(value: unknown): InterruptPayloadKind {
  if (typeof value !== "object" || value === null) return { kind: "unknown" };
  if ("fromLabel" in value || "toLabel" in value) {
    const parsed = CalendarChangePayloadSchema.safeParse(value);
    if (parsed.success) return { kind: "calendar-change", card: parsed.data };
  }
  if ("sourceLabel" in value || "fields" in value) {
    const parsed = MemoryExtractionPayloadSchema.safeParse(value);
    if (parsed.success) return { kind: "memory-extraction", payload: parsed.data };
  }
  return { kind: "unknown" };
}

/** The interrupts array off a projected state read / `__interrupt__` event. */
export function firstInterruptValue(interrupts: unknown): unknown {
  if (!Array.isArray(interrupts) || interrupts.length === 0) return null;
  const first = interrupts[0];
  if (typeof first !== "object" || first === null) return null;
  if ("value" in first) return (first as Record<string, unknown>).value;
  return null;
}

// ---------------------------------------------------------------------------
// Card-bearing AI message content (stream surfaces for the fixed-contract
// cards). Code-assembled AI messages carry either a reminder delivery
// ("literal line\n{DATA envelope with a reminder:* block}") or a pure-JSON
// component confirmation; neither ever renders its raw JSON.
// ---------------------------------------------------------------------------

export const ReminderCardPayloadSchema = z.object({
  title: z.string(),
  schedule: z.string(),
  weekday: z.string(),
  time: z.string(),
  active: z.boolean(),
  nextRun: z.string().optional(),
});

export type ReminderCardPayload = z.infer<typeof ReminderCardPayloadSchema>;

export interface ReminderDelivery {
  /** The code-assembled literal line (the bubble text). */
  literal: string;
  /** null when the envelope matched but its data is not a valid card. */
  card: ReminderCardPayload | null;
}

/**
 * The reminder_delivery node's message: a fixed literal, a newline, then the
 * ReminderCard DATA envelope (block_id `reminder:<reminder_id>`).
 */
export function parseReminderDelivery(content: unknown): ReminderDelivery | null {
  if (typeof content !== "string") return null;
  const newline = content.indexOf("\n");
  if (newline === -1) return null;
  const envelope = parseDataEnvelope(content.slice(newline + 1));
  if (envelope === null || !envelope.block_id.startsWith("reminder:")) return null;
  const card = ReminderCardPayloadSchema.safeParse(envelope.data);
  return { literal: content.slice(0, newline), card: card.success ? card.data : null };
}

export const ResolvedMemoryFieldSchema = z.object({
  key: z.string(),
  label: z.string(),
  value: z.string(),
  needsReview: z.boolean().optional(),
  status: z.enum(["saved", "discarded"]),
  notice: z.string().optional(),
});

export const MemoryConfirmationSchema = z.object({
  component: z.literal("MemoryExtractionCard"),
  data: z.object({
    sourceLabel: z.string(),
    fields: z.array(ResolvedMemoryFieldSchema),
  }),
});

export type ResolvedMemoryField = z.infer<typeof ResolvedMemoryFieldSchema>;
export type MemoryConfirmation = z.infer<typeof MemoryConfirmationSchema>;

/** The review_document confirmation: pure JSON `{component, data}`. */
export function parseMemoryConfirmation(content: unknown): MemoryConfirmation | null {
  const component = parseComponentCard(content);
  if (component === null || component.component !== "MemoryExtractionCard") return null;
  const result = MemoryConfirmationSchema.safeParse(JSON.parse(typeof content === "string" ? content : ""));
  return result.success ? result.data : null;
}

/** Any pure-JSON `{component: <string>, …}` AI content — never rendered as text. */
export function parseComponentCard(content: unknown): { component: string } | null {
  if (typeof content !== "string" || !content.startsWith("{")) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const component = (parsed as Record<string, unknown>).component;
  return typeof component === "string" ? { component } : null;
}

/**
 * Bubble text for an AI message: card JSON never renders — a reminder
 * delivery shows its literal line, a component confirmation shows nothing.
 */
export function aiDisplayText(content: unknown): string {
  if (parseComponentCard(content) !== null) return "";
  const delivery = parseReminderDelivery(content);
  if (delivery !== null) return delivery.literal;
  return messageText(content);
}

/** Natural-language NEW turns dispatched by full-mode ReminderCard actions. */
export type ReminderActionKind = "pause" | "resume" | "move" | "cancel";

export function reminderActionTurn(
  kind: ReminderActionKind,
  title: string,
  schedule?: { weekday: string; timeLabel: string },
): string {
  switch (kind) {
    case "pause":
      return `Pause my ${title} reminder`;
    case "resume":
      return `Resume my ${title} reminder`;
    case "move":
      return `Move my ${title} reminder to ${schedule?.weekday} at ${schedule?.timeLabel}`;
    case "cancel":
      return `Cancel my ${title} reminder`;
  }
}
