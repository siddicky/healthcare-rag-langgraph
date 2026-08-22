import { z } from "zod";

/**
 * The ONE data-ref grammar shared by the frontend zod schemas, the hydrator and
 * the backend composition model (F1 asserts grammar equality later):
 *
 *   {__ref: {turn_scope_id, block_id, pointer}}
 *
 * `pointer` is an RFC 6901 JSON path into the envelope's `data` field.
 * Fact-bearing catalog props accept ONLY this object shape — literals are
 * rejected at the wire-schema boundary.
 */
export const DataRefSchema = z.object({
  __ref: z.object({
    turn_scope_id: z.string(),
    block_id: z.string(),
    pointer: z.string(),
  }),
});

export type DataRef = z.infer<typeof DataRefSchema>;

export function isDataRef(value: unknown): value is DataRef {
  return DataRefSchema.safeParse(value).success;
}
