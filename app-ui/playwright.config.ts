import { defineConfig } from "@playwright/test";

/** Playwright config — the smoke test runs against the DEV SERVER with the engine API
 *  deliberately absent (127.0.0.1:8900 refused): the UI must degrade honestly. */
export default defineConfig({
  testDir: "./tests-e2e",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:3100",
    headless: true,
  },
  webServer: {
    command: "npm run dev -- -p 3100",
    url: "http://127.0.0.1:3100",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
