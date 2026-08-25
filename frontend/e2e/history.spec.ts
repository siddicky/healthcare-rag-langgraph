import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import {
  memberApi,
  readRun,
  serverWithPerimeter,
  threadStreamAdmitted,
  THREAD_STREAM_SKIP_REASON,
  type Runfile,
  type RunIdentity,
} from "./run";

const COMPOSER = 'textarea[aria-label="Message your coach"]';

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

test.describe.configure({ mode: "serial" });

function isV2(run: Runfile): boolean {
  return run.perimeter === "v2" || run.next_public_perimeter === "v2";
}

async function createThread(api: APIRequestContext): Promise<string> {
  const create = await api.post("/threads", { data: {} });
  expect(create.status()).toBe(200);
  const body = (await create.json()) as { thread_id: string };
  expect(body.thread_id).toMatch(/^[0-9a-f-]{36}$/i);
  return body.thread_id;
}

test("history + time-travel UI smoke (v2 gated)", async ({ page }) => {
  test.setTimeout(180_000);
  const run = readRun();
  if (!isV2(run)) {
    test.skip(true, "history/time-travel requires NEXT_PUBLIC_HC_RAG_MEMBER_STREAM_PERIMETER=v2 (run with COACH_E2E_PERIMETER=v2)");
    return;
  }
  const api = await memberApi(run, run.u1.token);

  if (!(await threadStreamAdmitted(api))) {
    await api.dispose();
    test.skip(true, THREAD_STREAM_SKIP_REASON);
    return;
  }

  await login(page, run, run.u1);

  await send(page, "hello there");
  await waitForIdle(page);
  await send(page, "How should I take Metformin?");
  await waitForIdle(page);

  const panel = page.getByTestId("time-travel-panel");
  await expect(panel).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("time-travel-list")).toBeVisible();

  const entries = page.getByTestId("time-travel-entry");
  await expect(entries.first()).toBeVisible({ timeout: 30_000 });
  const count = await entries.count();
  expect(count).toBeGreaterThanOrEqual(1);

  const first = entries.first();
  await expect(first.getByTestId("time-travel-count")).toContainText("msgs");
  await expect(first.getByTestId("time-travel-view-btn")).toBeEnabled();
  await expect(first.getByTestId("time-travel-fork-btn")).toBeEnabled();

  const checkpointId = await first.getAttribute("data-checkpoint-id");
  expect(checkpointId).not.toBeNull();
  if (checkpointId !== null) {
    await first.getByTestId("time-travel-view-btn").click();
    await waitForIdle(page);
    await expect(page.locator(".bubble")).not.toHaveCount(0);
    await expect(first).toHaveAttribute("data-selected", "true");

    if (count >= 1) {
      const forkBtn = first.getByTestId("time-travel-fork-btn");
      await forkBtn.click();
      await waitForIdle(page);
      await expect(page.locator(".bubble")).not.toHaveCount(0);
    }
  }

  const refresh = page.getByTestId("time-travel-refresh");
  if (await refresh.isVisible()) {
    await expect(refresh).toBeEnabled();
    await refresh.click();
    await expect(panel).toBeVisible();
  }

  await api.dispose();
});

