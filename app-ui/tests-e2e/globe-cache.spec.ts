import { test, expect, type Page } from "@playwright/test";

/** §8.2 — Playwright with Supabase REST INTERCEPTED (page.route): rows shaped exactly like
 *  PostgREST (including Content-Range), built from the real CachedSummary/CachedVerdict types.
 *  This is test-side interception of a third party, NOT app-side mocking; the fixtures below are
 *  labelled as such. Engine stays DOWN (as in the smoke class). */

const TILE = "11SLT66";
const BUNDLE = "bundle-test-0001";
const BUNDLE_OLD = "bundle-test-0000";

/** test fixture: a minimal VALID LayerPayload (float32 2x2) */
const layerPayload = {
  name: "displacement",
  dtype: "<f4",
  shape: [2, 2],
  data_b64: Buffer.from(new Float32Array([0.01, -0.02, 0.03, 0.04]).buffer).toString("base64"),
  units: "m (LOS, + = toward sensor)",
  colormap: "diverging_teal_terracotta",
  vmin: -0.04,
  vmax: 0.04,
  note: "fixture layer",
  bounds_lonlat: [-118.3, 34.0, -118.2, 34.1],
};

const verdictRow = (pipeline: string, status: string, tier: string, bundleId: string, computedAt: string) => ({
  tile: TILE,
  pipeline,
  bundle_id: bundleId,
  epoch: "2026-08-06",
  status,
  tier,
  raw_p: 0.7,
  summary: "fixture verdict summary",
  run_id: "run-fixture0001",
  computed_at: computedAt,
  first_computed_at: computedAt,
  center_lon: -118.25,
  center_lat: 34.05,
  bbox_lonlat: [-118.3, 34.0, -118.2, 34.1],
  result: {
    pipeline,
    tile: TILE,
    epoch: "2026-08-06",
    raw_p: 0.7,
    status,
    tier,
    tier_label: "supported by evidence",
    summary: "fixture verdict summary",
    evidence: [{ id: "e1", kind: "detection", claim: "fixture claim", level: "decision" }],
    layers: [
      { name: "displacement", kind: "raster", crs: "EPSG:32611", bounds_lonlat: [-118.3, 34.0, -118.2, 34.1] },
      { name: "detected", kind: "raster", crs: "EPSG:32611", bounds_lonlat: [-118.3, 34.0, -118.2, 34.1] },
    ],
    data_used: ["fixture data"],
    references_used: ["fixture reference"],
    runtime_s: 1,
    warnings: [],
    min_epochs_met: true,
    bundle_id: bundleId,
    detail: {
      narrative: { headline: "fixture headline", reliability: "fixture reliability", note: "not a probability" },
      detections: [
        { id: `earthquake_coseismic:A005#2:0`, label: "cluster 1: 3 px (0.02 km², toward)", kind: "cluster",
          layer: "detected", centroid_lonlat: [-118.25, 34.05],
          bbox_lonlat: [-118.26, 34.04, -118.24, 34.06], area_km2: 0.02, polarity: "toward",
          metrics: { peak_abs_los_m: { value: 0.031, units: "m" } } },
      ],
      detections_truncated: false,
      layers: { displacement: layerPayload, detected: { ...layerPayload, name: "detected", dtype: "<u1",
        data_b64: Buffer.from(new Uint8Array([0, 1, 2, 0]).buffer).toString("base64"),
        categories: [{ value: 1, label: "toward sensor", tone: "positive" }, { value: 2, label: "away from sensor", tone: "negative" }] } },
    },
  },
});

const lightRow = (r: ReturnType<typeof verdictRow>) => {
  const { result, ...light } = r;
  void result;
  return light;
};

const EQ_NOW = verdictRow("earthquake_coseismic", "SUPPORTED", "supported", BUNDLE, "2026-10-28T10:00:00Z");
const EQ_OLD = verdictRow("earthquake_coseismic", "INSUFFICIENT_EVIDENCE", "insufficient", BUNDLE_OLD, "2026-10-20T10:00:00Z");

/** Headers real Supabase PostgREST sends on every response: the anon API is CORS-open and
 *  EXPOSES Content-Range (without Access-Control-Expose-Headers a cross-origin fetch cannot
 *  read it — the browser hides non-safelisted headers). The interceptor must match. */
const POSTGREST_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Expose-Headers": "Content-Range",
  "Content-Type": "application/json",
};

