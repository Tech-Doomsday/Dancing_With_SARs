import { test, expect, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";

/** §8.3 visual verification — engine-DOWN shots (main config). Gated: run
 *  `SCREENSHOTS=1 npx playwright test tests-e2e/screenshots.spec.ts` so the normal suite
 *  stays fast. Output: test-results/screens/<name>-{1440x900,390x844}.png */

const SHOTS = "test-results/screens";
const SIZES: [string, number, number][] = [["1440x900", 1440, 900], ["390x844", 390, 844]];

test.skip(process.env.SCREENSHOTS !== "1", "visual-verification shots — run with SCREENSHOTS=1");

async function snap(page: Page, name: string, w: number, h: number) {
  await page.setViewportSize({ width: w, height: h });
  await page.waitForTimeout(400); // let overlays/settle
  await page.screenshot({ path: `${SHOTS}/${name}-${w}x${h}.png` });
}

const verdictRow = (tier: string, status: string, bundleId: string, computedAt: string) => ({
  tile: "11SLT66", pipeline: "earthquake_coseismic", bundle_id: bundleId, epoch: "2026-08-06",
  status, tier, raw_p: 0.7, summary: "fixture verdict summary", run_id: "run-fixture0001",
  computed_at: computedAt, first_computed_at: computedAt,
  center_lon: -118.25, center_lat: 34.05, bbox_lonlat: [-118.3, 34.0, -118.2, 34.1],
  result: {
    pipeline: "earthquake_coseismic", tile: "11SLT66", epoch: "2026-08-06", raw_p: 0.7,
    status, tier, tier_label: "supported by evidence", summary: "fixture verdict summary",
    evidence: [{ id: "e1", kind: "detection", claim: "fixture claim", level: "decision" }],
    layers: [
      { name: "displacement", kind: "raster", crs: "EPSG:32611", bounds_lonlat: [-118.3, 34.0, -118.2, 34.1] },
      { name: "detected", kind: "raster", crs: "EPSG:32611", bounds_lonlat: [-118.3, 34.0, -118.2, 34.1] },
    ],
    data_used: ["fixture data"], references_used: ["fixture reference"], runtime_s: 1,
    warnings: [], min_epochs_met: true, bundle_id: bundleId,
    detail: {
      narrative: { headline: "fixture headline", reliability: "fixture reliability", note: "not a probability" },
      detections: [{ id: "earthquake_coseismic:A005#2:0", label: "cluster 1: 3 px (0.02 km², toward)",
        kind: "cluster", layer: "detected", centroid_lonlat: [-118.25, 34.05],
        bbox_lonlat: [-118.26, 34.04, -118.24, 34.06], area_km2: 0.02, polarity: "toward",
        metrics: { peak_abs_los_m: { value: 0.031, units: "m" } } }],
      detections_truncated: false,
      layers: {
        displacement: {
          name: "displacement", dtype: "<f4", shape: [2, 2],
          data_b64: Buffer.from(new Float32Array([0.01, -0.02, 0.03, 0.04]).buffer).toString("base64"),
          units: "m (LOS, + = toward sensor)", colormap: "diverging_teal_terracotta",
          vmin: -0.04, vmax: 0.04, note: "fixture layer", bounds_lonlat: [-118.3, 34.0, -118.2, 34.1],
        },
        detected: {
          name: "detected", dtype: "<u1", shape: [2, 2],
          data_b64: Buffer.from(new Uint8Array([0, 1, 2, 0]).buffer).toString("base64"),
          note: "fixture layer", bounds_lonlat: [-118.3, 34.0, -118.2, 34.1],
          categories: [{ value: 1, label: "toward sensor", tone: "positive" }, { value: 2, label: "away from sensor", tone: "negative" }],
        },
      },
    },
  },
});

const POSTGREST_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Expose-Headers": "Content-Range",
  "Content-Type": "application/json",
};

