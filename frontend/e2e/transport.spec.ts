import { expect, test, type Page } from "@playwright/test";
import { execSync, spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { copilotApi, memberApi, readRun, type Runfile, type RunIdentity } from "./run";

const COMPOSER = 'textarea[aria-label="Message your coach"]';

test.describe.configure({ mode: "serial" });

async function login(page: Page, run: Runfile, identity: RunIdentity): Promise<void> {
  await page.goto(`${run.frontend_url}/login`);
  await page.fill("#email", identity.email);
  await page.fill("#password", identity.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(`${run.frontend_url}/chat`);
  await expect(page.locator(".coach-pill")).toBeVisible();
  await expect(page.locator(COMPOSER)).toBeVisible();
}

async function waitForIdle(page: Page): Promise<void> {
  await expect(page.locator(COMPOSER)).toBeEnabled({ timeout: 60_000 });
}

async function send(page: Page, text: string): Promise<void> {
  await waitForIdle(page);
  await page.fill(COMPOSER, text);
  await page.press(COMPOSER, "Enter");
}

/** Thread ids as seen by the transport: AG-UI run requests carry them. */
function trackThreadIds(page: Page): string[] {
  const ids: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "POST") return;
    if (!request.url().includes("/api/copilotkit/agent/coach/run")) return;
    try {
      const body = request.postDataJSON() as { threadId?: unknown };
      if (typeof body?.threadId === "string") ids.push(body.threadId);
    } catch {
      // non-JSON run request — nothing to track
    }
  });
  return ids;
}

async function newConversation(page: Page): Promise<void> {
  await page.getByRole("button", { name: "New conversation" }).click();
}

test("copilotkit isolation: u1's thread is unreachable for u2 through the runtime route", async ({ page }) => {
  test.setTimeout(180_000);
  const run = readRun();
  const ids = trackThreadIds(page);

  await login(page, run, run.u1);
  await newConversation(page);
  await send(page, "hello there from the isolation scenario");
  await expect(page.locator(".bubble.assistant", { hasText: "Hello from your coach." })).toHaveCount(1, {
    timeout: 60_000,
  });
  await waitForIdle(page);
  await expect
    .poll(() => ids.length, { timeout: 30_000 })
    .toBeGreaterThan(0);
  const u1Thread = ids[0] ?? "";

  const u2 = await copilotApi(run, run.u2.token);
  // The runtime listing is answered with an EMPTY page by our route (the
  // runtime's own memory-global list would leak thread ids across members);
  // it must never contain u1's threads.
  const listing = await u2.get("/api/copilotkit/threads");
  expect(listing.status()).toBe(200);
  const listingBody = (await listing.json()) as { threads: unknown[] };
  expect(listingBody.threads).toEqual([]);

  for (const suffix of ["messages", "state", "events"]) {
    const read = await u2.get(`/api/copilotkit/threads/${u1Thread}/${suffix}`);
    expect(read.status()).toBe(404);
  }

  const stop = await u2.post(`/api/copilotkit/agent/coach/stop/${u1Thread}`, { data: {} });
  expect(stop.status()).toBe(404);

  const foreignRun = await u2.post("/api/copilotkit/agent/coach/run", {
    data: {
      threadId: u1Thread,
      runId: crypto.randomUUID(),
      state: {},
      messages: [],
      tools: [],
      context: [],
    },
  });
  expect(foreignRun.status()).toBe(404);

  const foreignConnect = await u2.post("/api/copilotkit/agent/coach/connect", {
    data: { threadId: u1Thread },
  });
  expect(foreignConnect.status()).toBe(404);

  const owner = await copilotApi(run, run.u1.token);
  const ownRead = await owner.get(`/api/copilotkit/threads/${u1Thread}/messages`);
  expect(ownRead.status()).toBe(200);
  await owner.dispose();
  await u2.dispose();
});

