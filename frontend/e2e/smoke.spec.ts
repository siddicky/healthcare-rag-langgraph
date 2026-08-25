import { expect, request, test, type APIRequestContext, type Page } from "@playwright/test";
import path from "node:path";
import {
  envelopesOf,
  historyBranchUiEnabled,
  HISTORY_BRANCH_UI_SKIP_REASON,
  internalHeaders,
  memberApi,
  monthOf,
  nextFriday,
  nextWeekday,
  readRun,
  threadStreamAdmitted,
  THREAD_STREAM_SKIP_REASON,
  threadState,
  type DataEnvelope,
  type Runfile,
  type RunIdentity,
} from "./run";

const SCREENSHOTS = path.join(__dirname, "__screenshots__");
const COMPOSER = 'textarea[aria-label="Message your coach"]';
const REGENERATE = '[data-testid="action-bar"] button[aria-label="Regenerate"]';

/**
 * A perimeter-VALID run envelope: under a v2 server the v1 fixed values
 * (resumable=false / reject) would be denied for the wrong reason, so the
 * sentinel's 403s must each be attributable to the violation under test
 * (cron_wake input, foreign attachment, webhook/metadata keys).
 */
function runEnvelope(): Record<string, unknown> {
  const v2 = readRun().perimeter === "v2";
  return {
    assistant_id: "coach",
    input: { question: "hello there" },
    stream_mode: ["updates"],
    stream_subgraphs: false,
    ...(v2
      ? { stream_resumable: true, multitask_strategy: "enqueue" }
      : { stream_resumable: false, multitask_strategy: "reject" }),
    durability: "exit",
    if_not_exists: "reject",
  };
}

const shared: { u1ThreadId: string; u1UploadId: string; u2ThreadId: string } = {
  u1ThreadId: "",
  u1UploadId: "",
  u2ThreadId: "",
};

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

async function expectAssistantCount(page: Page, text: string, count: number): Promise<void> {
  await expect(page.locator(".bubble.assistant", { hasText: text })).toHaveCount(count, {
    timeout: 60_000,
  });
  await waitForIdle(page);
}

function trackThreadCreations(page: Page): string[] {
  const ids: string[] = [];
  page.on("response", (response) => {
    if (response.request().method() !== "POST") return;
    if (!response.url().endsWith("/threads")) return;
    void response
      .json()
      .then((body) => {
        if (body && typeof body.thread_id === "string") ids.push(body.thread_id);
      })
      .catch(() => {
        // non-JSON thread response — nothing to track
      });
  });
  return ids;
}

async function confirmInterrupt(page: Page, confirmedSoFar: number): Promise<void> {
  const card = page.getByTestId("interrupt-card");
  await expect(card).toBeVisible({ timeout: 60_000 });
  await card.getByRole("button", { name: "Confirm change" }).click();
  await expect(page.locator(".widget-wrap", { hasText: "✓ Confirmed" })).toHaveCount(
    confirmedSoFar,
    { timeout: 60_000 },
  );
  await waitForIdle(page);
}

async function latestEnvelope(
  api: APIRequestContext,
  threadId: string,
  blockPrefix: string,
): Promise<DataEnvelope> {
  const envelopes = envelopesOf(await threadState(api, threadId));
  const matches = envelopes.filter((envelope) => envelope.block_id.startsWith(blockPrefix));
  const last = matches[matches.length - 1];
  if (last === undefined) throw new Error(`no envelope with block prefix ${blockPrefix}`);
  return last;
}

function reminderPresent(state: { values: { messages?: unknown[] } }): boolean {
  const messages = state.values.messages ?? [];
  return messages.some((message) => {
    if (typeof message !== "object" || message === null) return false;
    const content = (message as { content?: unknown }).content;
    return typeof content === "string" && /"block_id":\s*"reminder:/.test(content);
  });
}

async function gateOnThreadStream(): Promise<void> {
  const run = readRun();
  const api = await memberApi(run, run.u1.token);
  const admitted = await threadStreamAdmitted(api);
  await api.dispose();
  test.skip(!admitted, THREAD_STREAM_SKIP_REASON);
}

