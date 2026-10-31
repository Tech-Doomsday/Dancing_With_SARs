import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/** Globe navigation (Phase 1). ENGINE responses are FIXTURES (routed in this file), not engine data.
 *  Flow: Granule Explorer -> "Show on globe" must be an in-app navigation:
 *    · no document reload (a window marker set before the click survives),
 *    · the map is created ONCE (a MutationObserver counts the `maplibregl-map` class being added),
 *    · the camera is at the target (<= 0.01°) with zoom >= 7, and no world-view camera is ever
 *      stored in between (sessionStorage.setItem is wrapped to log every camera write). */
const FIXTURE = JSON.parse(readFileSync(join(__dirname, "fixtures", "granules-bbox.json"), "utf8")) as {
  granules: { id: string; footprint: [number, number][] | null }[];
};
// the committed explorer fixture (labelled as a fixture; generated from the engine's enrichment code)
const GRANULE = FIXTURE.granules.find((g) => g.id.includes("GUNW_031_034_A_019_033_4000_SH") && g.footprint?.length) ??
  FIXTURE.granules.find((g) => g.footprint?.length)!;
const TILE = {
  ok: true,
  tile: {
    id: "11SLT86", epsg: 32611, size_m: 10000,
    bbox_lonlat: [-118.45, 33.7, -118.05, 34.3],
    center_lonlat: [-118.25, 34.05],
    polygon_lonlat: [[-118.45, 33.7], [-118.05, 33.7], [-118.05, 34.3], [-118.45, 34.3], [-118.45, 33.7]],
  },
};

test("Show on globe: in-app navigation, one map, camera at the target, no world-view frame", async ({ page }) => {
  await page.addInitScript(() => {
    const w = window as unknown as Record<string, unknown>;
    w.__maps = 0;
    w.__cameraWrites = [] as string[];
    const orig = Storage.prototype.setItem;
    Storage.prototype.setItem = function (k: string, v: string) {
      if (k === "nisar:map-camera:v1") (w.__cameraWrites as string[]).push(v);
      return orig.call(this, k, v);
    };
    const seen = new WeakSet<Element>();
    new MutationObserver((muts) => {
      for (const m of muts) {
        const t = m.target as Element;
        if (t.classList?.contains("maplibregl-map") && !seen.has(t)) { seen.add(t); w.__maps = (w.__maps as number) + 1; }
      }
    }).observe(document, { subtree: true, attributes: true, attributeFilter: ["class"] });
  });
  await page.route("**/api/health**", (r) => r.fulfill({ json: { ok: true, scans_enabled: false, earthdata_credentials: "present", cache_write: { status: "disabled", reason: null } } }));
  await page.route("**/api/tile-at**", (r) => r.fulfill({ json: TILE }));
  await page.route("**/api/pipelines**", (r) => r.fulfill({ json: { ok: true, pipelines: [], implemented_count: 0 } }));
  await page.route("**/api/granules**", (r) => r.fulfill({ json: FIXTURE }));

  await page.goto("/granules?west=-118.45&south=33.7&east=-118.05&north=34.3&level=GUNW,GCOV");
  await page.evaluate(() => { (window as unknown as Record<string, unknown>).__marker = "same-document"; });

  // open the granule's detail through the List view (deterministic), then click "Show on globe"
  await page.getByLabel("List view").check();
  await page.locator("tr", { hasText: GRANULE.id }).first().click();
  const show = page.getByRole("button", { name: /show on (the )?globe/i }).first();
  await expect(show).toBeEnabled({ timeout: 10_000 });
  await show.click();

  await page.waitForURL(/\/map\?lat=/, { timeout: 15_000 });
  await expect.poll(() => page.evaluate(() => (window as unknown as { __maps: number }).__maps), { timeout: 20_000 }).toBeGreaterThanOrEqual(1);
  await page.waitForTimeout(2500);   // any later remount / fly-in would show up here

  expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__marker)).toBe("same-document");
  const navs = await page.evaluate(() => performance.getEntriesByType("navigation").length);
  expect(navs).toBe(1);
  // The map is created ONCE. React StrictMode (dev server only) mounts every effect twice, which can show up as
  // one extra create+remove; the production-build run (playwright.nav-prod.config.ts) asserts 1.
  const maps = await page.evaluate(() => (window as unknown as { __maps: number }).__maps);
  if (process.env.PROD_BUILD) expect(maps).toBe(1);
  else expect(maps).toBeLessThanOrEqual(2);   // dev StrictMode: 1 or 2 depending on whether the container element is reused
  expect(await page.locator(".maplibregl-map").count(), "exactly one live map").toBe(1);

  const writes = (await page.evaluate(() => (window as unknown as { __cameraWrites: string[] }).__cameraWrites)).map((s) => JSON.parse(s));
  expect(writes.length).toBeGreaterThan(0);
  for (const c of writes) expect(c.zoom, "a world-view camera was stored").toBeGreaterThanOrEqual(7);
  const last = writes[writes.length - 1];
  const [wlat, wlon] = [Number(new URL(page.url()).searchParams.get("lat")), Number(new URL(page.url()).searchParams.get("lon"))];
  expect(Math.abs(last.center[0] - wlon)).toBeLessThan(0.01);
  expect(Math.abs(last.center[1] - wlat)).toBeLessThan(0.01);
});