test("copilotkit erase-unrecoverability: an erased thread stays unreachable through the runtime route", async ({ page }) => {
  test.setTimeout(180_000);
  const run = readRun();
  const ids = trackThreadIds(page);

  await login(page, run, run.u1);
  await newConversation(page);
  // Distinct marker: earlier suites' threads persist server-side for u1 and
  // get replayed on login, so a generic greeting could be satisfied by the
  // replay before this conversation's own run fires.
  await send(page, "hello there from the erase scenario");
  await expect(
    page.locator(".bubble.assistant", { hasText: "Hello from your coach." }),
  ).toHaveCount(1, { timeout: 60_000 });
  await waitForIdle(page);
  await expect
    .poll(() => ids.length, { timeout: 30_000 })
    .toBeGreaterThan(0);
  const erasedThread = ids[0] ?? "";

  // forget-member equivalent, scripted: the erase phrasing drives the
  // graph's erasure node THROUGH the transport (store/crons/reservations
  // swept, durable marker emitted), then the member side snapshots and
  // deletes every owned thread over the direct perimeter surface.
  await send(page, "please erase all my data");
  await waitForIdle(page);

  const direct = await memberApi(run, run.u1.token);
  let remaining: string[] = [erasedThread];
  await expect
    .poll(async () => {
      const search = await direct.post("/threads/search", {
        data: { select: ["thread_id"], limit: 100, offset: 0 },
      });
      expect(search.status()).toBe(200);
      remaining = ((await search.json()) as { thread_id: string }[]).map(
        (t) => t.thread_id,
      );
      for (const id of remaining) {
        const deleted = await direct.delete(`/threads/${id}`);
        expect([200, 204, 404]).toContain(deleted.status());
      }
      return remaining.length;
    }, { timeout: 60_000 })
    .toBe(0);
  await direct.dispose();

  const u1 = await copilotApi(run, run.u1.token);
  const connect = await u1.post("/api/copilotkit/agent/coach/connect", {
    data: { threadId: erasedThread },
  });
  expect(connect.status()).toBe(404);

  const rerun = await u1.post("/api/copilotkit/agent/coach/run", {
    data: {
      threadId: erasedThread,
      runId: crypto.randomUUID(),
      state: {},
      messages: [],
      tools: [],
      context: [],
    },
  });
  expect(rerun.status()).toBe(404);

  const messages = await u1.get(`/api/copilotkit/threads/${erasedThread}/messages`);
  expect(messages.status()).toBe(404);
  await u1.dispose();
});

test("copilotkit restart/reconnect: a Next restart mid-run resumes or cleanly completes via /connect", async ({ page }) => {
  test.setTimeout(300_000);
  const run = readRun();
  const ids = trackThreadIds(page);
  let connectRequests = 0;
  page.on("request", (request) => {
    if (request.url().includes("/api/copilotkit/agent/coach/connect")) connectRequests += 1;
  });

  await login(page, run, run.u1);
  await newConversation(page);
  await send(page, "slowly restart marker");
  await expect(page.locator(COMPOSER)).toBeDisabled({ timeout: 30_000 });

  // Restart ONLY the Next server; the LangGraph run continues upstream.
  const frontendPort = new URL(run.frontend_url).port;
  // -sTCP:LISTEN: without it lsof also matches the browser's open client
  // connection to the port, and the spec would SIGTERM its own Chromium.
  const pids = execSync(`lsof -ti tcp:${frontendPort} -sTCP:LISTEN`)
    .toString()
    .trim()
    .split("\n")
    .map(Number)
    .filter((pid) => Number.isInteger(pid) && pid > 0);
  expect(pids.length).toBeGreaterThan(0);
  for (const pid of pids) process.kill(pid, "SIGTERM");
  const deadline = Date.now() + 30_000;
  for (;;) {
    let free = true;
    try {
      execSync(`lsof -ti tcp:${frontendPort}`, { stdio: "ignore" });
      free = false;
    } catch {
      free = true;
    }
    if (free || Date.now() > deadline) break;
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 500);
  }

  const restarted = spawn("bun", ["run", "start"], {
    cwd: path.join(__dirname, ".."),
    env: {
      ...process.env,
      PORT: frontendPort,
      LANGGRAPH_DEPLOYMENT_URL: run.server_url,
      NEXT_TELEMETRY_DISABLED: "1",
    },
    detached: true,
    stdio: "ignore",
  });
  restarted.unref();
  writeFileSync(path.join(__dirname, ".tmp", "frontend-restart.pid"), String(restarted.pid));

  let up = false;
  for (let i = 0; i < 120 && !up; i += 1) {
    try {
      const probe = await fetch(`${run.frontend_url}/login`);
      up = probe.status < 500;
    } catch {
      up = false;
    }
    if (!up) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1000);
  }
  expect(up).toBe(true);

  // The Supabase session survives in localStorage; re-enter the chat shell.
  await page.goto(`${run.frontend_url}/chat`);
  await expect(page.locator(COMPOSER)).toBeVisible({ timeout: 60_000 });

  await expect(
    page.locator(".bubble.assistant", { hasText: "Slow reply to: slowly restart marker" }),
  ).toHaveCount(1, { timeout: 120_000 });
  await waitForIdle(page);
  expect(connectRequests).toBeGreaterThanOrEqual(1);
  expect(ids[0] ?? "").not.toBe("");
});