test("u1 journey: chat, cards, interrupts, reminders, documents, regenerate, feedback, branch", async ({ page }) => {
  test.setTimeout(300_000);
  await gateOnThreadStream();
  const run = readRun();
  const api = await memberApi(run, run.u1.token);
  const createdThreads = trackThreadCreations(page);

  await login(page, run, run.u1);
  await page.getByRole("button", { name: "New conversation" }).click();

  await test.step("medical_lookup answer renders exactly once", async () => {
    await send(page, "How should I take Metformin?");
    await expectAssistantCount(page, "Offline monograph answer for: How should I take Metformin?", 1);
  });
  shared.u1ThreadId = createdThreads[0] ?? "";
  expect(shared.u1ThreadId).not.toBe("");

  await test.step("weight logged twice hydrates TrendCard with delta", async () => {
    await send(page, "log my weight");
    await expectAssistantCount(page, "Logged your weight.", 1);
    await send(page, "log my weight");
    await expectAssistantCount(page, "Logged your weight.", 2);

    const trend = await latestEnvelope(api, shared.u1ThreadId, "trend:weight");
    expect(trend.data.value).toBe("80");
    expect(trend.data.unit).toBe("kg");
    expect(trend.data.delta).toBe("-2.0 kg");
    const card = page.getByTestId("compose-tree").last();
    await expect(card).toContainText(String(trend.data.label));
    await expect(card).toContainText(String(trend.data.value));
    await expect(card).toContainText(String(trend.data.unit));
    await expect(card).toContainText(String(trend.data.delta));
    await expect(card.locator("svg polyline")).toHaveCount(1);
    await expect(card).not.toContainText("__ref");
    await page.screenshot({ path: path.join(SCREENSHOTS, "trend-card.png"), fullPage: true });
  });

  await test.step("schedule add goes through the calendar interrupt", async () => {
    await send(page, "schedule my weekly friday check-in");
    await confirmInterrupt(page, 1);
  });

  await test.step("double-click confirm on a second distinct change applies exactly once", async () => {
    await send(page, "move my friday check-in to monday");
    const card = page.getByTestId("interrupt-card");
    await expect(card).toBeVisible({ timeout: 60_000 });
    const confirm = card.getByRole("button", { name: "Confirm change" });
    const box = await confirm.boundingBox();
    if (box === null) throw new Error("confirm button has no bounding box");
    await confirm.click({ noWaitAfter: true });
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await expect(page.locator(".widget-wrap", { hasText: "✓ Confirmed" })).toHaveCount(2, {
      timeout: 60_000,
    });
    await waitForIdle(page);

    const state = await threadState(api, shared.u1ThreadId);
    const mondayOps = envelopesOf(state).filter(
      (envelope) =>
        envelope.block_id.startsWith("calendar-change:") &&
        typeof envelope.data.card === "object" &&
        envelope.data.card !== null &&
        String((envelope.data.card as Record<string, unknown>).toLabel).includes("Mon,"),
    );
    expect(mondayOps).toHaveLength(1);
  });

  await test.step("move back to friday confirms and keeps one entry", async () => {
    await send(page, "move my check-in back to friday");
    await confirmInterrupt(page, 3);
    await page.screenshot({
      path: path.join(SCREENSHOTS, "calendar-change-confirmed.png"),
      fullPage: true,
    });
  });

  const finalFriday = nextWeekday(nextWeekday(nextFriday(), 0), 4);
  const currentMonth = monthOf(new Date());
  const finalMonth = monthOf(finalFriday);

  await test.step("calendar month views render hydrated MiniCalendar facts", async () => {
    if (currentMonth !== finalMonth) {
      await send(page, `what's on my calendar in ${currentMonth}?`);
      await waitForIdle(page);
      const tree = page.getByTestId("compose-tree").last();
      const sameMonth = await latestEnvelope(api, shared.u1ThreadId, `calendar:${currentMonth}`);
      await expect(tree).toContainText(String(sameMonth.data.monthLabel));
      expect(sameMonth.data.highlights).toEqual([]);
      await expect(tree).not.toContainText("__ref");
    }

    await send(page, `what's on my calendar in ${finalMonth}?`);
    await waitForIdle(page);
    const tree = page.getByTestId("compose-tree").last();
    const finalMonthEnvelope = await latestEnvelope(api, shared.u1ThreadId, `calendar:${finalMonth}`);
    await expect(tree).toContainText(String(finalMonthEnvelope.data.monthLabel));
    expect(finalMonthEnvelope.data.highlights).toEqual([
      { date: finalFriday.getDate(), type: "checkin" },
    ]);
    const highlights = finalMonthEnvelope.data.highlights as { date: number }[];
    const gold = tree.locator('button[style*="var(--gold)"]');
    await expect(gold).toHaveCount(highlights.length);
    for (const [index, highlight] of highlights.entries()) {
      await expect(gold.nth(index)).toHaveText(String(highlight.date));
    }
    await expect(tree.locator("button")).toHaveCount(
      Number(finalMonthEnvelope.data.firstWeekday) + Number(finalMonthEnvelope.data.daysInMonth),
    );
    await expect(tree).not.toContainText("__ref");
  });

  await test.step("reminder creation lists the active reminder", async () => {
    await send(page, "remind me to log my weight every monday");
    await expectAssistantCount(page, "Reminder set for Mondays at 8:00 AM.", 1);
    const list = page.getByTestId("reminder-list").last();
    await expect(list).toContainText("Log my weight");
    await expect(list).toContainText("Every Monday at 8:00 AM");
  });

  await test.step("cron wake delivery renders the full ReminderCard", async () => {
    const internal = await request.newContext({ baseURL: run.server_url });
    const search = await internal.post("/runs/crons/search", {
      headers: internalHeaders(run, run.u1.user_id),
      data: {
        metadata: { user_id: run.u1.user_id },
        limit: 10,
        offset: 0,
        sort_by: "cron_id",
        sort_order: "asc",
      },
    });
    expect(search.status()).toBe(200);
    const crons = (await search.json()) as { cron_id: string; thread_id: string; schedule: string }[];
    expect(crons).toHaveLength(1);
    const cron = crons[0];
    if (cron === undefined) throw new Error("reminder cron not found");
    expect(cron.thread_id).toBe(shared.u1ThreadId);
    const patched = await internal.patch(
      `/runs/crons/${cron.cron_id}`,
      { headers: internalHeaders(run, run.u1.user_id), data: { schedule: "* * * * *" } },
    );
    expect(patched.status()).toBe(200);

    await expect
      .poll(async () => reminderPresent(await threadState(api, shared.u1ThreadId)), {
        timeout: 120_000,
        intervals: [2_000],
      })
      .toBe(true);

    // Disable the cron at once: an every-minute schedule would launch a
    // concurrent cron run that collides with the pause turn (the member
    // envelope pins multitask_strategy to "reject"). Disabling still leaves
    // the active record for the pause turn to flip.
    const disabled = await internal.patch(
      `/runs/crons/${cron.cron_id}`,
      { headers: internalHeaders(run, run.u1.user_id), data: { enabled: false } },
    );
    expect(disabled.status()).toBe(200);
    await internal.dispose();

    await page.locator('.thread-item[title="How should I take Metformin?"]').click();
    const reminder = page.getByTestId("reminder-card");
    await expect(reminder).toBeVisible({ timeout: 30_000 });
    await expect(reminder).toContainText("Log my weight");
    await expect(reminder).toContainText("Every Monday at 8:00 AM");
    await expect(reminder).toContainText("Next:");
    await page.screenshot({ path: path.join(SCREENSHOTS, "reminder-card.png"), fullPage: true });
  });

  await test.step("pausing the reminder shows the paused state and gates regenerate off", async () => {
    await send(page, "pause my log my weight reminder");
    await expectAssistantCount(page, "Paused your reminder.", 1);
    const list = page.getByTestId("reminder-list").last();
    await expect(list).toContainText("Log my weight");
    await expect(list).toContainText("Paused");

    await expect(page.locator(REGENERATE)).toHaveCount(0);
    await expect(page.getByTestId("action-bar")).toBeVisible();
  });

  await test.step("a fresh medical_lookup turn answers, but tool activity keeps regenerate off", async () => {
    const answer = "Offline monograph answer for: What are the side effects of Lipitor?";
    await send(page, "What are the side effects of Lipitor?");
    await expectAssistantCount(page, answer, 1);
    // medical_lookup is a real tool call now (ToolMessage + return_direct AIMessage),
    // so the turn-local "no tool activity" regenerate gate applies to it exactly like
    // any other tool — see regenerateEligibility() in src/chat/model.ts.
    await expect(page.locator(REGENERATE)).toHaveCount(0);
  });

  await test.step("thumbs-up reaches the feedback mirror", async () => {
    const up = page.locator('[data-testid="action-bar"] button[aria-label="Good response"]');
    await up.click();
    await expect(up).toHaveClass(/up-active/);
    await expect(up).toBeDisabled();

    const dep = await request.newContext({ baseURL: run.dep_url });
    await expect
      .poll(
        async () => {
          const response = await dep.get("/e2e/feedback");
          const payload = (await response.json()) as { posts: { score?: unknown }[] };
          return payload.posts.some(
            (post) => post.score === 1 && JSON.stringify(post).includes(shared.u1ThreadId),
          );
        },
        { timeout: 30_000, intervals: [1_000] },
      )
      .toBe(true);
    await dep.dispose();
  });

  await test.step("branch copies history into a new thread", async () => {
    // Early return (not test.skip) so the rest of the serial journey still runs.
    if (!historyBranchUiEnabled(run)) {
      test.info().annotations.push({ type: "skip", description: HISTORY_BRANCH_UI_SKIP_REASON });
      return;
    }
    const before = await page.locator(".thread-item").count();
    const copyResponse = page.waitForResponse(
      (response) => response.request().method() === "POST" && response.url().endsWith("/copy"),
    );
    await page.locator('button[aria-label="Branch into a new thread"]').click();
    const copied = await copyResponse;
    expect(copied.status()).toBe(200);
    await expect(page.locator(".thread-item")).toHaveCount(before + 1);
    await expect(
      page.locator(".bubble.human", { hasText: "How should I take Metformin?" }),
    ).toBeVisible();
    await expect(page.getByTestId("action-bar")).toBeVisible();
  });

  await test.step("document upload reviews, edits and saves extracted memory", async () => {
    let uploadId = "";
    await page.route("**/coach/uploads", async (route) => {
      const body = route.request().postDataBuffer()?.toString("latin1") ?? "";
      uploadId = /name="upload_id"\r\n\r\n([0-9a-fA-F-]{36})/.exec(body)?.[1] ?? "";
      await route.continue();
    });
    const uploadResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" && response.url().includes("/coach/uploads"),
    );
    await page.setInputFiles(
      'input[aria-label="Attach a document"]',
      path.join(__dirname, "fixtures", "intake.pdf"),
    );
    const uploaded = await uploadResponse;
    await page.unroute("**/coach/uploads");
    expect(uploaded.status()).toBe(201);
    shared.u1UploadId = uploadId;
    expect(shared.u1UploadId).not.toBe("");

    const ingest = page.getByTestId("document-ingest");
    await expect(ingest).toBeVisible();
    await expect(ingest).toContainText("intake.pdf");
    await expect(ingest).toContainText("Ready to review", { timeout: 30_000 });
    await expect(page.getByText("Document ready to review")).toBeVisible();

    await send(page, "please review");
    const review = page.getByTestId("interrupt-card");
    await expect(review).toBeVisible({ timeout: 60_000 });
    await expect(review).toContainText("Found in");
    await expect(review).toContainText("Lipitor");
    await review.getByRole("button", { name: "Edit Dose time" }).click();
    await review.locator("input.form-input").fill("Morning");
    await review.locator("input.form-input").press("Enter");
    await review.getByRole("button", { name: "Save to profile" }).click();

    const confirmation = page.getByTestId("memory-confirmation");
    await expect(confirmation).toBeVisible({ timeout: 60_000 });
    await expect(confirmation).toContainText("Morning");
    await expect(confirmation).toContainText("Lipitor");
    await expect(confirmation.locator("text=✓ Saved")).toHaveCount(2);
    await page.screenshot({ path: path.join(SCREENSHOTS, "memory-extraction-card.png"), fullPage: true });
    await waitForIdle(page);
  });

  await api.dispose();
});

