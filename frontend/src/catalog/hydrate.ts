import type { DataRef } from "./dataRef";
import type { DataEnvelope } from "./envelopes";

/**
 * Hydration: resolve data-ref objects against the turn's tool DATA envelopes.
 *
 * A ref hydrates ONLY when its turn_scope_id matches the CURRENT turn's scope
 * AND an envelope with that block_id exists under that same scope AND the RFC
 * 6901 pointer resolves inside its `data`. A prior turn's envelope carrying
 * the same block_id can never hydrate (cross-turn refs are rejected).
 */
export type RefResolution =
  | { ok: true; value: unknown }
  | { ok: false; reason: "unresolved" | "cross_turn" };

export interface Hydrator {
  resolve(ref: DataRef): RefResolution;
}

export function createHydrator(turnScopeId: string, envelopes: readonly DataEnvelope[]): Hydrator {
  const inScope = new Map<string, DataEnvelope>();
  for (const envelope of envelopes) {
    if (envelope.turn_scope_id === turnScopeId) {
      inScope.set(envelope.block_id, envelope);
    }
  }

  return {
    resolve(ref: DataRef): RefResolution {
      const envelope = inScope.get(ref.__ref.block_id);
      if (envelope === undefined) {
        const existsElsewhere = envelopes.some((e) => e.block_id === ref.__ref.block_id);
        return { ok: false, reason: existsElsewhere ? "cross_turn" : "unresolved" };
      }
      const value = resolvePointer(envelope.data, ref.__ref.pointer);
      if (value === undefined) {
        return { ok: false, reason: "unresolved" };
      }
      return { ok: true, value };
    },
  };
}

/** RFC 6901 JSON pointer resolution. "" -> the whole document. */
export function resolvePointer(document: unknown, pointer: string): unknown {
  if (pointer === "") return document;
  if (!pointer.startsWith("/")) return undefined;
  let current: unknown = document;
  for (const rawToken of pointer.slice(1).split("/")) {
    const token = rawToken.replaceAll("~1", "/").replaceAll("~0", "~");
    if (Array.isArray(current)) {
      if (!/^\d+$/.test(token)) return undefined;
      const index = Number(token);
      current = current[index];
    } else if (typeof current === "object" && current !== null) {
      current = (current as Record<string, unknown>)[token];
    } else {
      return undefined;
    }
    if (current === undefined) return undefined;
  }
  return current;
}
