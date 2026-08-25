import { describe, expect, it, vi } from "vitest";
import { Client } from "@langchain/langgraph-sdk";
import {
  cancelRun,
  copyThread,
  createThread,
  deleteThread,
  getThreadHistory,
  getThreadState,
  getUploadStatus,
  joinRun,
  postFeedback,
  postUpload,
  searchThreads,
  streamRun,
} from "@/chat/coachApi";
import {
  CANCEL_PATH,
  getRunStreamParams,
  HISTORY_PATH,
  JOIN_PATH,
  JOIN_STREAM_PATH,
  RUN_STREAM_PARAMS,
  RUN_STREAM_PARAMS_V2,
  SENTINEL_QUESTION,
  STATE_VALUE_KEYS,
  THREAD_SELECT_FIELDS,
} from "@/chat/coachProtocol";

/** Captures method/path/body of every request a CoachFetch issues. */
type Captured = { method: string; path: string; body: string | FormData | null; contentType: string | null };

function captureFetch(respond: (capture: Captured) => unknown = () => ({})) {
  const calls: Captured[] = [];
  const fetcher = vi.fn(async (path: string, init?: RequestInit): Promise<Response> => {
    const rawBody = init?.body;
    const body =
      typeof rawBody === "string" ? rawBody : rawBody instanceof FormData ? rawBody : null;
    const headers = new Headers(init?.headers);
    calls.push({
      method: init?.method ?? "GET",
      path,
      body,
      contentType: headers.get("content-type"),
    });
    const payload = respond(calls[calls.length - 1] as Captured);
    return new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } });
  });
  return { fetcher, calls };
}

describe("perimeter-exact request bodies", () => {
  it("creates threads with EXACTLY the empty object body", async () => {
    const { fetcher, calls } = captureFetch(() => ({ thread_id: "t", status: "idle", updated_at: "x" }));
    await createThread(fetcher);
    expect(calls[0]?.body).toBe("{}");
  });

  it("searches with only select/limit/offset (and the five allowed fields)", async () => {
    const { fetcher, calls } = captureFetch(() => []);
    await searchThreads(fetcher, { limit: 50, offset: 0 });
    const body = JSON.parse(typeof calls[0]?.body === "string" ? calls[0].body : "{}") as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(["limit", "offset", "select"]);
    expect(body.select).toEqual([...THREAD_SELECT_FIELDS]);
  });

  it("uses the thread_id-asc sort only when requested", async () => {
    const { fetcher, calls } = captureFetch(() => []);
    await searchThreads(fetcher, { limit: 100, offset: 0, sortByIdAsc: true });
    const body = JSON.parse(typeof calls[0]?.body === "string" ? calls[0].body : "{}") as Record<string, unknown>;
    expect(body.sort_by).toBe("thread_id");
    expect(body.sort_order).toBe("asc");
  });

  it("copies bodyless (no body, no content-type)", async () => {
    const { fetcher, calls } = captureFetch(() => ({ thread_id: "t" }));
    await copyThread(fetcher, "11111111-1111-4111-8111-111111111111");
    expect(calls[0]?.body).toBeNull();
    expect(calls[0]?.contentType).toBeNull();
  });

  it("deletes bodyless", async () => {
    const { fetcher, calls } = captureFetch(() => ({}));
    await deleteThread(fetcher, "11111111-1111-4111-8111-111111111111");
    expect(calls[0]?.method).toBe("DELETE");
    expect(calls[0]?.body).toBeNull();
  });

  it("posts feedback with exactly the proxy shape {thread_id, message_id, score}", async () => {
    const { fetcher, calls } = captureFetch(() => ({ ok: true }));
    await postFeedback(fetcher, { threadId: "t1", messageId: "m1", score: 1 });
    const body = JSON.parse(typeof calls[0]?.body === "string" ? calls[0].body : "{}") as Record<string, unknown>;
    expect(body).toEqual({ thread_id: "t1", message_id: "m1", score: 1 });
  });

  it("reads the projected state as {values, interrupts}", async () => {
    const { fetcher } = captureFetch(() => ({ values: { messages: [] }, interrupts: [] }));
    const state = await getThreadState(fetcher, "11111111-1111-4111-8111-111111111111");
    expect(state.values).toEqual({ messages: [] });
    expect(state.interrupts).toEqual([]);
  });

  it("reads thread history on the v2 bodyless route", async () => {
    const { fetcher, calls } = captureFetch(() => [{ checkpoint_id: "checkpoint-1" }]);
    const history = await getThreadHistory(fetcher, "11111111-1111-4111-8111-111111111111");
    expect(history).toEqual([{ checkpoint_id: "checkpoint-1" }]);
    expect(calls[0]).toMatchObject({
      method: "GET",
      path: "/threads/11111111-1111-4111-8111-111111111111/history",
      body: null,
    });
  });

  it("joins and cancels runs on bodyless v2 routes", async () => {
    const { fetcher, calls } = captureFetch(() => ({ status: "success" }));
    const threadId = "11111111-1111-4111-8111-111111111111";
    const runId = "22222222-2222-4222-8222-222222222222";

    await joinRun(fetcher, threadId, runId);
    await cancelRun(fetcher, threadId, runId);

    expect(calls).toMatchObject([
      { method: "GET", path: `/threads/${threadId}/runs/${runId}/join`, body: null },
      { method: "POST", path: `/threads/${threadId}/runs/${runId}/cancel`, body: null },
    ]);
  });

  it("uploads multipart with upload_id, thread_id and one file", async () => {
    const { fetcher, calls } = captureFetch(() => ({ stage: "done" }));
    const file = new File(["%PDF-1.4 fake"], "form.pdf", { type: "application/pdf" });
    await postUpload(fetcher, {
      uploadId: "00000000-0000-4000-8000-000000000001",
      threadId: "11111111-1111-4111-8111-111111111111",
      file,
    });
    const capture = calls[0];
    expect(capture?.method).toBe("POST");
    expect(capture?.path).toBe("/coach/uploads");
    const form = capture?.body;
    expect(form instanceof FormData).toBe(true);
    if (form instanceof FormData) {
      expect(form.get("upload_id")).toBe("00000000-0000-4000-8000-000000000001");
      expect(form.get("thread_id")).toBe("11111111-1111-4111-8111-111111111111");
      const sent = form.get("file");
      expect(sent instanceof File).toBe(true);
      expect(sent instanceof File && sent.name).toBe("form.pdf");
    }
  });

  it("polls upload status on the allow-listed route", async () => {
    const { fetcher, calls } = captureFetch(() => ({ stage: "scanning" }));
    const status = await getUploadStatus(fetcher, "abc-123");
    expect(status.stage).toBe("scanning");
    expect(calls[0]?.path).toBe("/coach/uploads/abc-123/status");
    expect(calls[0]?.method).toBe("GET");
    expect(calls[0]?.body).toBeNull();
  });
});

