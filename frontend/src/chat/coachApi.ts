import type { Client } from "@langchain/langgraph-sdk";
import { LANGGRAPH_URL } from "@/lib/env";
import {
  RUN_ASSISTANT_ID,
  RUN_STREAM_PARAMS,
  THREAD_SELECT_FIELDS,
  type ResumePayload,
  type RunInput,
  type ThreadStatus,
} from "./coachProtocol";

/**
 * Every member-facing HTTP call, shaped byte-for-byte to what the perimeter
 * (`healthcare_rag/agent/perimeter.py`) allows. The only routes touched are
 * the allow-listed member routes; cron/assistant/store endpoints are never
 * called from this app.
 */

export class CoachApiError extends Error {
  readonly status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "CoachApiError";
    this.status = status;
  }
}

async function readDetail(response: Response): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (typeof payload === "object" && payload !== null && "detail" in payload) {
      const detail = (payload as Record<string, unknown>).detail;
      if (typeof detail === "string") return detail;
    }
  } catch {
    // fall through to the generic message
  }
  return `Request failed (${response.status})`;
}

async function fail(response: Response): Promise<never> {
  throw new CoachApiError(response.status, await readDetail(response));
}

/** A fetch bound to the Agent Server base URL with the member bearer stamped on. */
export type CoachFetch = (path: string, init?: RequestInit) => Promise<Response>;

export function createCoachFetch(
  getAccessToken: () => Promise<string | null>,
  baseUrl: string = LANGGRAPH_URL,
): CoachFetch {
  return async (path, init = {}) => {
    const headers = new Headers(init.headers);
    const token = await getAccessToken();
    if (token !== null) headers.set("Authorization", `Bearer ${token}`);
    return fetch(`${baseUrl}${path}`, { ...init, headers });
  };
}

export interface ThreadSummary {
  thread_id: string;
  status: ThreadStatus;
  updated_at: string;
}

export interface ThreadStateProjection {
  values: { messages?: unknown[] };
  interrupts: unknown[];
}

/** POST /threads — the member create envelope is EXACTLY the empty object. */
export async function createThread(fetcher: CoachFetch): Promise<ThreadSummary> {
  const response = await fetcher("/threads", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!response.ok) await fail(response);
  return (await response.json()) as ThreadSummary;
}

export interface SearchPageOptions {
  limit: number;
  offset: number;
  /** Ascending thread-id ordering is required for the erase snapshot pass. */
  sortByIdAsc?: boolean;
}

/** POST /threads/search — select-projection body only, keys within the allow-list. */
export async function searchThreads(
  fetcher: CoachFetch,
  options: SearchPageOptions,
): Promise<ThreadSummary[]> {
  const body: Record<string, unknown> = {
    select: [...THREAD_SELECT_FIELDS],
    limit: options.limit,
    offset: options.offset,
  };
  if (options.sortByIdAsc === true) {
    body.sort_by = "thread_id";
    body.sort_order = "asc";
  }
  const response = await fetcher("/threads/search", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) await fail(response);
  return (await response.json()) as ThreadSummary[];
}

/** GET /threads/{id} — status polling (idle|busy|interrupted|error). */
export async function getThread(fetcher: CoachFetch, threadId: string): Promise<ThreadSummary> {
  const response = await fetcher(`/threads/${threadId}`);
  if (!response.ok) await fail(response);
  return (await response.json()) as ThreadSummary;
}

/** DELETE /threads/{id} — bodyless. */
export async function deleteThread(fetcher: CoachFetch, threadId: string): Promise<void> {
  const response = await fetcher(`/threads/${threadId}`, { method: "DELETE" });
  if (!response.ok) await fail(response);
}

/** POST /threads/{id}/copy — bodyless; the server copies the LATEST state. */
export async function copyThread(fetcher: CoachFetch, threadId: string): Promise<ThreadSummary> {
  const response = await fetcher(`/threads/${threadId}/copy`, { method: "POST" });
  if (!response.ok) await fail(response);
  return (await response.json()) as ThreadSummary;
}

/** GET /threads/{id}/state — the perimeter projects this to `{values, interrupts}`. */
export async function getThreadState(
  fetcher: CoachFetch,
  threadId: string,
): Promise<ThreadStateProjection> {
  const response = await fetcher(`/threads/${threadId}/state`);
  if (!response.ok) await fail(response);
  return (await response.json()) as ThreadStateProjection;
}

export interface UploadResponseBody {
  stage?: string;
  detail?: string;
}

/** POST /coach/uploads — multipart {upload_id, thread_id, file} (client-generated uuid). */
export async function postUpload(
  fetcher: CoachFetch,
  upload: { uploadId: string; threadId: string; file: File },
): Promise<UploadResponseBody> {
  const form = new FormData();
  form.append("upload_id", upload.uploadId);
  form.append("thread_id", upload.threadId);
  form.append("file", upload.file, upload.file.name);
  const response = await fetcher("/coach/uploads", { method: "POST", body: form });
  const payload = (await response.json().catch(() => ({}))) as UploadResponseBody;
  if (!response.ok) {
    throw new CoachApiError(
      response.status,
      typeof payload.detail === "string" ? payload.detail : `Upload failed (${response.status})`,
    );
  }
  return payload;
}

/** GET /coach/uploads/{id}/status — `{stage}` (410/404 when expired/missing). */
export async function getUploadStatus(
  fetcher: CoachFetch,
  uploadId: string,
): Promise<UploadResponseBody> {
  const response = await fetcher(`/coach/uploads/${uploadId}/status`);
  const payload = (await response.json().catch(() => ({}))) as UploadResponseBody;
  if (!response.ok) {
    throw new CoachApiError(
      response.status,
      typeof payload.detail === "string" ? payload.detail : `Upload status failed (${response.status})`,
    );
  }
  return payload;
}

/** POST /coach/feedback — the message-targeted proxy shape, no extra keys. */
export async function postFeedback(
  fetcher: CoachFetch,
  feedback: { threadId: string; messageId: string; score: 1 | -1 },
): Promise<void> {
  const response = await fetcher("/coach/feedback", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      thread_id: feedback.threadId,
      message_id: feedback.messageId,
      score: feedback.score,
    }),
  });
  if (!response.ok) await fail(response);
}

/**
 * The SDK streaming surface. Updates-only via the fixed run envelope:
 * assistant "coach", stream_mode ["updates"], subgraphs off, non-resumable,
 * durability "exit", if_not_exists/multitask reject. `input` XOR `command`
 * rides the same envelope (the perimeter rejects anything else).
 */
export type CoachStreamClient = Pick<Client, "runs">;

export interface RunStreamPart {
  event: string;
  data: unknown;
}

export function streamRun(
  client: CoachStreamClient,
  threadId: string,
  payload: { input: RunInput } | { command: { resume: ResumePayload } },
): AsyncIterable<RunStreamPart> {
  return client.runs.stream(threadId, RUN_ASSISTANT_ID, {
    ...RUN_STREAM_PARAMS,
    ...payload,
  });
}
