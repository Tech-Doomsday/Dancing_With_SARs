import { test, expect, chromium, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";

/** §8.3 — the WebGL-unavailable state, produced HONESTLY: chromium is launched with WebGL
 *  disabled (--disable-webgl --disable-webgl2 --disable-3d-apis), so maplibre's Map constructor
 *  really throws and the UI's real fallback message renders; the panel must keep working. */

const SHOTS = "test-results/screens";

async function routeOffline(page: Page) {
  // Supabase configured but empty; engine down (the offline config's default world)
  await page.route("**/rest/v1/**", (r) => r.fulfill({ headers: { "Content-Range": "0-0/0" }, json: [] }));
}

async function snap(page: Page, name: string, w: number, h: number) {
  await page.setViewportSize({ width: w, height: h });
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${SHOTS}/${name}-${w}x${h}.png` });
}

test("WebGL unavailable: the globe says so; search/lists/scan-status still work", async () => {
  test.setTimeout(180_000);
  mkdirSync(SHOTS, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    args: ["--disable-webgl", "--disable-webgl2", "--disable-3d-apis", "--disable-gpu"],
  });
  const page: Page = await browser.newPage();
  await routeOffline(page);
  await page.goto("/");
  await page.waitForTimeout(4_000);
  // the honest fallback message is on the page, the search pill still exists
  await expect(page.getByText(/The globe cannot start|could not start WebGL/i).first())
    .toBeVisible({ timeout: 20_000 });
  await expect(page.getByLabel("Latitude (-90 to 90)")).toBeVisible();
  await expect(page.getByText(/engine offline at/).first()).toBeVisible({ timeout: 20_000 });
  await snap(page, "webgl-unavailable", 1440, 900);
  await snap(page, "webgl-unavailable", 390, 844);
  await browser.close();
});
