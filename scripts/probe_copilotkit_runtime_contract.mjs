#!/usr/bin/env bun
/**
 * Throwaway contract-capture tool for CopilotKit runtime v2 and LangGraph.
 *
 * Boots the repository's hermetic coach stack, puts a redacting loopback proxy
 * in front of LangGraph, and drives one interrupt/resume/reconnect lifecycle.
 * This is evidence tooling, not a production runtime or compatibility layer.
 */

import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { get } from "node:http";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const requireFromFrontend = createRequire(resolve(ROOT, "frontend/package.json"));
const {
  CopilotRuntime,
  InMemoryAgentRunner,
  createCopilotRuntimeHandler,
} = requireFromFrontend("@copilotkit/runtime/v2");
const { LangGraphAgent } = requireFromFrontend("@copilotkit/runtime/langgraph");
const API_BASE = "/api/copilotkit";
const BOOT_TIMEOUT_MS = 120_000;
const REQUEST_TIMEOUT_MS = 120_000;
const FAILURE_TIMEOUT_MS = 10_000;
const pause = (milliseconds) => new Promise((resolvePause) => setTimeout(resolvePause, milliseconds));

class ProbeFailure extends Error {}

function redactShape(value) {
  if (Array.isArray(value)) return value.length === 0 ? [] : [redactShape(value[0])];
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, redactShape(item)]),
    );
  }
  if (value === null) return "<null>";
  return `<${typeof value}>`;
}

function queryShape(url) {
  return Object.fromEntries([...new Set(url.searchParams.keys())].sort().map((key) => [key, "<redacted>"]));
}

function bodyShape(bytes, contentType) {
  if (bytes.byteLength === 0) return null;
  if (!contentType.includes("application/json")) return "<binary>";
  try {
    return redactShape(JSON.parse(new TextDecoder().decode(bytes)));
  } catch (error) {
    if (error instanceof SyntaxError) return "<invalid-json>";
    throw error;
  }
}

function freePorts(count) {
  const reservations = Array.from({ length: count }, () => (
    Bun.serve({ port: 0, fetch: () => new Response(null, { status: 503 }) })
  ));
  const ports = reservations.map((reservation) => reservation.port);
  for (const reservation of reservations) reservation.stop(true);
  return ports;
}

async function waitForHttp(url, child) {
  const deadline = Date.now() + BOOT_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new ProbeFailure(`server exited with ${child.exitCode}`);
    const status = await new Promise((resolveStatus) => {
      const request = get(url, (response) => {
        response.resume();
        resolveStatus(response.statusCode ?? 599);
      });
      request.setTimeout(1_000, () => request.destroy());
      request.once("error", () => resolveStatus(599));
    });
    if (status < 500) return;
    await pause(250);
  }
  throw new ProbeFailure(`server did not boot within ${BOOT_TIMEOUT_MS}ms`);
}

