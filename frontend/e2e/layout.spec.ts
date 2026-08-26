import { expect, test } from "@playwright/test";
import { readRun } from "./run";

const COMPOSER = 'textarea[aria-label="Message your coach"]';

test("keeps a short conversation above the composer", async ({ page }, testInfo) => {
  // Given: a desktop chat with a fresh conversation.
  const run = readRun();
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto(`${run.frontend_url}/login`);
  await page.fill("#email", run.u1.email);
  await page.fill("#password", run.u1.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(`${run.frontend_url}/chat`);
  await expect(page.locator(COMPOSER)).toBeVisible();
  await page.getByRole("button", { name: "New conversation" }).click();

  // When: the member sends one message and the coach replies.
  await page.fill(COMPOSER, "hello there");
  await page.press(COMPOSER, "Enter");
  await expect(page.locator(".bubble.assistant", { hasText: "Hello from your coach." })).toBeVisible({
    timeout: 60_000,
  });

  // Then: the short transcript rests at the bottom of its scroll region.
  const viewports = [
    { name: "desktop", width: 1280, height: 800 },
    { name: "tablet", width: 768, height: 900 },
    { name: "mobile", width: 375, height: 812 },
  ] as const;
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    const scrollBox = await page.locator(".thread-scroll").boundingBox();
    const lastItemBox = await page.locator(".thread-inner > :last-child").boundingBox();
    if (scrollBox === null || lastItemBox === null) throw new Error("chat transcript has no layout box");
    const bottomInset = scrollBox.y + scrollBox.height - (lastItemBox.y + lastItemBox.height);
    expect(bottomInset).toBeGreaterThanOrEqual(20);
    expect(bottomInset).toBeLessThanOrEqual(28);
    await page.screenshot({ path: testInfo.outputPath(`${viewport.name}.png`) });
  }
});