describe("SDK run stream envelope", () => {
  function sseResponse(): Response {
    const body = [
      'event: metadata\ndata: {"run_id":"r1"}\n\n',
      'event: updates\ndata: {"finalize_coach":{"messages":[]}}\n\n',
    ].join("");
    return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
  }

  async function captureSdkBody(
    payload: { input: { question: string; attachment_id?: string } } | { command: { resume: { accept: boolean } } },
  ): Promise<Record<string, unknown>> {
    const bodies: string[] = [];
    const client = new Client({
      apiUrl: "http://localhost:9999",
      apiKey: null,
      callerOptions: {
        fetch: (async () => sseResponse()) as typeof fetch,
      },
      onRequest: async (_url, init) => {
        if (typeof init.body === "string") bodies.push(init.body);
        return init;
      },
    });
    for await (const _part of streamRun(client, "11111111-1111-4111-8111-111111111111", payload)) {
      void _part;
    }
    expect(bodies.length).toBeGreaterThan(0);
    return JSON.parse(bodies[0] as string) as Record<string, unknown>;
  }

  it("sends the EXACT fixed envelope for a new-turn input", async () => {
    const body = await captureSdkBody({ input: { question: "hello" } });
    expect(body).toEqual({
      assistant_id: "coach",
      stream_mode: ["updates"],
      stream_subgraphs: false,
      stream_resumable: false,
      durability: "exit",
      if_not_exists: "reject",
      multitask_strategy: "reject",
      input: { question: "hello" },
    });
  });

  it("sends attachment turns with the EXACT sentinel question", async () => {
    const body = await captureSdkBody({
      input: { question: SENTINEL_QUESTION, attachment_id: "00000000-0000-4000-8000-000000000001" },
    });
    expect(body.input).toEqual({
      question: "Please review this document.",
      attachment_id: "00000000-0000-4000-8000-000000000001",
    });
  });

  it("sends the unified resume command with command instead of input", async () => {
    const body = await captureSdkBody({ command: { resume: { accept: true } } });
    expect(Object.keys(body).sort()).toEqual(
      [
        "assistant_id",
        "command",
        "durability",
        "if_not_exists",
        "multitask_strategy",
        "stream_mode",
        "stream_resumable",
        "stream_subgraphs",
      ].sort(),
    );
    expect(body.command).toEqual({ resume: { accept: true } });
  });
});

describe("protocol constants", () => {
  it("pins the sentinel question byte-for-byte", () => {
    expect(SENTINEL_QUESTION).toBe("Please review this document.");
    expect(SENTINEL_QUESTION.endsWith(".")).toBe(true);
  });

  it("reads only allow-listed channels from projected state", () => {
    expect(STATE_VALUE_KEYS).toEqual(["messages"]);
  });

  it("selects the v2 run stream contract only for the explicit public env flip", () => {
    vi.stubEnv("NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER", "v2");
    expect(getRunStreamParams()).toBe(RUN_STREAM_PARAMS_V2);

    vi.stubEnv("NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER", "v1");
    expect(getRunStreamParams()).toBe(RUN_STREAM_PARAMS);
    vi.unstubAllEnvs();
  });

  it("pins exact v2 history, join, join-stream, and cancel route shapes", () => {
    const threadId = "11111111-1111-4111-8111-111111111111";
    const runId = "22222222-2222-4222-8222-222222222222";

    expect(HISTORY_PATH.test(`/threads/${threadId}/history`)).toBe(true);
    expect(JOIN_PATH.test(`/threads/${threadId}/runs/${runId}/join`)).toBe(true);
    expect(JOIN_STREAM_PATH.test(`/threads/${threadId}/runs/${runId}/join/stream`)).toBe(true);
    expect(CANCEL_PATH.test(`/threads/${threadId}/runs/${runId}/cancel`)).toBe(true);
    expect(JOIN_PATH.test(`/threads/${threadId}/runs/${runId}/join/stream`)).toBe(false);
  });
});
