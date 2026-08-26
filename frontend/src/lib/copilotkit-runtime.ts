/**
 * CopilotKit v2 runtime handler for `/api/copilotkit` (multi-route mode).
 *
 * Proxies the standard agent-server protocol to the coach graph deployment:
 * `CopilotRuntime` + `LangGraphAgent({graphId: "coach"})` + `InMemoryAgentRunner`.
 * The `onRequest` hook is the ONLY every-route gate: it rejects any request
 * whose Authorization header is missing or malformed with 401 BEFORE routing,
 * and otherwise passes the request through untouched — the runtime's default
 * header forwarding carries the member bearer to the LangGraph server, where
 * the member perimeter enforces identity and thread ownership.
 *
 * PHI posture (docs/safety.md): logs carry method/path/status + resolved
 * principal id ONLY — never bodies, never bearer tokens, never query values.
 *
 * Guardrails: NO `intelligence` option / license key / cloud call ($0 spend),
 * no CORS config (Next is same-origin by construction), no `mode:
 * "single-route"` (the provider pairs with `useSingleEndpoint={false}`), and
 * no body-buffering middleware (SSE must stream).
 */
import "@/lib/copilotkit-telemetry-off"; // MUST stay first: telemetry reads env at import time.
import {
  CopilotRuntime,
  InMemoryAgentRunner,
  createCopilotRuntimeHandler,
} from "@copilotkit/runtime/v2";
import type { CopilotRuntimeFetchHandler } from "@copilotkit/runtime/v2";
import { LangGraphAgent } from "@copilotkit/runtime/langgraph";
import { langgraphDeploymentUrl } from "@/lib/env.server";

const BASE_PATH = "/api/copilotkit";
const BEARER_PATTERN = /^Bearer\s+\S+$/i;
const THREAD_ID_PATTERN = /\/threads\/([0-9a-fA-F-]{36})(?:\/|$)/;
const STOP_PATTERN = /\/agent\/coach\/stop\/([0-9a-fA-F-]{36})$/;

function unauthorized(): Response {
  return Response.json({ error: "unauthorized" }, { status: 401 });
}

function notFound(): Response {
  // Same shape as a perimeter denial: never reveal whether a thread exists.
  return Response.json({ detail: "Not Found" }, { status: 404 });
}

/**
 * Thread id addressed by the request URL, when any.
 */
function urlAddressedThreadId(request: Request): string | null {
  const { pathname } = new URL(request.url);
  return (
    THREAD_ID_PATTERN.exec(pathname)?.[1] ?? STOP_PATTERN.exec(pathname)?.[1] ?? null
  );
}

/**
 * Run/connect address the thread through their JSON body. Every frontend
 * flow creates the thread through the direct member surface BEFORE its
 * first run (`ensureThread`), so a body threadId that does not exist
 * upstream is never legitimate here — probing it keeps erased/foreign
 * threads unrecoverable instead of letting the adapter re-create them.
 */
async function bodyThreadId(request: Request): Promise<string | null> {
  if (!/\/agent\/coach\/(run|connect)$/.test(new URL(request.url).pathname)) {
    return null;
  }
  try {
    const body = (await request.clone().json()) as { threadId?: unknown };
    return typeof body?.threadId === "string" ? body.threadId : null;
  } catch {
    return null;
  }
}

/**
 * The runtime serves thread reads and stop from its own memory with no
 * notion of ownership — without this probe ANY bearer could read another
 * member's messages or abort their active run through the proxy. Ownership
 * is delegated to the member perimeter on the loopback deployment: a thread
 * that does not exist for THIS bearer (403/404 upstream) is answered 404
 * here before the runtime ever sees it.
 */
async function authorizeThreadAccess(request: Request): Promise<void> {
  const { pathname } = new URL(request.url);
  if (pathname === `${BASE_PATH}/threads`) throw notFound();
  const threadId = urlAddressedThreadId(request) ?? (await bodyThreadId(request));
  if (threadId === null) return;
  const upstream = await fetch(
    `${langgraphDeploymentUrl()}/threads/${threadId}`,
    { method: "GET", headers: { authorization: request.headers.get("authorization") ?? "" } },
  );
  if (!upstream.ok) throw notFound();
}