async function interceptSupabase(page: Page, opts?: { featured?: unknown[]; status?: number }) {
  const light = [lightRow(EQ_NOW), lightRow(EQ_OLD)];
  const full = [EQ_NOW, EQ_OLD];
  await page.route("**/rest/v1/verdicts**", async (route) => {
    const url = new URL(route.request().url());
    const select = url.searchParams.get("select") ?? "";
    if (select.startsWith("layer:layers->")) {
      const name = select.split("layers->")[1]?.split("&")[0] ?? "";
      await route.fulfill({
        status: 200,
        headers: POSTGREST_HEADERS,
        body: JSON.stringify([{ layer: (EQ_NOW.result.detail as { layers: Record<string, unknown> }).layers[name] ?? null }]),
      });
      return;
    }
    if (url.searchParams.get("bundle_id")) {
      // honour the filter like real PostgREST: only rows of THIS bundle come back
      const bid = (url.searchParams.get("bundle_id") ?? "").replace(/^eq\./, "");
      await route.fulfill({ status: 200, headers: POSTGREST_HEADERS, body: JSON.stringify(full.filter((r) => r.bundle_id === bid)) });
      return;
    }
    if (url.searchParams.get("tile")) {
      const t = (url.searchParams.get("tile") ?? "").replace(/^eq\./, "");
      await route.fulfill({ status: 200, headers: POSTGREST_HEADERS, body: JSON.stringify(full.filter((r) => r.tile === t)) });
      return;
    }
    await route.fulfill({
      status: 200,
      headers: { ...POSTGREST_HEADERS, "Content-Range": `0-${light.length - 1}/${light.length}` },
      body: JSON.stringify(light),
    });
  });
  await page.route("**/rest/v1/featured_areas**", async (route) => {
    if (opts?.status) {
      await route.fulfill({ status: opts.status, headers: POSTGREST_HEADERS, body: JSON.stringify({ message: "fixture error" }) });
      return;
    }
    await route.fulfill({
      status: 200,
      headers: POSTGREST_HEADERS,
      body: JSON.stringify(opts?.featured ?? [{
        slug: "fixture-area", title: "Fixture Featured Area", blurb: "from the test fixture",
        tile: TILE, center_lon: -118.25, center_lat: 34.05, zoom: 11.5, sort_order: 0,
        created_at: "2026-10-28T00:00:00Z",
      }]),
    });
  });
}

test.describe("globe with intercepted Supabase cache", () => {
  test("featured list renders, joins the cached row, and opens the result", async ({ page }) => {
    await interceptSupabase(page);
    await page.goto("/");
    await expect(page.getByText("Fixture Featured Area")).toBeVisible({ timeout: 20_000 });
    // the featured row joins to the cached verdict (tile 11SLT66)
    await expect(page.getByText(/supported by evidence|supported/).first()).toBeVisible();
    await page.getByRole("button", { name: "open result" }).first().click();
    // the cached result panel opens with the fixture's honest contents
    await expect(page.getByText(/Cached verdict · tile 11SLT66/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("fixture headline")).toBeVisible();
    await expect(page.getByText(/cached ·/).first()).toBeVisible();
    // detections render with the engine's own labels
    await expect(page.getByText(/cluster 1: 3 px/)).toBeVisible();
  });

  test("layer chip toggles the overlay and the legend appears", async ({ page }) => {
    await interceptSupabase(page);
    await page.goto("/");
    await page.getByRole("button", { name: "open result" }).first().click();
    await expect(page.getByText(/Cached verdict · tile 11SLT66/)).toBeVisible({ timeout: 15_000 });
    // toggle the displacement overlay on (chip inside the supported finding)
    await page.getByRole("button", { name: "displacement", exact: true }).first().click();
    // the legend shows the engine's units + the bbox note
    await expect(page.getByText("raster shown on its geographic bounding box")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("m (LOS, + = toward sensor)")).toBeVisible();
    // toggle the categorical layer: the engine's own category labels appear
    // (exact: the displacement legend's units text "…+ = toward sensor" is also on screen)
    await page.getByRole("button", { name: "detected", exact: true }).first().click();
    await expect(page.getByText("toward sensor", { exact: true })).toBeVisible();
    await expect(page.getByText("away from sensor", { exact: true })).toBeVisible();
  });

  test("cached tab groups by tile and lists earlier scans", async ({ page }) => {
    await interceptSupabase(page);
    await page.goto("/");
    await page.getByRole("tab", { name: "cached" }).click();
    await expect(page.getByText("11SLT66")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("2 pipeline verdict(s)")).toBeVisible();
    await expect(page.getByText(/1 earlier scan\(s\)/)).toBeVisible();
    await expect(page.getByText(/2 of 2 cached verdicts/)).toBeVisible();
    // open the full report link for the current bundle
    await expect(page.getByRole("link", { name: "full report" })).toHaveAttribute("href", `/verdicts/${BUNDLE}`);
  });

  test("empty featured list is an honest, specific empty state", async ({ page }) => {
    await interceptSupabase(page, { featured: [] });
    await page.goto("/");
    await expect(page.getByText(/No featured areas have been added yet/)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/tools\/feature_area\.py/)).toBeVisible();
  });

  test("a failing cache read shows the engine-shaped error, not an empty list", async ({ page }) => {
    await interceptSupabase(page, { status: 401 });
    await page.goto("/");
    await expect(page.getByText(/cache read failed \(HTTP 401\)/)).toBeVisible({ timeout: 20_000 });
  });
});
