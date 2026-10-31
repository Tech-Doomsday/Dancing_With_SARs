import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/** Problem 1 regression: Map → Data tab → "Search this box in Data retrieval" must land on
 *  /background/retrieval and STAY there.
 *
 *  The old bug: a map `moveend` (or a tile-at answer, or a panel change) armed a 350 ms debounce that
 *  later ran `router.replace("/map?…")`. A click on the link inside that window navigated to Retrieval
 *  and then the stale timer dragged the user back to /map.
 *
 *  The pending write is armed by a REAL map interaction here (mouse wheel on the canvas → maplibre
 *  moveend → onMove → pushUrl({ z })), observed through the camera write that moveend performs; the
 *  click then happens immediately, inside the debounce window. Engine answers are FIXTURES (routed
 *  below), not engine data. Run it on the pristine tree to see it fail (red) and on the fixed tree to
 *  see it pass (green). */
const FIXTURE = JSON.parse(readFileSync(join(__dirname, "fixtures", "granules-bbox.json"), "utf8"));
const TOKEN = "e2e-bg-token";
const TILE = {
  ok: true,
  tile: {
    id: "46QGJ79", epsg: 32646, size_m: 10000,
    bbox_lonlat: [90.37, 23.7, 90.47, 23.79],
    center_lonlat: [90.41847, 23.74797],
    polygon_lonlat: [[90.37, 23.7], [90.47, 23.7], [90.47, 23.79], [90.37, 23.79], [90.37, 23.7]],
  },
};

async function routeEngine(page: Page, counters: { granules: number }) {
  await page.addInitScript((t) => {
    try { window.sessionStorage.setItem("nisar_bg_token", t); } catch { /* ignore */ }
    // record every camera write (= every map moveend)
    const w = window as unknown as Record<string, unknown>;
    w.__cameraWrites = 0;
    const set = Storage.prototype.setItem;
    Storage.prototype.setItem = function (k: string, v: string) {
      if (k === "nisar:map-camera:v1") (w.__cameraWrites as number)++;
      return set.call(this, k, v);
    };
  }, TOKEN);
  await page.route("**/api/health**", (r) => r.fulfill({ json: { ok: true, scans_enabled: false, earthdata_credentials: "present", cache_write: { status: "disabled", reason: null } } }));
  await page.route("**/api/tile-at**", (r) => r.fulfill({ json: TILE }));
  await page.route("**/api/pipelines**", (r) => r.fulfill({ json: { ok: true, pipelines: [], implemented_count: 0 } }));
  await page.route("**/api/granules**", (r) => {
    if (new URL(r.request().url()).searchParams.has("west")) counters.granules += 1;
    return r.fulfill({ json: FIXTURE });
  });
}

const cameraWrites = (page: Page) => page.evaluate(() => (window as unknown as { __cameraWrites: number }).__cameraWrites);

/** open the Data tab with the tile resolved, return the link */
async function openDataTab(page: Page) {
  await page.goto("/map?lat=23.74797&lon=90.41847");
  await page.getByRole("tab", { name: "Data" }).click();
  const link = page.getByRole("link", { name: /Search this box in Data retrieval/ });
  await expect(link).toHaveAttribute("href", /\/background\/retrieval\?/, { timeout: 15_000 });
  return link;
}

/** a real interaction on the canvas, then wait until maplibre's moveend actually fired */
async function moveMapForReal(page: Page) {
  const canvas = page.locator(".maplibregl-canvas").first();
  await expect(canvas).toBeVisible({ timeout: 20_000 });
  const box = (await canvas.boundingBox())!;
  const before = await cameraWrites(page);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -240);
  await expect.poll(() => cameraWrites(page), { timeout: 10_000 }).toBeGreaterThan(before);
}

test.describe("Map → Data retrieval does not bounce back", () => {
  test("click inside the debounce window: lands on Retrieval and stays; boxes/dates carry over; one search; Back goes to /map", async ({ page }) => {
    const counters = { granules: 0 };
    await routeEngine(page, counters);
    const link = await openDataTab(page);

    const mapFields = {
      west: await page.getByLabel("West").first().inputValue(),
      south: await page.getByLabel("South").first().inputValue(),
      east: await page.getByLabel("East").first().inputValue(),
      north: await page.getByLabel("North").first().inputValue(),
      from: await page.getByLabel("From (UTC)").first().inputValue(),
      to: await page.getByLabel("To (UTC)").first().inputValue(),
    };

    await moveMapForReal(page);          // moveend has just armed the 350 ms URL write
    await link.click();                  // …and we leave inside that window

    await page.waitForURL(/\/background\/retrieval/, { timeout: 15_000 });
    await page.waitForTimeout(1500);     // the old stale timer fired inside this wait
    expect(new URL(page.url()).pathname).toBe("/background/retrieval");

    await expect(page.getByLabel("West").first()).toHaveValue(mapFields.west);
    await expect(page.getByLabel("South").first()).toHaveValue(mapFields.south);
    await expect(page.getByLabel("East").first()).toHaveValue(mapFields.east);
    await expect(page.getByLabel("North").first()).toHaveValue(mapFields.north);
    await expect(page.getByLabel("From (UTC)").first()).toHaveValue(mapFields.from);
    await expect(page.getByLabel("To (UTC)").first()).toHaveValue(mapFields.to);
    expect(counters.granules, "exactly one bbox search").toBe(1);

    // Back returns to the map and does NOT bounce forward again
    await page.goBack();
    await page.waitForURL(/\/map(\?|$)/, { timeout: 15_000 });
    await page.waitForTimeout(1200);
    expect(new URL(page.url()).pathname).toBe("/map");
  });

  test("Ctrl+click opens Retrieval in a new page and the Map page is left alone", async ({ page, context }) => {
    const counters = { granules: 0 };
    await routeEngine(page, counters);
    const link = await openDataTab(page);
    await moveMapForReal(page);
    const popup = context.waitForEvent("page");
    await link.click({ modifiers: ["Control"] });
    const np = await popup;
    await np.waitForLoadState("domcontentloaded");
    expect(new URL(np.url()).pathname).toBe("/background/retrieval");
    await page.waitForTimeout(800);
    expect(new URL(page.url()).pathname).toBe("/map");   // original tab stays on the map
  });
});