test("copilotkit cancellation: the stop route aborts the run and busy resets (v2 perimeter)", async ({ page }) => {
  const run = readRun();
  test.skip(run.perimeter !== "v2", "member run cancel is admitted by perimeter v2 only (perimeter.py gates _CANCEL on v2)");
  test.setTimeout(180_000);
  const ids = trackThreadIds(page);

  await login(page, run, run.u1);
  await newConversation(page);
  await send(page, "slowly cancel me");
  await expect(page.locator(COMPOSER)).toBeDisabled({ timeout: 30_000 });
  await expect
    .poll(() => ids.length, { timeout: 30_000 })
    .toBeGreaterThan(0);
  const cancelledThread = ids[0] ?? "";

  const u1 = await copilotApi(run, run.u1.token);
  const stop = await u1.post(`/api/copilotkit/agent/coach/stop/${cancelledThread}`, { data: {} });
  expect(stop.status()).toBe(200);
  await u1.dispose();

  await waitForIdle(page);
  // The aborted turn must never produce its answer.
  await page.waitForTimeout(5_000);
  await expect(
    page.locator(".bubble.assistant", { hasText: "Slow reply to: slowly cancel me" }),
  ).toHaveCount(0);
});

test("copilotkit concurrency: simultaneous u1+u2 streams stay isolated", async ({ page }) => {
  test.setTimeout(240_000);
  const run = readRun();
  const browser = page.context().browser();
  expect(browser).not.toBeNull();
  const secondContext = await browser!.newContext();
  const u2Page = await secondContext.newPage();

  const u1Ids = trackThreadIds(page);
  const u2Ids = trackThreadIds(u2Page);

  await login(page, run, run.u1);
  await login(u2Page, run, run.u2);
  await newConversation(page);
  await newConversation(u2Page);

  await send(page, "slowly zebra one");
  await send(u2Page, "slowly koala two");

  await expect(
    page.locator(".bubble.assistant", { hasText: "Slow reply to: slowly zebra one" }),
  ).toHaveCount(1, { timeout: 120_000 });
  await expect(
    u2Page.locator(".bubble.assistant", { hasText: "Slow reply to: slowly koala two" }),
  ).toHaveCount(1, { timeout: 120_000 });

  await expect(page.locator(".bubble.assistant", { hasText: "koala two" })).toHaveCount(0);
  await expect(u2Page.locator(".bubble.assistant", { hasText: "zebra one" })).toHaveCount(0);

  const u1Thread = u1Ids[0] ?? "";
  const u2Thread = u2Ids[0] ?? "";
  expect(u1Thread).not.toBe("");
  expect(u2Thread).not.toBe("");
  expect(u1Thread).not.toBe(u2Thread);

  const u2Api = await copilotApi(run, run.u2.token);
  const crossRead = await u2Api.get(`/api/copilotkit/threads/${u1Thread}/messages`);
  expect(crossRead.status()).toBe(404);
  await u2Api.dispose();

  await secondContext.close();
});
