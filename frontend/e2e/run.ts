import { readFileSync } from "node:fs";
import path from "node:path";
import { request, type APIRequestContext } from "@playwright/test";

export interface RunIdentity {
  email: string;
  password: string;
  token: string;
  user_id: string;
}

export interface Runfile {
  ready: boolean;
  dep_url: string;
  server_url: string;
  /** Member stream perimeter of the main server + baked frontend ("v1"|"v2"). */
  perimeter?: string;
  next_public_perimeter?: string;
  /** Whether the frontend build enabled the history (TimeTravel) + branching UI. */
  history_branch_ui?: boolean;
  /** Second Agent Server running the flipped perimeter (always booted). */
  alt_server_url?: string;
  alt_perimeter?: string;
  frontend_url: string;
  u1: RunIdentity;
  u2: RunIdentity;
  internal: { api_key: string; token: string };
  anon_key: string;
}

export interface DataEnvelope {
  turn_scope_id: string;
  block_id: string;
  data: Record<string, unknown>;
  text?: string;
}

export function readRun(): Runfile {
  const runfile = process.env.COACH_E2E_RUNFILE ?? path.join(__dirname, ".tmp", "run.json");
  return JSON.parse(readFileSync(runfile, "utf8")) as Runfile;
}

export async function memberApi(
  run: Runfile,
  token: string,
  baseUrl?: string,
): Promise<APIRequestContext> {
  return request.newContext({
    baseURL: baseUrl ?? run.server_url,
    extraHTTPHeaders: { authorization: `Bearer ${token}` },
  });
}

/** Base URL of the run's server running the requested perimeter, or null. */
export function serverWithPerimeter(run: Runfile, perimeter: "v1" | "v2"): string | null {
  if (run.perimeter === perimeter) return run.server_url;
  if (run.alt_perimeter === perimeter && run.alt_server_url) return run.alt_server_url;
  return null;
}

/**
 * True when the server the FRONTEND points at admits the v2-native
 * ThreadStream transport (POST /threads/{id}/stream/events SSE +
 * POST /threads/{id}/commands — the surface `@langchain/react` useStream
 * submits through). The probe posts an INVALID subscription body: an
 * admitted route answers with an immediate protocol 400 (channels
 * required), while a perimeter denial is 403 — a valid body would open a
 * real SSE stream that (correctly) never closes and hang the probe.
 */
export async function threadStreamAdmitted(api: APIRequestContext): Promise<boolean> {
  const create = await api.post("/threads", { data: {} });
  if (create.status() !== 200) throw new Error(`probe thread create failed: ${create.status()}`);
  const { thread_id: threadId } = (await create.json()) as { thread_id: string };
  try {
    const probe = await api.post(`/threads/${threadId}/stream/events`, {
      data: {},
    });
    try {
      await probe.dispose();
    } catch {
      // response already consumed
    }
    return probe.status() !== 403;
  } finally {
    await api.delete(`/threads/${threadId}`);
  }
}

export const THREAD_STREAM_SKIP_REASON =
  "member frontend chats through the @langchain/react useStream ThreadStream transport (POST /threads/{id}/stream/events SSE + POST /threads/{id}/commands), which the member perimeter (v1 and v2) does not admit — see docs/safety.md 'Member stream perimeter v2 (useStream)'. UI specs auto-activate once a reviewed perimeter revision admits the transport.";

export const HISTORY_BRANCH_UI_SKIP_REASON =
  "history/branching UI disabled by default (NEXT_PUBLIC_COACH_HISTORY_BRANCH_UI≠1)";

export function historyBranchUiEnabled(run: Runfile): boolean {
  return run.history_branch_ui === true;
}

export function internalHeaders(run: Runfile, owner: string): Record<string, string> {
  return {
    "x-api-key": run.internal.api_key,
    "x-internal-token": run.internal.token,
    "x-internal-owner": owner,
    "content-type": "application/json",
  };
}

export async function threadState(api: APIRequestContext, threadId: string): Promise<{ values: { messages?: unknown[] } }> {
  const response = await api.get(`/threads/${threadId}/state`);
  if (response.status() !== 200) {
    throw new Error(`thread state ${response.status()}: ${await response.text()}`);
  }
  return (await response.json()) as { values: { messages?: unknown[] } };
}

/** Every DATA envelope embedded in the thread's message channel. */
export function envelopesOf(state: { values: { messages?: unknown[] } }): DataEnvelope[] {
  const messages = state.values.messages ?? [];
  const found: DataEnvelope[] = [];
  for (const message of messages) {
    if (typeof message !== "object" || message === null) continue;
    const content = (message as { content?: unknown }).content;
    if (typeof content !== "string" || !content.includes("block_id")) continue;
    try {
      const parsed = JSON.parse(content) as Record<string, unknown>;
      if (
        typeof parsed.block_id === "string" &&
        typeof parsed.turn_scope_id === "string" &&
        typeof parsed.data === "object" &&
        parsed.data !== null
      ) {
        found.push({
          turn_scope_id: parsed.turn_scope_id,
          block_id: parsed.block_id,
          data: parsed.data as Record<string, unknown>,
          text: typeof parsed.text === "string" ? parsed.text : undefined,
        });
      }
    } catch {
      // not an envelope
    }
  }
  return found;
}

/** Date helpers mirroring server.py so month expectations are derived, not hardcoded. */
function mondayBased(day: Date): number {
  return (day.getDay() + 6) % 7;
}

function addDays(day: Date, count: number): Date {
  const next = new Date(day.getFullYear(), day.getMonth(), day.getDate());
  next.setDate(next.getDate() + count);
  return next;
}

export function nextWeekday(from: Date, target: number): Date {
  const delta = (((target - mondayBased(from)) % 7) + 7) % 7 || 7;
  return addDays(from, delta);
}

export function nextFriday(): Date {
  return nextWeekday(new Date(), 4);
}

export function monthOf(day: Date): string {
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}`;
}
