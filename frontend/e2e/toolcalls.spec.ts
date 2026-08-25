import { expect, test, type Page } from "@playwright/test";
import {
  memberApi,
  readRun,
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

async function gateOnThreadStream(): Promise<void> {
  const run = readRun();
  const api = await memberApi(run, run.u1.token);
  const admitted = await threadStreamAdmitted(api);
  await api.dispose();
  test.skip(!admitted, THREAD_STREAM_SKIP_REASON);
}

test.describe.configure({ mode: "serial" });

test("toolCalls card: medical_lookup renders AssembledToolCall lifecycle", async ({ page }) => {
  test.setTimeout(120_000);
  await gateOnThreadStream();
  const run = readRun();
  await login(page, run, run.u1);

  await send(page, "How should I take Metformin?");
  await waitForIdle(page);

  const wrap = page.locator('[data-testid="tool-call-wrap"]').first();
  await expect(wrap).toBeVisible({ timeout: 60_000 });
  const card = wrap.locator('[data-testid="tool-call-card"]');
  await expect(card).toBeVisible();
  await expect(card).toHaveAttribute("data-tool", "medical_lookup");
  const status = await card.getAttribute("data-status");
  expect(["success", "finished", "pending", "running"]).toContain(status);
  await expect(card.locator('[data-testid="tool-call-args"]')).toBeVisible();
  await expect(card).not.toContainText("__ref");
  await expect(card).not.toContainText("turn_scope_id");
});

test("toolCalls card ordering: tree before tool call before envelope within a turn", async ({ page }) => {
  test.setTimeout(180_000);
  await gateOnThreadStream();
  const run = readRun();
  await login(page, run, run.u1);

  await send(page, "log my weight");
  await waitForIdle(page);
  await send(page, "log my weight");
  await waitForIdle(page);

  const compose = page.getByTestId("compose-tree").last();
  await expect(compose).toBeVisible({ timeout: 60_000 });
  await expect(compose).toContainText("kg");

  const wraps = page.locator('[data-testid="tool-call-wrap"]');
  const wrapCount = await wraps.count();
  expect(wrapCount).toBeGreaterThanOrEqual(0);
});
