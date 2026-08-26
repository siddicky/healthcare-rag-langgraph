import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import {
  readRun,
  type Runfile,
  type RunIdentity,
} from "./run";

const COMPOSER = 'textarea[aria-label="Message your coach"]';
const SCREENSHOTS = path.join(__dirname, "__screenshots__");

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

test("medical_lookup renders one final answer without a raw tool call", async ({ page }) => {
  test.setTimeout(120_000);
  const run = readRun();
  await login(page, run, run.u1);
  await page.getByRole("button", { name: "New conversation" }).click();

  await send(page, "How should I take Metformin?");
  await waitForIdle(page);

  const answer = "Offline monograph answer for: How should I take Metformin?";
  await expect(page.getByText(answer, { exact: true })).toHaveCount(1);
  await expect(page.locator('[data-testid="tool-call-wrap"]')).toHaveCount(0);
});

test("synthetic demo renders metric, injection, and calendar UI without raw tool calls", async ({ page }) => {
  test.setTimeout(180_000);
  const run = readRun();
  await login(page, run, run.u1);
  await page.getByRole("button", { name: "New conversation" }).click();

  await send(page, "show the generative ui demo");
  await waitForIdle(page);

  await expect(page.getByTestId("log-metric-card")).toContainText("Waist", { timeout: 60_000 });
  await expect(page.getByTestId("log-injection-card")).toContainText("Demo medication");
  await expect(page.getByTestId("view-schedule-card")).toBeVisible();
  await expect(page.getByText("Here is your generative UI demo.", { exact: true })).toHaveCount(1);
  await expect(page.locator('[data-testid="tool-call-wrap"]')).toHaveCount(0);
  await page.getByTestId("log-metric-card").screenshot({ path: path.join(SCREENSHOTS, "synthetic-trend-card.png") });
  await page.getByTestId("log-injection-card").screenshot({ path: path.join(SCREENSHOTS, "synthetic-injection-card.png") });
  await page.getByTestId("view-schedule-card").screenshot({ path: path.join(SCREENSHOTS, "synthetic-calendar-card.png") });
});
