import { defineConfig } from "@playwright/test";

/**
 * Hermetic E2E config. The global setup boots the full offline stack via
 * `e2e/server.py` (scripted OpenAI/Supabase/LangSmith gateway + real
 * `langgraph dev` + production `next start`) and tears it down afterwards.
 *
 * Workers are pinned to ONE: the stack binds ephemeral ports exactly once
 * and the in-process fixture state (scripted turns, feedback mirror) is
 * order-dependent by design.
 *
 * There is no static `baseURL`: ports are allocated by server.py at boot,
 * so tests read the JSON runfile (`.tmp/run.json`, see `e2e/run.ts`) and
 * navigate with absolute URLs derived from it.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 240_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  outputDir: "./e2e/.tmp/test-results",
  use: {
    headless: true,
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
  },
});
