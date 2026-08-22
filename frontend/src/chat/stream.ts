import { isRenderedNode } from "./coachProtocol";
import {
  firstInterruptValue,
  mergeMessages,
  toWireMessages,
  type WireMessage,
} from "./model";

/**
 * Updates-only stream application. One `updates` event carries
 * `{node_name: {messages?: [...]}}`; only allow-listed nodes are read, and
 * human messages from the stream are ignored (the human bubble is a local
 * echo — the gate's scrubbed HumanMessage still lands in latest state for
 * history reads).
 */

export interface StreamEventPart {
  event: string;
  data: unknown;
}

export interface StreamStateDelta {
  messages: WireMessage[];
  interruptValue: unknown | null;
}

export interface ChatTelemetryEvent {
  kind: "unknown_node" | "unknown_interrupt" | "stream_error";
  node?: string;
  detail?: string;
}

export const chatTelemetrySink: { emit: (event: ChatTelemetryEvent) => void } = {
  emit: (event) => {
    if (process.env.NODE_ENV !== "test") {
      console.warn("[coach-chat]", event);
    }
  },
};

export function chatTelemetry(event: ChatTelemetryEvent): void {
  chatTelemetrySink.emit(event);
}

/** Pure reducer: fold one stream part into the chat message model. */
export function applyStreamPart(
  messages: readonly WireMessage[],
  part: StreamEventPart,
): StreamStateDelta {
  if (part.event === "__interrupt__") {
    return { messages: [], interruptValue: firstInterruptValue(part.data) };
  }
  if (part.event !== "updates") {
    return { messages: [...messages], interruptValue: null };
  }
  const data = part.data;
  if (typeof data !== "object" || data === null) return { messages: [...messages], interruptValue: null };
  // The Agent Server delivers interrupts inside the updates stream as a
  // `{"__interrupt__": [...]}` data payload — not as a separate event.
  if ("__interrupt__" in data) {
    return {
      messages: [...messages],
      interruptValue: firstInterruptValue((data as Record<string, unknown>).__interrupt__),
    };
  }
  let next = [...messages];
  for (const [node, update] of Object.entries(data as Record<string, unknown>)) {
    if (!isRenderedNode(node)) {
      chatTelemetry({ kind: "unknown_node", node });
      continue;
    }
    const updateMessages =
      typeof update === "object" && update !== null && "messages" in update
        ? (update as Record<string, unknown>).messages
        : undefined;
    const arrived = toWireMessages(updateMessages).filter((message) => message.type !== "human");
    if (arrived.length === 0) continue;
    next = mergeMessages(next, arrived);
  }
  return { messages: next, interruptValue: null };
}

/**
 * Drive one SDK run stream to completion, folding parts through
 * `applyStreamPart` against a mutable accumulator the caller owns. Emits
 * the folded snapshot per part plus any interrupt value seen.
 */
export async function consumeRunStream(
  parts: AsyncIterable<StreamEventPart>,
  accumulated: WireMessage[],
  onFolded: (snapshot: { messages: WireMessage[]; interruptValue: unknown | null }) => void,
): Promise<void> {
  for await (const part of parts) {
    const delta = applyStreamPart(accumulated, part);
    accumulated.splice(0, accumulated.length, ...delta.messages);
    onFolded(delta);
  }
}
