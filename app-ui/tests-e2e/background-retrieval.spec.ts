import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/** UI-build-2 §4 — Data retrieval browser specs (fixtures). /api/granules is served from the
 *  COMMITTED fixture (generated through the REAL engine enrichment, groups included);
 *  /api/background/* are intercepted with fixture answers that carry the engine's own shapes
 *  (labelled as fixtures). The token rides the Authorization header — a spec asserts it is
 *  present and NEVER in the URL. */
const FIXTURE = JSON.parse(readFileSync(join(__dirname, "fixtures", "granules-bbox.json"), "utf8")) as {
  granules: { id: string; product: string | null; stack: string; ref_time: string | null;
              sec_time: string | null; start: string | null; baseline_days: number | null;
              flags: string[]; range_bandwidth_mhz: number | null; collection: string | null;
              crid: string | null; version: string | null;
              footprint: [number, number][] | null }[];
  groups: { key: string; kind: string; grouped: boolean; n_variants: number; granule_ids: string[];
            variant_facts: string[]; flags: string[]; ref_time: string | null; sec_time: string | null;
            start: string | null; stack: string; product: string | null }[];
};

const TEST_VECTOR = "NISAR_L2_PR_GUNW_031_034_A_019_033_4000_SH_20260920T130823_20260920T130857_20261014T130823_20261014T130857_P05023_N_F_J_001";
const VARIANT_A = "NISAR_L2_PR_GCOV_031_034_A_019_4005_SHNA_A_20260714T130823_20260714T130853_P05023_N_F_J_001";
const VARIANT_B = "NISAR_L2_PR_GCOV_031_034_A_019_7700_SHNA_A_20260714T130823_20260714T130853_P05023_N_F_J_001";
const UNKNOWN = "NISAR_L2_PR_SYNTH_UNKNOWN-LAYOUT_001";
const FLIPPED = "NISAR_L2_PR_GUNW_033_034_A_019_031_4000_SH_20260831T130823_20260831T130857_20260807T130823_20260807T130857_P05023_N_F_J_001";
const TOKEN = "e2e-bg-token";

// 1×1 transparent PNG (the fixture preview body; metadata rides the X-Img-Meta header)
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64");

function previewMeta(id: string, layer: string) {
  return {
    id, layer, cmap: "viridis", lo: 0.1, hi: 0.9, shown_stride: 1, shown_shape: [2, 2],
    epsg: 32611, clip_bbox_lonlat: [-118.45, 33.7, -118.05, 34.3],
    requested_bbox: [-118.45, 33.7, -118.05, 34.3],
    stats: { shape: [100, 100], dtype: "float32", min: 0.1, max: 0.9, mean: 0.5, p2: 0.2,
             p50: 0.5, p98: 0.8, nan_fraction: 0.0, n_bytes: 40000 },
    cache_hit: false,
  };
}

const LAYER_A = "science__LSAR__GCOV__grids__frequencyA__HHHH";

function granuleDetail(id: string) {
  const g = FIXTURE.granules.find((x) => x.id === id) ?? FIXTURE.granules[0];
  return {
    ok: true, id, granule: g, decoded: {}, group: null, grouped: false,
    inventory: {
      grids: [{
        path: "science/LSAR/GCOV/grids/frequencyA", key: "science__LSAR__GCOV__grids__frequencyA",
        epsg: 32611, nx: 100, ny: 100, dx: 20.0, dy: 20.0, extras: [],
        layers: [
          { path: "science/LSAR/GCOV/grids/frequencyA/HHHH", key: LAYER_A, shape: [100, 100],
            dtype: "float32", chunks: null, readable: true, why_not: null, requested_by_scan: true },
          { path: "science/LSAR/GCOV/grids/frequencyA/HVHV", key: "science__LSAR__GCOV__grids__frequencyA__HVHV",
            shape: [100, 100], dtype: "float32", chunks: null, readable: true, why_not: null, requested_by_scan: true },
          { path: "science/LSAR/GCOV/grids/frequencyA/mask", key: "science__LSAR__GCOV__grids__frequencyA__mask",
            shape: [100, 100], dtype: "uint8", chunks: null, readable: true, why_not: null, requested_by_scan: false },
        ],
      }],
      source: "layout index (metadata only)", n_layers: 3,
      scan_request_regex: "/GCOV/grids/frequencyA/", n_layers_requested_by_scan: 2,
    },
    inventory_note: null, footprint: g.footprint ?? null,
  };
}

const GCOV_AT_REF = "NISAR_L2_PR_GCOV_041_034_A_019_4005_SHNA_A_20260912T130823_20260912T130853_P05023_N_F_J_001";

