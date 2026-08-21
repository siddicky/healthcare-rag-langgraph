import { Client } from "@langchain/langgraph-sdk";
import type { SupabaseClient } from "@supabase/supabase-js";
import { LANGGRAPH_URL } from "./env";

/** Returns the current member access token, refreshing it when near expiry. */
export async function supabaseAccessToken(client: SupabaseClient): Promise<string | null> {
  const { data } = await client.auth.getSession();
  const session = data.session;
  if (!session) return null;
  const expiresAtMs = (session.expires_at ?? 0) * 1000;
  if (expiresAtMs - Date.now() < 60_000) {
    const { data: refreshed } = await client.auth.refreshSession();
    return refreshed.session?.access_token ?? session.access_token;
  }
  return session.access_token;
}

/**
 * SDK client factory for the Coach Agent Server.
 *
 * - Endpoint: NEXT_PUBLIC_LANGGRAPH_URL (default http://localhost:2024).
 * - Auth: a refresh-aware bearer injected per request via the async
 *   `onRequest` hook — the member Supabase token, never a platform key
 *   (apiKey is pinned to null so the SDK never auto-loads one client-side).
 */
export function bearerRequestHook(
  getAccessToken: () => Promise<string | null>,
): (url: URL, init: RequestInit) => Promise<RequestInit> {
  return async (_url, init) => {
    const token = await getAccessToken();
    const headers = new Headers(init.headers);
    if (token !== null) headers.set("Authorization", `Bearer ${token}`);
    return { ...init, headers };
  };
}

export function createCoachClient(
  getAccessToken: () => Promise<string | null>,
  apiUrl: string = LANGGRAPH_URL,
): Client {
  return new Client({
    apiUrl,
    apiKey: null,
    onRequest: bearerRequestHook(getAccessToken),
  });
}