async function interceptSupabase(page: Page, opts?: { featured?: unknown[] }) {
  const full = [verdictRow("supported", "SUPPORTED", "bundle-test-0001", "2026-10-28T10:00:00Z")];
  const light = full.map(({ result, ...rest }) => { void result; return rest; });
  await page.route("**/rest/v1/verdicts**", async (route) => {
    const url = new URL(route.request().url());
    const select = url.searchParams.get("select") ?? "";
    if (select.startsWith("layer:layers->")) {
      const name = select.split("layers->")[1]?.split("&")[0] ?? "";
      await route.fulfill({ status: 200, headers: POSTGREST_HEADERS,
        body: JSON.stringify([{ layer: (full[0].result.detail as { layers: Record<string, unknown> }).layers[name] ?? null }]) });
      return;
    }
    if (url.searchParams.get("bundle_id") || url.searchParams.get("tile")) {
      await route.fulfill({ status: 200, headers: POSTGREST_HEADERS, body: JSON.stringify(full) });
      return;
    }
    await route.fulfill({ status: 200,
      headers: { ...POSTGREST_HEADERS, "Content-Range": `0-${light.length - 1}/${light.length}` },
      body: JSON.stringify(light) });
  });
  await page.route("**/rest/v1/featured_areas**", async (route) => {
    await route.fulfill({ status: 200, headers: POSTGREST_HEADERS,
      body: JSON.stringify(opts?.featured ?? [{
        slug: "fixture-area", title: "Fixture Featured Area", blurb: "from the test fixture",
        tile: "11SLT66", center_lon: -118.25, center_lat: 34.05, zoom: 11.5, sort_order: 0,
        created_at: "2026-10-28T00:00:00Z",
      }]) });
  });
}

test.describe("screenshots (engine down)", () => {
  test.beforeAll(() => { mkdirSync(SHOTS, { recursive: true }); });

  test("engine offline + initial globe + cached tab + featured empty + webgl unavailable", async ({ browser }) => {
    // engine offline (plain /) — also the initial-globe offline strip
    {
      const ctx = await browser.newContext();
      const page = await ctx.newPage();
      for (const [label, w, h] of SIZES) {
        await page.goto("/map");
        await expect(page.getByText(/engine offline/).first()).toBeVisible({ timeout: 20_000 });
        await snap(page, label === "1440x900" ? "engine-offline" : "engine-offline", w, h)
          .then(() => { void label; });
      }
      await ctx.close();
    }
    // cached tab (intercepted Supabase)
    {
      const ctx = await browser.newContext();
      const page = await ctx.newPage();
      await interceptSupabase(page);
      await page.goto("/map");
      await page.getByRole("tab", { name: "cached" }).click();
      await expect(page.getByText("11SLT66").first()).toBeVisible({ timeout: 20_000 });
      for (const [, w, h] of SIZES) await snap(page, "cached-tab", w, h);
      await ctx.close();
    }
    // featured empty state
    {
      const ctx = await browser.newContext();
      const page = await ctx.newPage();
      await interceptSupabase(page, { featured: [] });
      await page.goto("/map");
      await expect(page.getByText(/No featured areas have been added yet/)).toBeVisible({ timeout: 20_000 });
      for (const [, w, h] of SIZES) await snap(page, "featured-empty", w, h);
      await ctx.close();
    }
    // WebGL unavailable: the panel still works, the map shows a clear message
    {
      const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      const page = await ctx.newPage();
      await interceptSupabase(page);
      // block the maplibre WebGL context by disabling 3D APIs
      await page.addInitScript(() => {
        const orig = HTMLCanvasElement.prototype.getContext;
        // test shim: force WebGL context creation to fail so the WebGL-unavailable state renders
        HTMLCanvasElement.prototype.getContext = function (type: string, ...rest: unknown[]) {
          if (typeof type === "string" && type.includes("webgl")) return null;
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          return (orig as any).apply(this, [type, ...rest]);
        };
      });
      await page.goto("/map");
      await expect(page.getByText(/WebGL/i).first()).toBeVisible({ timeout: 20_000 });
      await snap(page, "webgl-unavailable", 1440, 900);
      await ctx.close();
    }
  });
});
