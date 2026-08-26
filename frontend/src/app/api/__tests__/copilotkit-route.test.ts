// @vitest-environment node
/**
 * Route tests for the `/api/copilotkit` runtime handler
 * (frontend/src/lib/copilotkit-runtime.ts, mounted by
 * frontend/src/app/api/copilotkit/[[...slug]]/route.ts).
 *
 * The upstream LangGraph deployment is a scripted `fetch` stub (the LangGraph
 * SDK resolves `fetch` from global scope at call time, so vi.stubGlobal
 * intercepts every upstream call). Frames in the scripted SSE stream follow
 * the langgraph legacy protocol measured in
 * .omo/evidence/task-2-copilotkit-generative-ui.md.
 *
 * PHI posture: canaries planted in the fake bearer and the fake run body must
 * NEVER appear in captured console output.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

const BASE = "http://localhost:3000/api/copilotkit";
const BEARER_TOKEN = "stub-member-bearer-canary";
const AUTH_HEADER = `Bearer ${BEARER_TOKEN}`;
const BODY_CANARY = "CANARY-PHI-BODY-9f2c1a";
const SSE_MARKER = "PROXY-SSE-MARKER-d41f";

type RecordedCall = { method: string; path: string; authorization: string | null };

const upstreamCalls: RecordedCall[] = [];
let upstreamThreadVisible = true;

const ASSISTANT = {
  assistant_id: "assistant-coach",
  graph_id: "coach",
  name: "coach",
  config: {},
  metadata: {},
  created_at: "2026-08-25T00:00:00Z",
  updated_at: "2026-08-25T00:00:00Z",
  version: 2,
};

function threadBody(threadId: string) {
  return {
    thread_id: threadId,
    metadata: {},
    values: { messages: [] },
    tasks: [],
    interrupts: [],
    next: [],
    checkpoint: { checkpoint_id: "ckpt-1", thread_id: threadId },
    created_at: "2026-08-25T00:00:00Z",
    updated_at: "2026-08-25T00:00:00Z",
  };
}

function threadIdFromPath(pathname: string): string {
  return pathname.split("/")[2] ?? "unknown-thread";
}

function safeJsonBody(body: BodyInit | null | undefined): Record<string, unknown> {
  try {
    return JSON.parse(String(body)) as Record<string, unknown>;
  } catch {
    return {};
  }
}

const SSE_FRAMES = [
  `event: metadata`,
  `data: {"run_id":"stub-run-1"}`,
  ``,
  `event: events`,
  `data: ${JSON.stringify({
    event: "on_chat_model_stream",
    metadata: { langgraph_node: "coach", run_id: "stub-run-1" },
    data: {
      chunk: {
        id: "ai-1",
        type: "AIMessageChunk",
        content: SSE_MARKER,
        tool_call_chunks: [],
        response_metadata: {},
        usage_metadata: null,
      },
    },
  })}`,
  ``,
  `event: events`,
  `data: ${JSON.stringify({
    event: "on_chat_model_stream",
    metadata: { langgraph_node: "coach", run_id: "stub-run-1" },
    data: {
      chunk: {
        id: "ai-1",
        type: "AIMessageChunk",
        content: "",
        tool_call_chunks: [],
        response_metadata: { finish_reason: "stop" },
        usage_metadata: null,
      },
    },
  })}`,
  ``,
  `event: events`,
  `data: ${JSON.stringify({
    event: "on_chat_model_end",
    metadata: { langgraph_node: "coach", run_id: "stub-run-1" },
    data: { output: { id: "ai-1", type: "ai", content: "", tool_calls: [] } },
  })}`,
  ``,
].join("\n");

function stubUpstream(url: URL, init: RequestInit): Response {
  const headers = init.headers;
  const authorization =
    headers instanceof Headers
      ? headers.get("authorization")
      : Array.isArray(headers)
        ? (headers.find(([k]) => k.toLowerCase() === "authorization")?.[1] ?? null)
        : (headers as Record<string, string> | undefined)?.authorization ?? null;
  upstreamCalls.push({ method: init.method ?? "GET", path: url.pathname, authorization });

  const json = (body: unknown) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });

  if (url.pathname === "/assistants/search") return json([ASSISTANT]);
  if (/^\/threads\/[^/]+$/.test(url.pathname) && init.method === "GET")
    return upstreamThreadVisible
      ? json(threadBody(threadIdFromPath(url.pathname)))
      : new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
  if (url.pathname === "/threads" && init.method === "POST")
    return json(threadBody(String(safeJsonBody(init.body).thread_id)));
  if (/^\/threads\/[^/]+\/state$/.test(url.pathname))
    return json({ ...threadBody(threadIdFromPath(url.pathname)), next: [] });
  if (/^\/assistants\/[^/]+\/schemas$/.test(url.pathname)) return new Response("", { status: 501 });
  if (/^\/assistants\/[^/]+\/graph$/.test(url.pathname)) return json({ nodes: [], edges: [] });
  if (/^\/threads\/[^/]+\/runs\/stream$/.test(url.pathname))
    return new Response(new TextEncoder().encode(SSE_FRAMES), {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
  return json({});
}

let route: typeof import("@/lib/copilotkit-runtime");
const logSpy = {
  log: vi.spyOn(console, "log"),
  info: vi.spyOn(console, "info"),
  warn: vi.spyOn(console, "warn"),
  error: vi.spyOn(console, "error"),
  debug: vi.spyOn(console, "debug"),
};

beforeAll(async () => {
  process.env.LANGGRAPH_DEPLOYMENT_URL = "http://stub-upstream.invalid";
  vi.stubGlobal(
    "fetch",
    vi.fn((input: URL | RequestInfo, init?: RequestInit) => {
      const url =
        input instanceof Request ? new URL(input.url) : new URL(String(input));
      const requestInit: RequestInit =
        input instanceof Request
          ? { method: input.method, headers: input.headers, body: input.body }
          : (init ?? {});
      return Promise.resolve(stubUpstream(url, requestInit));
    }),
  );
  route = await import("@/lib/copilotkit-runtime");
});

afterEach(() => {
  for (const spy of Object.values(logSpy)) spy.mockClear();
});

function request(path: string, method: string, headers?: Record<string, string>, body?: string): Request {
  return new Request(`${BASE}${path}`, { method, headers, body });
}

describe("bearer gate", () => {
  it.each(["GET", "POST", "PATCH", "DELETE"] as const)("%s without a bearer → 401", async (method) => {
    const response = await route[method](request("/info", method));
    expect(response.status).toBe(401);
  });

  it.each([
    ["Token abc123"],
    ["Bearer"],
    ["Bearer   "],
    ["basic dXNlcjpwYXNz"],
    [""],
  ])("malformed Authorization %j → 401", async (header) => {
    const response = await route.GET(request("/info", "GET", { authorization: header }));
    expect(response.status).toBe(401);
  });
});

describe("GET /info with bearer", () => {
  it("returns 200 advertising the coach agent", async () => {
    const response = await route.GET(request("/info", "GET", { authorization: AUTH_HEADER }));
    expect(response.status).toBe(200);
    const info = (await response.json()) as { agents: Record<string, { name: string }> };
    expect(info.agents.coach?.name).toBe("coach");
  });
});

describe("POST /agent/coach/run proxies an SSE stream", () => {
  it("streams AG-UI events when the upstream schemas endpoint is unimplemented", async () => {
    const runInput = {
      threadId: crypto.randomUUID(),
      runId: crypto.randomUUID(),
      state: { question: BODY_CANARY },
      messages: [{ id: crypto.randomUUID(), role: "user", content: BODY_CANARY }],
      tools: [],
      context: [],
      forwardedProps: {},
    };
    const response = await route.POST(
      request("/agent/coach/run", "POST", { authorization: AUTH_HEADER }, JSON.stringify(runInput)),
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/event-stream");

    const text = await response.text();
    expect(text).toContain(SSE_MARKER);
    expect(text).toContain("TEXT_MESSAGE_CONTENT");
    expect(text).toContain("RUN_FINISHED");

    // Default forwarding carries the member bearer to the LangGraph server.
    const streamCall = upstreamCalls.find((c) => c.path.endsWith("/runs/stream"));
    expect(streamCall?.authorization).toBe(AUTH_HEADER);
  });
});

describe("thread ownership gate", () => {
  it("answers the runtime thread listing with an empty page (no cross-member leak)", async () => {
    const response = await route.GET(request("/threads", "GET", { authorization: AUTH_HEADER }));
    expect(response.status).toBe(200);
    const body = (await response.json()) as { threads: unknown[]; nextCursor: unknown };
    expect(body.threads).toEqual([]);
    expect(body.nextCursor).toBeNull();
  });

  it("answers 404 for a thread read the bearer cannot read upstream", async () => {
    upstreamThreadVisible = false;
    try {
      const response = await route.GET(
        request(`/threads/${crypto.randomUUID()}/messages`, "GET", { authorization: AUTH_HEADER }),
      );
      expect(response.status).toBe(404);
    } finally {
      upstreamThreadVisible = true;
    }
  });

  it("answers 404 for a stop on a thread the bearer cannot read upstream", async () => {
    upstreamThreadVisible = false;
    try {
      const response = await route.POST(
        request(`/agent/coach/stop/${crypto.randomUUID()}`, "POST", { authorization: AUTH_HEADER }, "{}"),
      );
      expect(response.status).toBe(404);
    } finally {
      upstreamThreadVisible = true;
    }
  });

  it("answers 404 for a connect on a thread the bearer cannot read upstream", async () => {
    upstreamThreadVisible = false;
    try {
      const response = await route.POST(
        request(
          "/agent/coach/connect",
          "POST",
          { authorization: AUTH_HEADER },
          JSON.stringify({ threadId: crypto.randomUUID(), runId: crypto.randomUUID() }),
        ),
      );
      expect(response.status).toBe(404);
    } finally {
      upstreamThreadVisible = true;
    }
  });

  it("lets an owned thread read through after the upstream probe passes", async () => {
    const response = await route.GET(
      request(`/threads/${crypto.randomUUID()}/messages`, "GET", { authorization: AUTH_HEADER }),
    );
    expect(response.status).toBeLessThan(500);
    expect(
      upstreamCalls.some((call) => /^\/threads\/[0-9a-f-]{36}$/.test(call.path)),
    ).toBe(true);
  });
});

describe("PHI posture of logs", () => {
  it("captured logs contain no bearer or body substrings", async () => {
    await route.GET(request("/info", "GET", { authorization: AUTH_HEADER }));
    await route.POST(
      request(
        "/agent/coach/run",
        "POST",
        { authorization: AUTH_HEADER },
        JSON.stringify({
          threadId: crypto.randomUUID(),
          runId: crypto.randomUUID(),
          state: { question: BODY_CANARY },
          messages: [{ id: crypto.randomUUID(), role: "user", content: BODY_CANARY }],
          tools: [],
          context: [],
          forwardedProps: {},
        }),
      ),
    );
    await route.POST(request("/agent/coach/stop/x", "POST", { authorization: AUTH_HEADER }, "{}"));

    const captured = Object.values(logSpy)
      .flatMap((spy) => spy.mock.calls.map((callArgs) => callArgs.map(String).join(" ")))
      .join("\n");
    expect(captured).not.toContain(BEARER_TOKEN);
    expect(captured).not.toContain(BODY_CANARY);
  });
});
