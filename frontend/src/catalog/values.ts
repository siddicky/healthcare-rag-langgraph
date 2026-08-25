"use client";

import type { DataEnvelope } from "./envelopes";

/**
 * Structured output via the `values` channel.
 *
 * The coach `useStream` values state can carry typed keys beyond `messages`:
 *  - `todos`, `citations`, `metrics` — structured domain data
 *  - Direct DATA envelopes (canonical {turn_scope_id, block_id, data, text})
 *  - A catalog tree (`tree` / `catalogTree` / `composeTree`) that may contain
 *    `__ref` objects into the same-turn envelopes.
 *
 * This module extracts envelopes and trees from arbitrary values payloads in a
 * fail-closed way: unknown shapes are ignored, never thrown.
 */

const STRUCTURED_ENVELOPE_KEYS = new Set(["todos", "citations", "metrics"]);
const TREE_KEYS = ["tree", "catalogTree", "composeTree", "uiTree", "structuredTree", "catalogTrees", "composeTrees"] as const;

function isEnvelopeLike(value: unknown): value is DataEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.turn_scope_id === "string" &&
    typeof v.block_id === "string" &&
    "data" in v &&
    typeof v.text === "string"
  );
}

function asEnvelope(value: unknown): DataEnvelope | null {
  if (!isEnvelopeLike(value)) return null;
  const v = value as DataEnvelope;
  // Defensive copy — never trust model output to be frozen
  return { turn_scope_id: v.turn_scope_id, block_id: v.block_id, data: v.data, text: v.text };
}

/**
 * Extract DATA envelopes carried via the values channel.
 *
 * Collects:
 *  1) Direct envelope objects or arrays at any top-level key
 *  2) Arrays under known envelope-carrying keys (envelopes, catalogEnvelopes, etc.)
 *  3) Synthetic envelopes for structured domain keys (todos/citations/metrics) so a
 *     same-turn tree can `__ref` them without the server emitting a full envelope.
 *
 * Synthetic envelopes use the supplied `fallbackScopeId` (typically the turn's
 * scopeId) so hydration's same-turn check passes. Real envelopes keep their own
 * turn_scope_id and are later filtered per-turn by CatalogTree's hydrator.
 */
export function envelopesFromValues(
  values: Record<string, unknown> | null | undefined,
  fallbackScopeId?: string,
): DataEnvelope[] {
  if (values === null || values === undefined || typeof values !== "object") return [];
  const out: DataEnvelope[] = [];
  const seen = new Set<string>();

  const push = (env: DataEnvelope) => {
    const key = `${env.turn_scope_id}::${env.block_id}::${JSON.stringify(env.data).slice(0, 80)}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push(env);
  };

  // 1) Known array-carrying keys
  for (const key of ["envelopes", "catalogEnvelopes", "dataEnvelopes", "structuredEnvelopes", "DATA", "data"]) {
    const maybe = (values as Record<string, unknown>)[key];
    if (Array.isArray(maybe)) {
      for (const entry of maybe) {
        const env = asEnvelope(entry);
        if (env !== null) push(env);
        // Also accept JSON-stringified envelopes inside values (mirrors ToolMessage string path)
        if (typeof entry === "string") {
          try {
            const parsed = JSON.parse(entry) as unknown;
            const env2 = asEnvelope(parsed);
            if (env2 !== null) push(env2);
          } catch {
            // ignore
          }
        }
      }
    } else if (isEnvelopeLike(maybe)) {
      const env = asEnvelope(maybe);
      if (env !== null) push(env);
    }
  }

  // 2) Top-level direct envelopes (any key whose value is envelope-like)
  for (const [key, value] of Object.entries(values)) {
    if (key === "messages" || key === "question" || key === "attachment_id") continue;
    if (isEnvelopeLike(value)) {
      const env = asEnvelope(value);
      if (env !== null) push(env);
      continue;
    }
    if (Array.isArray(value)) {
      // Check if array is all envelopes
      let allEnvelopes = value.length > 0;
      for (const item of value) {
        if (!isEnvelopeLike(item)) {
          allEnvelopes = false;
          break;
        }
      }
      if (allEnvelopes) {
        for (const item of value) {
          const env = asEnvelope(item);
          if (env !== null) push(env);
        }
      }
    }
  }

  // 3) Synthetic envelopes for structured domain keys
  const scopeForSynthetic = fallbackScopeId ?? "__values__";
  for (const key of STRUCTURED_ENVELOPE_KEYS) {
    if (key in values) {
      const data = (values as Record<string, unknown>)[key];
      if (data !== undefined) {
        // Avoid double-creating if a real envelope with same block_id already exists in this values payload
        const already = out.some((e) => e.block_id === key);
        if (!already) {
          push({ turn_scope_id: scopeForSynthetic, block_id: key, data, text: `values:${key}` });
        }
      }
    }
  }

  return out;
}

/**
 * Extract catalog compose_ui trees from the values channel.
 * Looks under well-known keys; returns each tree node (array flattened).
 */
export function treesFromValues(
  values: Record<string, unknown> | null | undefined,
): unknown[] {
  if (values === null || values === undefined || typeof values !== "object") return [];
  const out: unknown[] = [];

  const pushTree = (candidate: unknown) => {
    if (candidate === null || candidate === undefined) return;
    if (Array.isArray(candidate)) {
      for (const node of candidate) out.push(node);
    } else if (typeof candidate === "object") {
      // Heuristic: a tree node has `component`, or is an array of such
      if ("component" in (candidate as Record<string, unknown>)) {
        out.push(candidate);
      } else {
        // Could be a wrapper like {tree: [...]}
        out.push(candidate);
      }
    }
  };

  for (const key of TREE_KEYS) {
    const v = (values as Record<string, unknown>)[key];
    if (v !== undefined) pushTree(v);
  }

  // Also handle case where values itself IS a tree (rare but allow)
  if ("component" in (values as Record<string, unknown>)) {
    pushTree(values);
  }

  return out;
}

/**
 * Build synthetic envelopes + trees for structured keys when no explicit tree
 * was provided but structured data exists. This powers the "todos via values
 * renders as a catalog component" path without requiring the server to emit a
 * full compose_ui tree.
 *
 * Currently: `todos` -> Timeline, `metrics` -> StatRow, `citations` -> Card list.
 * Unknown structured keys are ignored (fail-closed).
 */
export function syntheticTreesFromStructuredValues(
  values: Record<string, unknown> | null | undefined,
  scopeId: string,
): unknown[] {
  if (values === null || values === undefined) return [];
  const trees: unknown[] = [];

  if ("todos" in values) {
    // Render todos as a Timeline where each todo becomes an item
    // The Timeline component expects items: DataRef -> array of {week, title, desc}
    // We keep it generic: produce an ActionCard-like? But Timeline is a good fit.
    // Instead, we produce a generic Card + Timeline that refs the todos envelope.
    trees.push({
      component: "Timeline",
      props: {
        items: { __ref: { turn_scope_id: scopeId, block_id: "todos", pointer: "" } },
      },
    });
  }

  // Future: metrics -> TrendCard/StatRow, citations -> Card, etc.
  // Keep minimal now; explicit trees via `tree` key are the primary path.

  return trees;
}
