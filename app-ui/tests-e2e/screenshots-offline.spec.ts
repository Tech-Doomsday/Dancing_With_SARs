import { test, expect, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";

/** §8.3 visual verification — OFFLINE shots (engine DOWN; Supabase intercepted with fixtures,
 *  same shapes as globe-cache.spec.ts). Covers the states the live suite cannot produce:
 *  cached tab open with a result, featured-empty state, engine offline notice, and the
 *  WebGL-unavailable fallback (chromium launched with GPU disabled). */

const SHOTS = "test-results/screens";

async function snap(page: Page, name: string, w: number, h: number) {
  await page.setViewportSize({ width: w, height: h });
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${SHOTS}/${name}-${w}x${h}.png` });
}

const TILE = "11SLT66";
const BUNDLE = "bundle-test-0001";

const verdictRow = (pipeline: string, status: string, tier: string) => ({
  tile: TILE,
  pipeline,
  bundle_id: BUNDLE,
  epoch: "2026-08-06",
  status,
  tier,
  raw_p: 0.7,
  summary: "fixture verdict summary",
  run_id: "run-fixture0001",
  computed_at: new Date(Date.now() - 3_600_000).toISOString(),
  first_computed_at: new Date(Date.now() - 3_600_000).toISOString(),
  center_lon: -118.25,
  center_lat: 34.05,
  bbox_lonlat: [-118.3, 34.0, -118.2, 34.1],
  result: {
    pipeline, tile: TILE, epoch: "2026-08-06", raw_p: 0.7, status, tier,
    tier_label: "supported by evidence", summary: "fixture verdict summary",
    evidence: [{ id: "e1", kind: "detection", claim: "fixture claim", level: "decision" }],
    layers: [], data_used: ["fixture data"], references_used: ["fixture reference"],
    runtime_s: 1, warnings: [], min_epochs_met: true, bundle_id: BUNDLE, detail: {},
  },
});

async function routeCache(page: Page, rows: unknown[], total: string) {
  await page.route("**/rest/v1/verdicts**", (r) =>
    r.fulfill({ headers: { "Content-Range": `0-${Math.max(0, rows.length - 1)}/${total}` },
                json: rows }));
  await page.route("**/rest/v1/featured_areas**", (r) => r.fulfill({ json: [] }));
}

test("offline states: cached tab, featured empty, engine offline, WebGL unavailable", async ({ browser }) => {
  test.setTimeout(180_000);
  mkdirSync(SHOTS, { recursive: true });

  // cached tab open with a result (overlay errors honestly noted; no engine needed)
  {
    const page = await browser.newPage();
    await routeCache(page, [verdictRow("earthquake_coseismic", "SUPPORTED", "supported")], "1");
    await page.goto("/");
    await page.goto(`/?panel=results&bundle=${BUNDLE}`);
    await expect(page.getByText("earthquake_coseismic").first()).toBeVisible({ timeout: 20_000 });
    for (const [w, h] of [[1440, 900], [390, 844]] as [number, number][]) {
      await snap(page, "cached-tab", w, h);
    }
    // engine offline notice (the engine is genuinely down in this config)
    await page.goto("/");
    await expect(page.getByText(/engine offline at/).first()).toBeVisible({ timeout: 20_000 });
    await snap(page, "engine-offline", 1440, 900);
    await page.close();
  }

  // featured empty state (honest, specific)
  {
    const page = await browser.newPage();
    await routeCache(page, [], "0");
    await page.goto("/?panel=explore");
    await expect(page.getByText(/No featured areas have been added yet/).first()).toBeVisible({ timeout: 20_000 });
    await snap(page, "featured-empty", 1440, 900);
    await page.close();
  }

  // WebGL unavailable is covered honestly in webgl-off.spec.ts (real GPU-less chromium).
});