test("perimeter v2: history, join, join/stream, cancel, messages+resumable+enqueue accepted", async () => {
  test.setTimeout(90_000);
  const run = readRun();
  const baseUrl = serverWithPerimeter(run, "v2");
  test.skip(baseUrl === null, "no v2 server in this run");
  const api = await memberApi(run, run.u1.token, baseUrl ?? undefined);

  const threadId = await createThread(api);

  const history = await api.get(`/threads/${threadId}/history`);
  expect(history.status()).toBe(200);
  const checkpoints = (await history.json()) as unknown[];
  expect(Array.isArray(checkpoints)).toBe(true);

  const fakeRun = "00000000-0000-4000-8000-000000000001";
  for (const path of [
    `/threads/${threadId}/runs/${fakeRun}/join`,
    `/threads/${threadId}/runs/${fakeRun}/join/stream`,
  ]) {
    const probe = await api.get(path);
    // v2 admits the ROUTE; a missing run may yield 404 or an empty 200
    // depending on the server flavor — the perimeter assertion is "not 403".
    expect(probe.status()).not.toBe(403);
    try {
      await probe.dispose();
    } catch {
      // SSE response — dropping the context is enough
    }
  }

  const cancel = await api.post(`/threads/${threadId}/runs/${fakeRun}/cancel`);
  const cancelStatus = cancel.status();
  let cancelDetail = "";
  try {
    cancelDetail = ((await cancel.json()) as { detail?: string }).detail ?? "";
  } catch {
    // non-JSON body
  }
  // v2's PERIMETER admits the cancel route (body-less, query-less). What
  // the platform answers for a missing run varies by server flavor: the
  // clean-room server returns 404 "Run not found"; real langgraph dev also
  // requires a member auth scope for cancel, returning 403 "Forbidden". The
  // one answer that would mean the perimeter denied it is "Route is not
  // available".
  expect([200, 404, 409, 403]).toContain(cancelStatus);
  if (cancelStatus === 403) expect(cancelDetail).toBe("Forbidden");
  expect(cancelDetail).not.toBe("Route is not available");

  const streamV2 = await api.post(`/threads/${threadId}/runs/stream`, {
    data: {
      assistant_id: "coach",
      input: { question: "hello there" },
      stream_mode: ["updates", "messages"],
      stream_subgraphs: false,
      stream_resumable: true,
      durability: "exit",
      if_not_exists: "reject",
      multitask_strategy: "enqueue",
    },
  });
  expect([200, 201]).toContain(streamV2.status());
  try {
    await streamV2.dispose();
  } catch {
    // streaming response — dropping the context is enough
  }

  await api.delete(`/threads/${threadId}`);
  await api.dispose();
});

test("perimeter v1: history, join, join/stream, cancel and the v2 envelope stay rejected", async () => {
  test.setTimeout(90_000);
  const run = readRun();
  const baseUrl = serverWithPerimeter(run, "v1");
  test.skip(baseUrl === null, "no v1 server in this run");
  const api = await memberApi(run, run.u1.token, baseUrl ?? undefined);

  const threadId = await createThread(api);

  const history = await api.get(`/threads/${threadId}/history`);
  expect(history.status()).toBe(403);

  const fakeRun = "00000000-0000-4000-8000-000000000001";
  for (const path of [
    `/threads/${threadId}/runs/${fakeRun}/join`,
    `/threads/${threadId}/runs/${fakeRun}/join/stream`,
  ]) {
    const probe = await api.get(path);
    expect(probe.status()).toBe(403);
  }
  const cancel = await api.post(`/threads/${threadId}/runs/${fakeRun}/cancel`);
  expect(cancel.status()).toBe(403);

  const v2envelope = await api.post(`/threads/${threadId}/runs/stream`, {
    data: {
      assistant_id: "coach",
      input: { question: "hello there" },
      stream_mode: ["updates", "messages"],
      stream_subgraphs: false,
      stream_resumable: true,
      durability: "exit",
      if_not_exists: "reject",
      multitask_strategy: "enqueue",
    },
  });
  expect(v2envelope.status()).toBe(403);

  const v1envelope = await api.post(`/threads/${threadId}/runs/stream`, {
    data: {
      assistant_id: "coach",
      input: { question: "hello there" },
      stream_mode: ["updates"],
      stream_subgraphs: false,
      stream_resumable: false,
      durability: "exit",
      if_not_exists: "reject",
      multitask_strategy: "reject",
    },
  });
  expect([200, 201]).toContain(v1envelope.status());
  try {
    await v1envelope.dispose();
  } catch {
    // streaming response — dropping the context is enough
  }

  await api.delete(`/threads/${threadId}`);
  await api.dispose();
});