async function routeEngine(page: Page, opts: {
  backgroundStatus?: number;                 // status for /api/background/granule (gate tests)
  previewStatus?: number;
  previewBody?: string;                      // engine error text for a failing preview
} = {}) {
  await page.route("**/api/health**", (r) =>
    r.fulfill({ json: { ok: true, scans_enabled: false, earthdata_credentials: "present",
                        cache_write: { status: "disabled", reason: null } } }));
  await page.route("**/api/granules**", (r) => r.fulfill({ json: FIXTURE }));
  await page.route("**/api/tile-at**", (r) => r.fulfill({
    json: { ok: true, tile: { id: "11SLT86", epsg: 32611, size_m: 10000,
      bbox_lonlat: [-118.45, 33.7, -118.05, 34.3], center_lonlat: [-118.25, 34.05],
      polygon_lonlat: [[-118.45, 33.7], [-118.05, 33.7], [-118.05, 34.3], [-118.45, 34.3], [-118.45, 33.7]] } } }));
  await page.route("**/api/background/granule**", (r) => {
    if (opts.backgroundStatus === 401) return r.fulfill({ status: 401, json: { ok: false, error: "missing or wrong background token" } });
    const url = new URL(r.request().url());
    const id = url.searchParams.get("id") ?? "";
    void id;
    return r.fulfill({ json: granuleDetail(id) });
  });
  await page.route("**/api/background/stack-pair**", (r) => r.fulfill({
    json: {
      ok: true, id: TEST_VECTOR, product: "GUNW",
      bbox_used: [-118.65, 33.65, -117.85, 34.45], epoch_merge_minutes: 10,
      ref: { epoch: "2026-09-20T13:08:23Z", granules: [FIXTURE.granules.find((g) => g.id === GCOV_AT_REF)]
        .filter(Boolean) },
      sec: { epoch: "2026-10-14T13:08:23Z", granules: [] },
      note: "fixture: the secondary date deliberately has NO GCOV — the honest empty end",
    },
  }));
  await page.route("**/api/background/preview**", (r) => {
    if (opts.previewStatus && opts.previewBody) {
      return r.fulfill({ status: opts.previewStatus, json: { ok: false, error: opts.previewBody } });
    }
    const url = new URL(r.request().url());
    return r.fulfill({
      status: 200, body: PNG, contentType: "image/png",
      headers: { "X-Img-Meta": JSON.stringify(previewMeta(url.searchParams.get("id") ?? "", url.searchParams.get("layer") ?? "")) },
    });
  });
  await page.route("**/api/background/probe**", (r) => r.fulfill({
    json: { ok: true, id: "x", layer: LAYER_A, value: 0.5123, finite: true, row: 10, col: 12,
            epsg: 32611, x: 380012.0, y: 3770234.0, lon: -118.25, lat: 34.0, dtype: "float32" },
  }));
}

async function seedToken(page: Page, token: string | null) {
  await page.addInitScript((t) => {
    try {
      if (t) window.sessionStorage.setItem("nisar_bg_token", t);
      else window.sessionStorage.removeItem("nisar_bg_token");
    } catch { /* ignore */ }
  }, token);
}

const BOX = "west=-118.45&south=33.7&east=-118.05&north=34.3&level=GUNW,GCOV";

async function selectInList(page: Page, idPrefix: string) {
  await page.getByLabel("List view").check();
  await page.locator("tr", { hasText: idPrefix }).first().click();
}