test("u2 isolation: own threads only, cross-identity upload rejected", async ({ page }) => {
  test.setTimeout(120_000);
  await gateOnThreadStream();
  const run = readRun();
  const api = await memberApi(run, run.u2.token);
  const createdThreads = trackThreadCreations(page);

  await login(page, run, run.u2);
  await expect(page.locator(".thread-item")).toHaveCount(0);

  await send(page, "hello there");
  await expectAssistantCount(page, "Hello from your coach.", 1);
  shared.u2ThreadId = createdThreads[0] ?? "";
  expect(shared.u2ThreadId).not.toBe("");
  expect(shared.u2ThreadId).not.toBe(shared.u1ThreadId);

  const foreignThread = await api.get(`/threads/${shared.u1ThreadId}`);
  expect(foreignThread.status()).toBe(404);

  const foreignStatus = await api.get(`/coach/uploads/${shared.u1UploadId}/status`);
  expect(foreignStatus.status()).toBe(404);

  const hijack = await api.post(`/threads/${shared.u2ThreadId}/runs/stream`, {
    data: {
      ...runEnvelope(),
      input: { question: "Please review this document.", attachment_id: shared.u1UploadId },
    },
  });
  expect(hijack.status()).toBe(403);

  await page.screenshot({ path: path.join(SCREENSHOTS, "isolation-denial.png"), fullPage: true });
  await api.dispose();
});

