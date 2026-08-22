import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { SUPABASE_URL, SUPABASE_ANON_KEY, supabaseConfigured } from "./env";

let singleton: SupabaseClient | null = null;

/**
 * Lazy Supabase browser client. Created on first use so a missing env var
 * surfaces as an actionable error at call time instead of an import-time
 * crash that would break builds and tests.
 */
export function getSupabase(): SupabaseClient {
  if (!supabaseConfigured()) {
    throw new Error(
      "Sign-in is not configured: set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY.",
    );
  }
  if (singleton === null) {
    singleton = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
  }
  return singleton;
}
