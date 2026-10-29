import { defineConfig } from "@playwright/test";

/** LIVE-engine config (§8.2): uvicorn engine.api.main against a temp NISAR_DATA_DIR seeded
 *  through tests/common_fixtures.py (SYNTHETIC, labelled), plus a dev server pointed at it.
 *  Run: npm run test:e2e:live  — separate from the main config, whose suite must always see
 *  the engine DOWN. The live-scan flow itself is covered by the Python tests with an injected
 *  retriever; here we verify the UI flows against a real HTTP engine. */
export default defineConfig({
  testDir: "./tests-e2e-live",
  timeout: 60_000,
  use: {
    baseURL: "http://127.0.0.1:3200",
    headless: true,
  },
  webServer: [
    {
      command: "python tests-e2e-live/seed_engine.py --port 8907",
      url: "http://127.0.0.1:8907/api/health",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      // the dev UI runs on :3200 — the engine's CORS list (D9) must include it
      env: { NISAR_UI_ORIGINS: "http://127.0.0.1:3200,http://localhost:3200" },
    },
    {
      command: "npx next dev -p 3200",
      url: "http://127.0.0.1:3200",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        NEXT_PUBLIC_ENGINE_API: "http://127.0.0.1:8907",
        NEXT_PUBLIC_SUPABASE_URL: "http://127.0.0.1:39999",
        NEXT_PUBLIC_SUPABASE_ANON_KEY: "test-anon-key-public-by-design",
      },
    },
  ],
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
