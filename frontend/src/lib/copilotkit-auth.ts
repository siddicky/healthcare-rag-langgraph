import type { SupabaseClient } from "@supabase/supabase-js";
import { supabaseAccessToken } from "./langgraph";
import { getSupabase } from "./supabase";

let authorization: string | null = null;

export async function refreshCopilotKitAuthorization(
  client: SupabaseClient = getSupabase(),
): Promise<void> {
  const token = await supabaseAccessToken(client);
  authorization = token === null ? null : `Bearer ${token}`;
}

export function copilotKitHeaders(): Record<string, string> {
  return authorization === null ? {} : { Authorization: authorization };
}
