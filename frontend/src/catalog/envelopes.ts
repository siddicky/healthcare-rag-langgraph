import { z } from "zod";

/**
 * The canonical DATA envelope shape produced by the backend helper
 * `make_envelope(thread_id, human_msg_id, block_id, data, text)`:
 * a JSON string riding a ToolMessage content field.
 * {turn_scope_id: sha256-hex, block_id: str, data: {...}, text: str}
 */
export interface DataEnvelope {
  turn_scope_id: string;
  block_id: string;
  data: unknown;
  text: string;
}

const EnvelopeSchema = z.object({
  turn_scope_id: z.string(),
  block_id: z.string(),
  data: z.unknown(),
  text: z.string(),
});

/** Parse one ToolMessage content value; returns null for anything else. */
export function parseDataEnvelope(content: unknown): DataEnvelope | null {
  if (typeof content !== "string") return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(content);
  } catch {
    return null;
  }
  const result = EnvelopeSchema.safeParse(parsed);
  if (!result.success) return null;
  return result.data;
}

export function parseDataEnvelopes(contents: readonly unknown[]): DataEnvelope[] {
  const envelopes: DataEnvelope[] = [];
  for (const content of contents) {
    const envelope = parseDataEnvelope(content);
    if (envelope !== null) envelopes.push(envelope);
  }
  return envelopes;
}