/** Unverified JWT `sub`, for logging only — enforcement happens downstream. */
function principalId(request: Request): string {
  try {
    const token = request.headers.get("authorization")?.trim().split(/\s+/)[1] ?? "";
    const [, payload] = token.split(".");
    if (!payload) return "unknown";
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    const sub = JSON.parse(json)?.sub;
    return typeof sub === "string" ? sub : "unknown";
  } catch {
    return "unknown";
  }
}

function logRequest(method: string, path: string, status: number, principal: string): void {
  console.info(`[copilotkit] ${method} ${path} ${status} principal=${principal}`);
}

/**
 * Node >=22 undici rejects Response bodies whose chunks are not Uint8Array,
 * and @ag-ui/encoder emits strings into the runtime's SSE stream. Re-wrap SSE
 * responses with a pass-through that encodes chunks to bytes — chunks flow
 * straight through, nothing buffers, so SSE stays streaming.
 */
function asByteStream(response: Response): Response {
  const contentType = response.headers.get("content-type") ?? "";
  if (!response.body || !contentType.includes("text/event-stream")) return response;
  const encoder = new TextEncoder();
  const bytes = response.body.pipeThrough(
    new TransformStream({
      transform(chunk, controller) {
        controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      },
    }),
  );
  return new Response(bytes, { status: response.status, headers: response.headers });
}

let handlerPromise: Promise<CopilotRuntimeFetchHandler> | null = null;

function getHandler(): Promise<CopilotRuntimeFetchHandler> {
  handlerPromise ??= (async () => {
    const runtime = new CopilotRuntime({
      agents: {
        coach: new LangGraphAgent({
          deploymentUrl: langgraphDeploymentUrl(),
          graphId: "coach",
        }),
      },
      runner: new InMemoryAgentRunner(),
    });
    return createCopilotRuntimeHandler({
      runtime,
      basePath: BASE_PATH,
      hooks: {
        // The only every-route gate: reject before routing/dispatch.
        async onRequest({ request }) {
          const header = request.headers.get("authorization")?.trim() ?? "";
          if (!BEARER_PATTERN.test(header)) {
            logRequest(request.method, new URL(request.url).pathname, 401, "anonymous");
            throw unauthorized();
          }
          await authorizeThreadAccess(request);
        },
        onResponse({ request, response }) {
          logRequest(
            request.method,
            new URL(request.url).pathname,
            response.status,
            principalId(request),
          );
        },
        onError(error) {
          // Surface a thrown hook Response (e.g. our 401) instead of a 500.
          return error instanceof Response ? error : undefined;
        },
      },
    });
  })();
  return handlerPromise;
}

function bearerChecked(request: Request): Response | null {
  const header = request.headers.get("authorization")?.trim() ?? "";
  if (BEARER_PATTERN.test(header)) return null;
  logRequest(request.method, new URL(request.url).pathname, 401, "anonymous");
  return unauthorized();
}

export async function GET(request: Request): Promise<Response> {
  const rejected = bearerChecked(request);
  if (rejected) return rejected;
  const { pathname } = new URL(request.url);
  if (pathname === `${BASE_PATH}/threads`) {
    // The runtime's listing is memory-global — it would hand every member's
    // thread ids to any bearer. Thread management is a direct-perimeter
    // surface (ThreadSidebar/coachApi); the client's initialization probe
    // gets an empty page so `useAgent` still becomes ready.
    return Response.json({ threads: [], nextCursor: null });
  }
  const handler = await getHandler();
  return asByteStream(await handler(request));
}

export async function POST(request: Request): Promise<Response> {
  const handler = await getHandler();
  return asByteStream(await handler(request));
}

export async function PATCH(request: Request): Promise<Response> {
  const handler = await getHandler();
  return asByteStream(await handler(request));
}

export async function DELETE(request: Request): Promise<Response> {
  const handler = await getHandler();
  return asByteStream(await handler(request));
}
