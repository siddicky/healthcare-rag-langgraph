/**
 * Client-side configuration. Only NEXT_PUBLIC_* names are read here — no
 * server secrets ever reach the browser bundle.
 */
export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
export const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

/** The Agent Server base URL the SDK factory targets (member bearer rides per-request). */
export const LANGGRAPH_URL = process.env.NEXT_PUBLIC_LANGGRAPH_URL ?? "http://localhost:2024";

export function supabaseConfigured(): boolean {
  return SUPABASE_URL !== "" && SUPABASE_ANON_KEY !== "";
}