test("wrong password stays on /login with an error", async ({ page }) => {
  const run = readRun();
  await page.goto(`${run.frontend_url}/login`);
  await page.fill("#email", run.u1.email);
  await page.fill("#password", "not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator('p[role="alert"]')).toBeVisible();
  await expect(page.locator('p[role="alert"]')).toContainText("doesn't match");
  await expect(page).toHaveURL((url) => url.href === `${run.frontend_url}/login`);
});

test("perimeter sentinel: member token rejection set", async () => {
  test.setTimeout(60_000);
  const run = readRun();
  const api = await memberApi(run, run.u1.token);
  if (shared.u1ThreadId === "" || shared.u2ThreadId === "") {
    // The journey/isolation tests were probe-skipped — create the threads
    // the cross-identity assertions need, under their owning identities.
    if (shared.u1ThreadId === "") {
      const create = await api.post("/threads", { data: {} });
      expect(create.status()).toBe(200);
      shared.u1ThreadId = ((await create.json()) as { thread_id: string }).thread_id;
    }
    if (shared.u2ThreadId === "") {
      const u2Api = await memberApi(run, run.u2.token);
      const create = await u2Api.post("/threads", { data: {} });
      expect(create.status()).toBe(200);
      shared.u2ThreadId = ((await create.json()) as { thread_id: string }).thread_id;
      await u2Api.dispose();
    }
  }
  const stream = `/threads/${shared.u1ThreadId}/runs/stream`;

  const cronSearch = await api.post("/runs/crons/search", { data: {} });
  expect(cronSearch.status()).toBe(403);

  const cronCreate = await api.post(`/threads/${shared.u1ThreadId}/runs/crons`, {
    data: { assistant_id: "coach", schedule: "* * * * *", input: {} },
  });
  expect(cronCreate.status()).toBe(403);

  const wakeRun = await api.post(stream, {
    data: {
      ...runEnvelope(),
      input: {
        cron_wake: {
          reminder_id: "00000000-0000-4000-8000-000000000001",
          user_id: run.u1.user_id,
          thread_id: shared.u1ThreadId,
          wake_token: "f".repeat(64),
        },
      },
    },
  });
  expect(wakeRun.status()).toBe(403);

  const webhookRun = await api.post(stream, {
    data: { ...runEnvelope(), webhook: "https://example.com/hook" },
  });
  expect(webhookRun.status()).toBe(403);

  const metadataRun = await api.post(stream, {
    data: { ...runEnvelope(), metadata: { injected: true } },
  });
  expect(metadataRun.status()).toBe(403);

  const badSearch = await api.post("/threads/search", {
    data: { select: ["thread_id", "values"], limit: 5, offset: 0 },
  });
  expect(badSearch.status()).toBe(403);

  const store = await api.get("/store/items");
  expect(store.status()).toBe(403);

  const assistants = await api.get("/assistants");
  expect(assistants.status()).toBe(403);

  const trailingSlash = await api.post("/threads/", { data: {} });
  expect(trailingSlash.status()).toBe(403);

  const dirtyCreate = await api.post("/threads", { data: { metadata: 1 } });
  expect(dirtyCreate.status()).toBe(403);

  const crossDelete = await api.delete(`/threads/${shared.u2ThreadId}`);
  expect(crossDelete.status()).toBe(403);

  const crossState = await api.get(`/threads/${shared.u2ThreadId}/state`);
  expect(crossState.status()).toBe(404);

  await api.dispose();
});

test("open chat recovers when its active thread disappears", async ({ page }) => {
  test.setTimeout(120_000);
  await gateOnThreadStream();
  const run = readRun();
  const api = await memberApi(run, run.u1.token);
  const createdThreads = trackThreadCreations(page);

  await login(page, run, run.u1);
  await page.getByRole("button", { name: "New conversation" }).click();
  await send(page, "start reset scenario");
  await expectAssistantCount(page, "Hello from your coach.", 1);
  const missingThreadId = createdThreads[0] ?? "";
  expect(missingThreadId).not.toBe("");

  const deleted = await api.delete(`/threads/${missingThreadId}`);
  expect(deleted.ok()).toBe(true);

  await send(page, "continue after reset");

  const recoveryAlert = page.locator(".banner-error");
  await expect(recoveryAlert).toContainText(
    "That conversation is no longer available. Start a new one.",
  );
  await expect(recoveryAlert).not.toContainText("HTTP 404");
  await expect(page.locator(".bubble")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Nymble Coach" })).toBeVisible();
  for (const viewport of [
    { name: "mobile", width: 375, height: 812 },
    { name: "tablet", width: 768, height: 1024 },
    { name: "desktop", width: 1280, height: 720 },
  ]) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await expect(recoveryAlert).toBeVisible();
    await page.screenshot({
      path: path.join(SCREENSHOTS, `stale-thread-recovery-${viewport.name}.png`),
      fullPage: true,
    });
  }

  await api.dispose();
});
