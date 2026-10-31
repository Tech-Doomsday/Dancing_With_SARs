import { defineConfig } from "@playwright/test";

/** Production-build run of the globe-navigation spec (no React StrictMode double-mount):
 *  `npm run build && PROD_BUILD=1 npx playwright test -c playwright.nav-prod.config.ts` */
export default defineConfig({
  testDir: "./tests-e2e",
  testMatch: /globe-navigation\.spec\.ts/,
  timeout: 30_000,
  use: { baseURL: "http://127.0.0.1:3102", headless: true },
  webServer: {
    command: "npm run start -- -p 3102",
    url: "http://127.0.0.1:3102",
    reuseExistingServer: false,
    timeout: 120_000,
    env: { NEXT_PUBLIC_SUPABASE_URL: "http://127.0.0.1:39999", NEXT_PUBLIC_SUPABASE_ANON_KEY: "test-anon-key-public-by-design" },
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
