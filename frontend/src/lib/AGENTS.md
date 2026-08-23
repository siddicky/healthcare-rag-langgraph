<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-08-22 | Updated: 2026-08-22 -->

# src/lib

## Purpose
Cross-cutting client infrastructure: environment config (`env.ts`, `NEXT_PUBLIC_*`-only), the lazy Supabase browser client singleton (`supabase.ts`), and the LangGraph Agent Server SDK client factory with refresh-aware bearer injection (`langgraph.ts`). Everything the chat/auth layers need to reach the outside world is constructed here, then injected into `@/chat/coachClient.ts`'s `createBrowserDeps()`.

## Key Files
| File | Description |
|------|-------------|
| `env.ts` | Reads `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_LANGGRAPH_URL` (default `http://localhost:2024`); `supabaseConfigured()` guard |
| `supabase.ts` | `getSupabase()` — lazy singleton `SupabaseClient`; throws `"Sign-in is not configured: ..."` at CALL time (not import time) if env vars are missing, so a misconfigured deploy fails at first use rather than crashing the build |
| `langgraph.ts` | `supabaseAccessToken(client)` — returns the current access token, refreshing it if within 60s of expiry; `bearerRequestHook(getAccessToken)` — an async `onRequest` hook stamping `Authorization: Bearer <token>`; `createCoachClient(getAccessToken, apiUrl?)` — SDK `Client` factory with `apiKey: null` pinned (so the SDK never auto-loads a platform key client-side) |

## For AI Agents

### Working In This Directory
- `getSupabase()`'s lazy-throw-at-call-time pattern is intentional (see the comment in `supabase.ts`) — don't refactor it to throw at module load, since that would break builds/tests that never actually call it.
- `createCoachClient` always pins `apiKey: null` — never pass a real API key here; the member Supabase bearer (via `bearerRequestHook`) is the ONLY credential this app is allowed to send to the Agent Server, per the perimeter contract.
- `supabaseAccessToken`'s 60-second refresh-ahead window exists so a long-running stream doesn't get cut off mid-request by an expiring token — keep it comfortably larger than typical request latency if you tune it.

### Testing Requirements
- Covered indirectly through `src/chat/__tests__/` (which inject fakes rather than these real implementations) and `e2e/smoke.spec.ts`, which exercises the real Supabase-stub + SDK path end to end. No dedicated `__tests__/` unit tests exist for `env.ts` beyond what's asserted via `src/lib/__tests__/` if present — check there before assuming coverage gaps.

### Common Patterns
- Factory-with-injected-getter: both `createCoachFetch` (in `@/chat/coachApi.ts`) and `createCoachClient` here take a `getAccessToken: () => Promise<string | null>` closure rather than a client reference directly, so the token-refresh logic (`supabaseAccessToken`) is the single source of truth reused by both the plain-fetch and SDK-stream call paths.

## Dependencies

### Internal
- Consumed by `@/chat/coachClient.ts` (`createBrowserDeps`), `@/app/chat/page.tsx`, `@/components/auth/LoginForm.tsx`

### External
- `@supabase/supabase-js` (`createClient`, `SupabaseClient`)
- `@langchain/langgraph-sdk` (`Client`)

<!-- MANUAL: Notes added below this line are preserved on regeneration -->
