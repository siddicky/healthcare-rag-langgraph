import { describe, expect, it, vi } from "vitest";
import { bearerRequestHook, supabaseAccessToken } from "@/lib/langgraph";
import { LANGGRAPH_URL } from "@/lib/env";
import type { SupabaseClient } from "@supabase/supabase-js";

function fakeSupabase(
  getSession: () => Promise<unknown>,
  refreshSession?: () => Promise<unknown>,
): SupabaseClient {
  return {
    auth: { getSession, refreshSession },
  } as unknown as SupabaseClient;
}

describe("bearerRequestHook", () => {
  it("stamps the resolved token as the Authorization bearer", async () => {
    const hook = bearerRequestHook(() => Promise.resolve("token-123"));
    const init = await hook(new URL("http://localhost:2024/threads"), { method: "POST" });
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer token-123");
  });

  it("omits the header when there is no session token", async () => {
    const hook = bearerRequestHook(() => Promise.resolve(null));
    const init = await hook(new URL("http://localhost:2024/threads"), { method: "POST" });
    expect(new Headers(init.headers).get("Authorization")).toBeNull();
  });

  it("preserves existing headers while adding the bearer", async () => {
    const hook = bearerRequestHook(() => Promise.resolve("token-123"));
    const init = await hook(new URL("http://localhost:2024/threads"), {
      method: "POST",
      headers: { "content-type": "application/json" },
    });
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer token-123");
    expect(headers.get("content-type")).toBe("application/json");
  });
});

describe("supabaseAccessToken", () => {
  it("returns the current token when it is not near expiry", async () => {
    const client = fakeSupabase(() =>
      Promise.resolve({
        data: { session: { access_token: "fresh-token", expires_at: Math.floor(Date.now() / 1000) + 3600 } },
      }),
    );
    await expect(supabaseAccessToken(client)).resolves.toBe("fresh-token");
  });

  it("refreshes a near-expiry token before returning it", async () => {
    const refreshSession = vi.fn(() =>
      Promise.resolve({ data: { session: { access_token: "refreshed-token", expires_at: Math.floor(Date.now() / 1000) + 3600 } } }),
    );
    const client = fakeSupabase(
      () =>
        Promise.resolve({
          data: { session: { access_token: "stale-token", expires_at: Math.floor(Date.now() / 1000) - 10 } },
        }),
      refreshSession,
    );
    await expect(supabaseAccessToken(client)).resolves.toBe("refreshed-token");
    expect(refreshSession).toHaveBeenCalledTimes(1);
  });

  it("returns null without a session", async () => {
    const client = fakeSupabase(() => Promise.resolve({ data: { session: null } }));
    await expect(supabaseAccessToken(client)).resolves.toBeNull();
  });
});

describe("LANGGRAPH_URL default", () => {
  it("defaults to the local Agent Server", () => {
    expect(LANGGRAPH_URL).toBe("http://localhost:2024");
  });
});