async function terminate(child) {
  if (child.exitCode !== null) return;
  const exited = new Promise((resolveExit) => child.once("exit", resolveExit));
  try {
    process.kill(-child.pid, "SIGTERM");
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
  await Promise.race([exited, pause(15_000)]);
  if (child.exitCode === null) {
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch (error) {
      if (error?.code !== "ESRCH") throw error;
    }
    await Promise.race([exited, pause(5_000)]);
  }
}

function parseSse(text) {
  return text.split("\n").filter((line) => line.startsWith("data: "))
    .map((line) => line.slice(6)).filter((line) => line !== "[DONE]").map((line) => JSON.parse(line));
}

function interruptFrom(events) {
  const findCandidate = (value) => {
    if (Array.isArray(value)) {
      for (const item of value) {
        const candidate = findCandidate(item);
        if (candidate) return candidate;
      }
      return null;
    }
    if (value === null || typeof value !== "object") return null;
    if (typeof value.interruptId === "string") return { ...value, id: value.interruptId };
    if (typeof value.interrupt_id === "string") return { ...value, id: value.interrupt_id };
    if (typeof value.id === "string") return value;
    for (const item of Object.values(value)) {
      const candidate = findCandidate(item);
      if (candidate) return candidate;
    }
    return null;
  };
  for (const event of events) {
    if (
      (event.type === "CUSTOM" && ["LangGraphInterruptEvent", "on_interrupt"].includes(event.name))
      || (event.type === "RAW" && JSON.stringify(event.event).includes("__interrupt__"))
    ) {
      let payload = event;
      if (event.type === "CUSTOM" && typeof event.value === "string") {
        try {
          payload = JSON.parse(event.value);
        } catch (error) {
          if (!(error instanceof SyntaxError)) throw error;
          payload = { id: event.value };
        }
      }
      const interrupt = findCandidate(payload);
      if (interrupt) return interrupt;
      if (event.type === "CUSTOM" && event.name === "on_interrupt") {
        return { id: null, value: payload };
      }
    }
  }
  const summary = events.map((event) => ({
    type: event.type,
    name: event.name ?? null,
    rawEvent: event.event?.event ?? null,
    rawName: event.event?.name ?? null,
    valueShape: event.value === undefined ? null : redactShape(event.value),
  }));
  throw new ProbeFailure(`run emitted no LangGraphInterruptEvent: ${JSON.stringify(summary)}`);
}

async function requestText(url, init, timeoutMs = REQUEST_TIMEOUT_MS) {
  const response = await fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
  return { status: response.status, text: await response.text() };
}

async function main() {
  console.error("probe: starting hermetic servers");
  const asynchronousFailures = [];
  const recordAsynchronousFailure = (error) => {
    asynchronousFailures.push(error instanceof Error ? error.message : String(error));
  };
  process.on("uncaughtException", recordAsynchronousFailure);
  process.on("unhandledRejection", recordAsynchronousFailure);
  process.env.COPILOTKIT_TELEMETRY_DISABLED = "true";
  process.env.DO_NOT_TRACK = "1";
  const [gatewayPort, prePerimeterPort, memberPort] = freePorts(3);
  console.error(`probe: ports ${gatewayPort},${prePerimeterPort},${memberPort}`);
  const gatewayUrl = `http://127.0.0.1:${gatewayPort}`;
  const run = {
    server_url: `http://127.0.0.1:${prePerimeterPort}`,
    member_url: `http://127.0.0.1:${memberPort}`,
    u1: { token: "u1-access-token-4e2e", user_id: "member-u1" },
  };
  const fixtureCode = [
    "import importlib.util, sys",
    "spec=importlib.util.spec_from_file_location('copilotkit_probe_fixture', sys.argv[1])",
    "module=importlib.util.module_from_spec(spec)",
    "sys.modules[spec.name]=module",
    "spec.loader.exec_module(module)",
    "module.ThreadingHTTPServer(('127.0.0.1', int(sys.argv[2])), module.FixtureHandler).serve_forever()",
  ].join(";");
  const gateway = spawn(resolve(ROOT, ".venv/bin/python"), [
    "-c", fixtureCode, resolve(ROOT, "frontend/e2e/server.py"), String(gatewayPort),
  ], { cwd: ROOT, detached: true, stdio: ["ignore", "pipe", "pipe"] });
  const commonEnvironment = {
    ...process.env,
    SERVER_STORAGE: "memory",
    OPENAI_API_KEY: "fixture-openai",
    OPENAI_BASE_URL: `${gatewayUrl}/v1`,
    OPENAI_API_BASE: `${gatewayUrl}/v1`,
    SUPABASE_URL: gatewayUrl,
    SUPABASE_SERVICE_KEY: "service-secret",
    LANGSMITH_API_KEY: "platform-secret",
    LANGSMITH_ENDPOINT: gatewayUrl,
    LANGSMITH_TRACING: "false",
    HC_RAG_LLM_MODEL: "gpt-4o-mini",
    HC_RAG_VALIDATOR_MODEL: "gpt-4o-mini",
    HC_RAG_MEMBER_STREAM_PERIMETER: "v2",
    COACH_INTERNAL_TOKEN: "internal-secret",
  };
  const temp = await mkdtemp(join(tmpdir(), "copilotkit-contract-"));
  const setupCode = [
    "import importlib.util,json,sys",
    "from pathlib import Path",
    "root=Path(sys.argv[1]); work=Path(sys.argv[2])",
    "spec=importlib.util.spec_from_file_location('copilotkit_probe_fixture', root/'frontend/e2e/server.py')",
    "module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)",
    "graph=work/'e2e_graphs.py'; graph.write_text(module.E2E_GRAPHS)",
    "auth=work/'probe_auth.py'",
    "auth.write_text(\"from langgraph_sdk import Auth\\nauth=Auth()\\n@auth.authenticate\\nasync def authenticate(request): return {'identity':'contract-probe','is_authenticated':True,'kind':'StudioUser'}\\n@auth.on\\nasync def allow_all(ctx, value): return None\\n\")",
    "config=json.loads((root/'langgraph.json').read_text())",
    "config['dependencies']=[str(root)]; config['graphs']={'coach':f'{graph}:coach'}",
    "config['auth']={'path':f'{auth}:auth','disable_studio_auth':False}",
    "config.pop('http',None); config.pop('store',None); config.pop('env',None)",
    "(work/'langgraph.probe.json').write_text(json.dumps(config))",
  ].join(";");
  const setup = spawnSync(resolve(ROOT, ".venv/bin/python"), ["-c", setupCode, ROOT, temp], { cwd: ROOT });
  if (setup.status !== 0) throw new ProbeFailure(`probe config setup failed: ${setup.stderr?.toString() ?? "unknown"}`);
  const stack = spawn(resolve(ROOT, ".venv/bin/langgraph"), [
    "dev", "--no-browser", "--no-reload", "--config", join(temp, "langgraph.probe.json"),
    "--port", String(prePerimeterPort), "--server-log-level", "error",
  ], {
    cwd: temp,
    detached: true,
    env: { ...commonEnvironment, LANGGRAPH_API_URL: run.server_url, PYTHONPATH: `${temp}:${process.env.PYTHONPATH ?? ""}` },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const member = spawn(resolve(ROOT, ".venv/bin/python"), ["-m", "server"], {
    cwd: ROOT,
    detached: true,
    env: { ...commonEnvironment, SERVER_PORT: String(memberPort), SERVER_LOCAL_DEV: "0" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const children = [gateway, stack, member];
  console.error(`probe: pids ${children.map((child) => child.pid).join(",")}`);
  let stackOutput = "";
  for (const child of children) {
    child.stdout.on("data", (chunk) => { stackOutput += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stackOutput += chunk.toString(); });
  }

  const captures = [];
  const replayRequests = [];
  const routeChecks = [];
  let runtimeHandler;
  const server = Bun.serve({
    port: 0,
    idleTimeout: 255,
    async fetch(request) {
      const incoming = new URL(request.url);
      if (incoming.pathname.startsWith("/langgraph")) {
        const bytes = new Uint8Array(await request.arrayBuffer());
        const path = incoming.pathname.slice("/langgraph".length) || "/";
        const upstreamUrl = new URL(path + incoming.search, run.server_url);
        const headers = new Headers(request.headers);
        headers.delete("host");
        headers.delete("content-length");
        headers.delete("authorization");
        const record = {
          method: request.method,
          path,
          query: queryShape(incoming),
          bodyShape: bodyShape(bytes, request.headers.get("content-type") ?? ""),
          hasAuthorization: request.headers.has("authorization"),
          prePerimeterStatus: null,
          perimeterStatus: null,
        };
        captures.push(record);
        replayRequests.push({ method: request.method, path, search: incoming.search, bytes, contentType: request.headers.get("content-type") });
        try {
          const upstream = await fetch(upstreamUrl, {
            method: request.method,
            headers,
            body: bytes.byteLength === 0 ? undefined : bytes,
            signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
          });
          record.prePerimeterStatus = upstream.status;
          return new Response(upstream.body, { status: upstream.status, headers: upstream.headers });
        } catch (error) {
          record.prePerimeterStatus = "network-error";
          throw error;
        }
      }
      if (incoming.pathname.startsWith(API_BASE) && runtimeHandler) return runtimeHandler(request);
      return new Response("not found", { status: 404 });
    },
  });

  try {
    await Promise.all([
      waitForHttp(`${gatewayUrl}/feedback`, gateway),
      waitForHttp(`${run.server_url}/ok`, stack),
      waitForHttp(`${run.member_url}/ok`, member),
    ]);
    console.error("probe: servers ready");
    const runtime = new CopilotRuntime({
      agents: { coach: new LangGraphAgent({ deploymentUrl: `${server.url}langgraph`, graphId: "coach" }) },
      runner: new InMemoryAgentRunner(),
    });
    runtimeHandler = createCopilotRuntimeHandler({ runtime, basePath: API_BASE });
    const runtimeUrl = `${server.url}${API_BASE}`;
    const headers = { authorization: `Bearer ${run.u1.token}`, "content-type": "application/json" };
    const threadId = crypto.randomUUID();
    const userMessage = { id: crypto.randomUUID(), role: "user", content: "Schedule my weekly Friday check-in" };
    const input = (runId, resume) => ({
      threadId, runId,
      state: { question: userMessage.content },
      messages: [userMessage], tools: [], context: [], forwardedProps: {},
      ...(resume ? { resume } : {}),
    });

    const info = await requestText(`${runtimeUrl}/info`, { method: "GET", headers });
    routeChecks.push({ method: "GET", path: "/info", status: info.status });
    const first = await requestText(`${runtimeUrl}/agent/coach/run`, {
      method: "POST", headers, body: JSON.stringify(input(crypto.randomUUID())),
    });
    routeChecks.push({ method: "POST", path: "/agent/coach/run", status: first.status });
    if (first.status !== 200) throw new ProbeFailure(`initial run failed ${first.status}: ${first.text}`);
    const firstEvents = parseSse(first.text);
    const interruptEvent = interruptFrom(firstEvents);
    const pendingState = await requestText(`${run.server_url}/threads/${threadId}/state`, { headers });
    if (pendingState.status !== 200) {
      throw new ProbeFailure(`pending state failed ${pendingState.status}: ${pendingState.text}`);
    }
    const pendingStateBody = JSON.parse(pendingState.text);
    const stateInterrupt = pendingStateBody.tasks
      ?.flatMap((task) => task.interrupts ?? [])
      .find((candidate) => typeof candidate?.id === "string");
    const interruptId = interruptEvent.id ?? stateInterrupt?.id;
    if (typeof interruptId !== "string") {
      throw new ProbeFailure(`pending state omitted interrupt id: ${JSON.stringify(redactShape(pendingStateBody))}`);
    }

    const resumed = await requestText(`${runtimeUrl}/agent/coach/run`, {
      method: "POST", headers,
      body: JSON.stringify(input(crypto.randomUUID(), [{
        interruptId, status: "resolved", payload: { accept: true },
      }])),
    });
    if (resumed.status !== 200) throw new ProbeFailure(`resume failed ${resumed.status}: ${resumed.text}`);
    const resumedEvents = parseSse(resumed.text);
    if (!resumedEvents.some((event) => event.type === "RUN_FINISHED")) {
      throw new ProbeFailure(`resume did not finish: ${resumed.text}`);
    }

    const connected = await requestText(`${runtimeUrl}/agent/coach/connect`, {
      method: "POST", headers, body: JSON.stringify(input(crypto.randomUUID())),
    });
    routeChecks.push({ method: "POST", path: "/agent/coach/connect", status: connected.status });
    const connectedEvents = parseSse(connected.text);
    if (connected.status !== 200 || connectedEvents.length === 0) {
      throw new ProbeFailure(`connect replay failed ${connected.status}: ${connected.text}`);
    }

    for (const [method, path, body] of [
      ["GET", "/threads", undefined],
      ["GET", `/threads/${threadId}/messages`, undefined],
      ["GET", `/threads/${threadId}/events`, undefined],
      ["GET", `/threads/${threadId}/state`, undefined],
      ["POST", `/agent/coach/stop/${threadId}`, {}],
      ["POST", "/transcribe", {}],
    ]) {
      const result = await requestText(`${runtimeUrl}${path}`, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      });
      routeChecks.push({ method, path: path.replace(threadId, ":threadId"), status: result.status });
    }

    for (let index = 0; index < replayRequests.length; index += 1) {
      const replay = replayRequests[index];
      const controller = new AbortController();
      const replayHeaders = { authorization: `Bearer ${run.u1.token}` };
      if (replay.contentType) replayHeaders["content-type"] = replay.contentType;
      try {
        const response = await fetch(`${run.member_url}${replay.path}${replay.search}`, {
          method: replay.method,
          headers: replayHeaders,
          body: replay.bytes.byteLength === 0 ? undefined : replay.bytes,
          signal: controller.signal,
        });
        captures[index].perimeterStatus = response.status;
        controller.abort();
      } catch (error) {
        if (error?.name !== "AbortError") throw error;
      }
    }

    await terminate(stack);
    const failureStarted = Date.now();
    let failed;
    try {
      failed = await requestText(`${runtimeUrl}/agent/coach/run`, {
        method: "POST", headers, body: JSON.stringify(input(crypto.randomUUID())),
      }, FAILURE_TIMEOUT_MS);
    } catch (error) {
      failed = { status: "request-error", text: error instanceof Error ? error.message : String(error) };
    }
    const failureElapsedMs = Date.now() - failureStarted;
    await pause(50);
    const failureEvents = failed.status === 200 ? parseSse(failed.text) : [];
    const reportedFailure = failureEvents.some((event) => event.type === "RUN_ERROR")
      || asynchronousFailures.length > 0
      || failed.status === "request-error"
      || (typeof failed.status === "number" && failed.status >= 500);
    if (!reportedFailure) {
      throw new ProbeFailure(`dead-upstream run did not report failure: ${failed.status} ${failed.text}`);
    }
    console.log(JSON.stringify({
      versions: { runtime: "1.69.1", entrypoint: "@copilotkit/runtime/v2" },
      spawnedPids: children.map((child) => child.pid),
      killedPids: children.map((child) => child.pid),
      routeChecks, captures,
      lifecycle: {
        initialEventCount: firstEvents.length,
        interruptId: "<redacted>",
        resumedEventCount: resumedEvents.length,
        reconnectEventCount: connectedEvents.length,
      },
      deadUpstream: {
        reportedFailure,
        runtimeStatus: failed.status,
        callerTimedOut: failed.status === "request-error" && failed.text.includes("timed out"),
        asynchronousFailureCount: asynchronousFailures.length,
        elapsedMs: failureElapsedMs,
        timeoutMs: FAILURE_TIMEOUT_MS,
      },
    }, null, 2));
  } catch (error) {
    if (stackOutput) console.error(stackOutput);
    if (captures.length > 0) console.error(JSON.stringify({ captures }, null, 2));
    throw error;
  } finally {
    process.off("uncaughtException", recordAsynchronousFailure);
    process.off("unhandledRejection", recordAsynchronousFailure);
    server.stop(true);
    await Promise.all(children.map(terminate));
    await rm(temp, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack : String(error));
  process.exitCode = 1;
});
