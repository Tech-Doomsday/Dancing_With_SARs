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
    // the anon key is public by design (RLS read-only); the URL is a dead local port that the
    // intercept spec routes away — un-routed calls fail and the UI says so honestly
    env: {
      NEXT_PUBLIC_SUPABASE_URL: "http://127.0.0.1:39999",
      NEXT_PUBLIC_SUPABASE_ANON_KEY: "test-anon-key-public-by-design",
    },
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
