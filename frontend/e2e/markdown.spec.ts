import { expect, test, type Page } from "@playwright/test";
import {
  memberApi,
  readRun,
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

test("markdown rendering: heading, bold, list and link render via Markdown component", async ({ page }) => {
  test.setTimeout(120_000);
  const run = readRun();
  await login(page, run, run.u1);
  await page.getByRole("button", { name: "New conversation" }).click();

  await send(page, "show markdown");
  await waitForIdle(page);

  const bubble = page.locator(".bubble.assistant").last();
  await expect(bubble).toBeVisible({ timeout: 60_000 });

  const mdRoot = bubble.locator(".md-root");
  await expect(mdRoot).toBeVisible({ timeout: 30_000 });
  await expect(mdRoot.locator(".md-h1")).toContainText("Test Heading");
  await expect(mdRoot.locator(".md-strong")).toContainText("bold");
  await expect(mdRoot.locator(".md-ul .md-li")).toHaveCount(2);
  const link = mdRoot.locator(".md-a");
  await expect(link).toHaveAttribute("target", "_blank");
  // The Presidio identifier sanitizer (privacy.py, URL entity) redacts link
  // URLs in member-visible content — the anchor must never carry the raw URL.
  const href = await link.getAttribute("href");
  expect(href).not.toContain("example.com");
  expect(href).toContain("REDACTED_URL");
  await expect(link).toHaveText("docs");
  await expect(bubble).not.toContainText("__ref");
  await expect(bubble).not.toContainText("```");
});

test("markdown table: GFM table renders with md-table classes", async ({ page }) => {
  test.setTimeout(120_000);
  const run = readRun();
  await login(page, run, run.u1);
  await page.getByRole("button", { name: "New conversation" }).click();

  await send(page, "show markdown table");
  await waitForIdle(page);

  const bubble = page.locator(".bubble.assistant").last();
  await expect(bubble).toBeVisible({ timeout: 60_000 });
  const mdRoot = bubble.locator(".md-root");
  await expect(mdRoot).toBeVisible({ timeout: 30_000 });
  await expect(mdRoot.locator(".md-table")).toBeVisible();
  await expect(mdRoot.locator(".md-th")).toHaveCount(2);
  await expect(mdRoot.locator(".md-td")).toHaveCount(4);
});