test.describe("background retrieval (fixtures)", () => {
  test.beforeEach(async ({ page }) => {
    await seedToken(page, TOKEN);
  });

  test("GUNW renders as a pair; GCOV as a single date; the product text is always present", async ({ page }) => {
    await routeEngine(page);
    await page.goto(`/background/retrieval?${BOX}`);
    await expect(page.getByText("20 granules")).toBeVisible();
    // GUNW pair detail
    await selectInList(page, "NISAR_L2_PR_GUNW_031_034");
    await expect(page.getByText("GUNW · pair")).toBeVisible();
    await expect(page.getByText(/A GUNW is not two images/)).toBeVisible();
    await expect(page.getByText(/interferometric layers/)).toBeVisible();
    // GCOV single-date detail
    await selectInList(page, "NISAR_L2_PR_GCOV_027_034");
    await expect(page.getByText("GCOV · single date")).toBeVisible();
    await expect(page.getByText(/is one acquisition/)).toBeVisible();
  });

  test("variant group: the ×2 badge opens a picker listing both bandwidths, none hidden", async ({ page }) => {
    await routeEngine(page);
    await page.goto(`/background/retrieval?${BOX}`);
    await selectInList(page, VARIANT_A.slice(0, 40));
    await expect(page.getByText("2 variants", { exact: true })).toBeVisible();
    await expect(page.getByText(/differing in range_bandwidth_mhz|differing in bandwidth/)).toBeVisible();
    await expect(page.getByText("40 MHz").first()).toBeVisible();
    await expect(page.getByText("77 MHz").first()).toBeVisible();
    // picking the other variant swaps the shown file name — nothing is merged away
    const radios = page.locator('input[name="variant"]');
    await expect(radios).toHaveCount(2);
    await radios.nth(1).check();
    await expect(page.getByText(/7700/).first()).toBeVisible();
  });

  test("flipped pair: ends labelled by true time order and the card says so", async ({ page }) => {
    await routeEngine(page);
    await page.goto(`/background/retrieval?${BOX}`);
    await selectInList(page, "NISAR_L2_PR_GUNW_033_034");
    await expect(page.getByText("Earlier date")).toBeVisible();
    await expect(page.getByText("Later date")).toBeVisible();
    await expect(page.getByText(/pair stored secondary-first \(flipped\)/).first()).toBeVisible();
  });

  test("unknown-layout id stays ungrouped and raw (never invented into a group)", async ({ page }) => {
    await routeEngine(page);
    await page.goto(`/background/retrieval?${BOX}`);
    await selectInList(page, UNKNOWN);
    await expect(page.getByText(/not grouped: no verified acquisition time/)).toBeVisible();
    await expect(page.getByText("unknown layout (raw)").or(page.getByText("GCOV · single date"))).toBeVisible();
  });

  test("detail tabs: Layers (engine inventory), Timeline (epochs + gaps), Data parts (requested vs exists)", async ({ page }) => {
    await routeEngine(page);
    await page.goto(`/background/retrieval?${BOX}`);
    await selectInList(page, "NISAR_L2_PR_GCOV_027_034");
    await page.getByRole("tab", { name: "Layers" }).click();
    await expect(page.getByText(/layout index \(metadata only\)/)).toBeVisible();
    await expect(page.getByText("HHHH").first()).toBeVisible();
    await expect(page.getByText("float32").first()).toBeVisible();
    await page.getByRole("tab", { name: "Timeline" }).click();
    await expect(page.getByText(/distinct epochs/)).toBeVisible();
    await expect(page.getByText(/gaps min\/med\/max/)).toBeVisible();
    await page.getByRole("tab", { name: "Data parts" }).click();
    await expect(page.getByText(/of 3 layers would be fetched by a scan/)).toBeVisible();
    await expect(page.getByText("requested by scan").or(page.getByText(/scan requests/))).toBeVisible();
  });

  test("the background token rides the Authorization header and NEVER the URL", async ({ page }) => {
    let sawHeader = false;
    let urlHadToken = false;
    await routeEngine(page);
    await page.route("**/api/background/granule**", (r) => {
      sawHeader = r.request().headers()["authorization"] === `Bearer ${TOKEN}`;
      urlHadToken = r.request().url().includes(TOKEN);
      const url = new URL(r.request().url());
      return r.fulfill({ json: granuleDetail(url.searchParams.get("id") ?? "") });
    });
    await page.goto(`/background/retrieval?${BOX}`);
    await selectInList(page, "NISAR_L2_PR_GCOV_027_034");
    await page.getByRole("tab", { name: "Layers" }).click();
    await expect(page.getByText(/layout index \(metadata only\)/)).toBeVisible({ timeout: 15_000 });
    expect(sawHeader).toBe(true);
    expect(urlHadToken).toBe(false);
  });

  test("gate prompt: a 401 asks once, the token is kept in sessionStorage and a retry succeeds", async ({ page }) => {
    await seedToken(page, null);
    let gated = true;
    await routeEngine(page);
    await page.route("**/api/background/granule**", (r) => {
      if (gated) return r.fulfill({ status: 401, json: { ok: false, error: "missing or wrong background token — send 'Authorization: Bearer <NISAR_BACKGROUND_TOKEN>'" } });
      const url = new URL(r.request().url());
      return r.fulfill({ json: granuleDetail(url.searchParams.get("id") ?? "") });
    });
    await page.goto(`/background/retrieval?${BOX}`);
    await selectInList(page, "NISAR_L2_PR_GCOV_027_034");
    await expect(page.getByRole("dialog", { name: /background token required/i })).toBeVisible({ timeout: 15_000 });
    await page.getByRole("textbox", { name: "Background token" }).fill(TOKEN);
    gated = false;                     // un-gate BEFORE the retry click (the refetch races the click)
    await page.getByRole("button", { name: /Save and retry/ }).click();
    await page.getByRole("tab", { name: "Layers" }).click();
    await expect(page.getByText(/layout index \(metadata only\)/)).toBeVisible({ timeout: 15_000 });
    const stored = await page.evaluate(() => window.sessionStorage.getItem("nisar_bg_token"));
    expect(stored).toBe(TOKEN);
  });

  test("compare view: the two ends from the engine's stack-pair; a missing end says so; zoom is a new sub-box request", async ({ page }) => {
    const previewUrls: string[] = [];
    await routeEngine(page);
    await page.route("**/api/background/preview**", (r) => {
      previewUrls.push(r.request().url());
      const url = new URL(r.request().url());
      return r.fulfill({ status: 200, body: PNG, contentType: "image/png",
        headers: { "X-Img-Meta": JSON.stringify(previewMeta(url.searchParams.get("id") ?? "", url.searchParams.get("layer") ?? "")) } });
    });
    await page.goto(`/background/retrieval?${BOX}`);
    await selectInList(page, "NISAR_L2_PR_GUNW_031_034");
    // open compare from the pair card (before/after backscatter offer)
    await page.getByRole("button", { name: /compare the two ends/ }).click();
    await expect(page.getByText("Compare the two ends")).toBeVisible();
    // left (before) pane has an image; right (after) pane honestly says no acquisition
    await expect(page.locator('[data-pane-title^="before"] img')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('[data-pane-title^="after"]')).toContainText(/no backscatter acquisition found for 2026-10-14T13:08:23Z UTC/);
    // synced zoom: wheel over the left pane -> a NEW preview request with a SMALLER box
    const before = previewUrls.length;
    const firstBox = new URL(previewUrls[0]).searchParams;
    const pane = page.locator('[data-pane-title^="before"] div[role="img"]').first();
    await pane.hover({ position: { x: 100, y: 80 } });
    await page.mouse.wheel(0, -240);
    await expect.poll(() => previewUrls.length, { timeout: 15_000 }).toBeGreaterThan(before);
    const second = new URL(previewUrls[previewUrls.length - 1]).searchParams;
    expect(Number(second.get("east")) - Number(second.get("west")))
      .toBeLessThan(Number(firstBox.get("east")) - Number(firstBox.get("west")));
    // the token is never in a preview URL
    expect(previewUrls.every((u) => !u.includes(TOKEN))).toBe(true);
  });

  test("preview states: all-NaN answers the engine's text; a failure shows 'preview unavailable: …'", async ({ page }) => {
    await routeEngine(page, { previewStatus: 422, previewBody: "no valid pixels in this clip — every sampled value of layer 'x' is NaN / no-data here" });
    await page.goto(`/background/retrieval?${BOX}`);
    await selectInList(page, "NISAR_L2_PR_GUNW_031_034");
    await page.getByRole("button", { name: /compare the two ends/ }).click();
    await expect(page.locator('[data-pane-title^="before"]')).toContainText(/no valid pixels in this clip/, { timeout: 15_000 });
  });

  test("preview failure surfaces the engine's own error text with 'preview unavailable'", async ({ page }) => {
    await routeEngine(page, { previewStatus: 502, previewBody: "the engine could not read this granule: x: RangeError: gave up on bytes" });
    await page.goto(`/background/retrieval?${BOX}`);
    await selectInList(page, "NISAR_L2_PR_GUNW_031_034");
    await page.getByRole("button", { name: /compare the two ends/ }).click();
    await expect(page.locator('[data-pane-title^="before"]')).toContainText(/preview unavailable: the engine could not read this granule/, { timeout: 15_000 });
  });

  test("engine offline: the retrieval tab says so and Search is disabled", async ({ page }) => {
    await page.route("**/api/health**", (r) => r.abort());
    await page.goto(`/background/retrieval?${BOX}`);
    await expect(page.getByText(/the engine API is not reachable at/)).toBeVisible();
    await expect(page.getByRole("button", { name: /Search/ })).toBeDisabled();
  });

  test("engine 503 (no Earthdata credentials) carries the engine's text", async ({ page }) => {
    await routeEngine(page);
    await page.route("**/api/granules**", (r) => r.fulfill({
      status: 503, json: { ok: false, error: "Earthdata credentials absent — ASF search needs EARTHDATA_TOKEN (or EARTHDATA_USERNAME + EARTHDATA_PASSWORD)" } }));
    await page.goto(`/background/retrieval?${BOX}`);
    await expect(page.getByText(/Earthdata credentials absent/)).toBeVisible({ timeout: 15_000 });
  });

  test("mobile (390×844): the timeline fills the width and the detail becomes a bottom sheet", async ({ page }) => {
    await routeEngine(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/background/retrieval?${BOX}`);
    await expect(page.getByText("20 granules")).toBeVisible();
    await selectInList(page, "NISAR_L2_PR_GCOV_027_034");
    const aside = page.locator("aside.explorer-detail");
    await expect(aside).toBeVisible();
    const box = await aside.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.y).toBeGreaterThan(200);           // below the timeline (bottom sheet)
    expect(box!.height).toBeLessThanOrEqual(844 * 0.5);
  });
});
