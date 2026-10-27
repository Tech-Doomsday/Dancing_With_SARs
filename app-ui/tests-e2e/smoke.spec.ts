import { test, expect } from "@playwright/test";

/** Playwright smoke test — the UI must render honestly against the REAL contract:
 *  the engine API is expected to be DOWN in CI, and the page must say so clearly
 *  (no fake data, no infinite "Loading run…" — the no-mock rule, enforced in the UI). */

test("home page renders the honest headline and engine-health line", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Did the ground move?");
  await expect(page.getByText(/engine data dir:/)).toBeVisible();
});

test("run page with an unknown run surfaces the API error, never 'Loading run…' forever", async ({ page }) => {
  await page.goto("/runs/run-does-not-exist");
  // pollWhile now reports the error through onError -> the page shows it as an alert
  await expect(page.getByRole("alert").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Loading run…")).toHaveCount(0, { timeout: 15_000 });
});
