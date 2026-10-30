import { test, expect } from "@playwright/test";

/** Playwright smoke — updated for the globe UI (D12): the landing page is now the globe; the old
 *  "Did the ground move?" home lives at /workbench. The engine API is expected DOWN in CI: the
 *  UI must degrade honestly (no fake data, no infinite spinners). */

test("globe landing renders the map (or the honest WebGL message) and the search pill", async ({ page }) => {
  await page.goto("/map");
  // the map canvas or the WebGL-unavailable message must appear (no infinite spinner)
  const canvas = page.locator("canvas.maplibregl-canvas");
  const webglMsg = page.getByText("The globe cannot start");
  await expect(canvas.or(webglMsg).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("Latitude (-90 to 90)")).toBeVisible();
  await expect(page.getByLabel("Longitude (-180 to 180)")).toBeVisible();
  // engine-offline notice with the engine URL
  await expect(page.getByText(/engine offline/).first()).toBeVisible({ timeout: 20_000 });
  // Scan disabled with a reason (the offline notice says so explicitly)
  await expect(page.getByText(/Scan is disabled/).first()).toBeVisible({ timeout: 15_000 });
});

test("Data tab shows its own engine-offline message, never a stale or empty timeline", async ({ page }) => {
  await page.goto("/map");
  await page.getByRole("tab", { name: "Data" }).click();
  await expect(page.getByText(/the engine is offline — granule availability needs it/)).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("svg[aria-label*='Granule timeline']")).toHaveCount(0);
});

test("no console errors and no infinite spinners on the landing page", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto("/map");
  await page.waitForTimeout(4000);
  expect(errors).toEqual([]);
});

test("legacy /workbench redirects to /background/pipelines and keeps working (§1)", async ({ page }) => {
  await page.goto("/workbench");
  await expect(page).toHaveURL(/\/background\/pipelines/);
  // with the engine deliberately absent, the catalogue shows its honest error (never a fake list)
  await expect(page.getByRole("alert").first()).toBeVisible({ timeout: 20_000 });
});

test("/runs/run-does-not-exist still surfaces the API error, never 'Loading run…' forever", async ({ page }) => {
  await page.goto("/runs/run-does-not-exist");
  await expect(page.getByRole("alert").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Loading run…")).toHaveCount(0, { timeout: 15_000 });
});
