import { test, expect, type Page } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

/** UI-build-2 §7 screenshots — 1440×900 and 390×844 for: Landing, Map, Data retrieval (GUNW and
 *  GCOV selected, variant group, Compare), Preprocessing, Pipelines list + detail, gate prompt,
 *  engine offline. Output: test-results/screens2/. Fixture-served engine answers are the same
 *  labelled fixtures the other specs use; the Map shot uses the WebGL-enabled headless flags
 *  from playwright.config (swiftshader). */

const SHOTS = "test-results/screens2";
const SIZES: [number, number][] = [[1440, 900], [390, 844]];

const FIXTURE = JSON.parse(readFileSync(join(__dirname, "fixtures", "granules-bbox.json"), "utf8"));
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64");

const TEST_VECTOR = "NISAR_L2_PR_GUNW_031_034_A_019_033_4000_SH_20260920T130823_20260920T130857_20261014T130823_20261014T130857_P05023_N_F_J_001";
const VARIANT_A = "NISAR_L2_PR_GCOV_031_034_A_019_4005_SHNA_A_20260714T130823_20260714T130853_P05023_N_F_J_001";
const GCOV_AT_REF = "NISAR_L2_PR_GCOV_041_034_A_019_4005_SHNA_A_20260912T130823_20260912T130853_P05023_N_F_J_001";
const LAYER_A = "science__LSAR__GCOV__grids__frequencyA__HHHH";

async function snap(page: Page, name: string, w: number, h: number) {
  console.log(`[shot] ${name} ${w}x${h}…`);
  await page.setViewportSize({ width: w, height: h });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SHOTS}/${name}-${w}x${h}.png`, timeout: 30_000 });
  console.log(`[shot] ${name} ${w}x${h} done`);
}

function granuleDetail(id: string) {
  const g = FIXTURE.granules.find((x: { id: string }) => x.id === id) ?? FIXTURE.granules[0];
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
        ],
      }],
      source: "layout index (metadata only)", n_layers: 2,
      scan_request_regex: "/GCOV/grids/frequencyA/", n_layers_requested_by_scan: 2,
    },
    inventory_note: null, footprint: g.footprint ?? null,
  };
}

async function routeEngine(page: Page, opts: { offline?: boolean } = {}) {
  if (opts.offline) {
    await page.route("**/api/**", (r) => r.abort());
    return;
  }
  await page.route("**/api/health**", (r) =>
    r.fulfill({ json: { ok: true, scans_enabled: false, earthdata_credentials: "present",
                        cache_write: { status: "disabled", reason: null } } }));
  await page.route("**/api/granules**", (r) => r.fulfill({ json: FIXTURE }));
  await page.route("**/api/tile-at**", (r) => r.fulfill({
    json: { ok: true, tile: { id: "11SLT86", epsg: 32611, size_m: 10000,
      bbox_lonlat: [-118.45, 33.7, -118.05, 34.3], center_lonlat: [-118.25, 34.05],
      polygon_lonlat: [[-118.45, 33.7], [-118.05, 33.7], [-118.05, 34.3], [-118.45, 34.3], [-118.45, 33.7]] } } }));
  await page.route("**/api/background/granule**", (r) => r.fulfill({
    json: granuleDetail(new URL(r.request().url()).searchParams.get("id") ?? "") }));
  await page.route("**/api/background/stack-pair**", (r) => r.fulfill({
    json: { ok: true, id: TEST_VECTOR, product: "GUNW",
            bbox_used: [-118.65, 33.65, -117.85, 34.45], epoch_merge_minutes: 10,
            ref: { epoch: "2026-09-20T13:08:23Z", granules: [FIXTURE.granules.find((g: { id: string }) => g.id === GCOV_AT_REF)].filter(Boolean) },
            sec: { epoch: "2026-10-14T13:08:23Z", granules: [] },
            note: "fixture: the secondary date deliberately has NO GCOV — the honest empty end" } }));
  await page.route("**/api/background/preview**", (r) => r.fulfill({
    status: 200, body: PNG, contentType: "image/png",
    headers: { "X-Img-Meta": JSON.stringify({
      id: "x", layer: LAYER_A, cmap: "viridis", lo: 0.1, hi: 0.9, shown_stride: 1, shown_shape: [2, 2],
      epsg: 32611, clip_bbox_lonlat: [-118.45, 33.7, -118.05, 34.3], requested_bbox: [-118.45, 33.7, -118.05, 34.3],
      stats: { shape: [100, 100], dtype: "float32", min: 0.1, max: 0.9, mean: 0.5, p2: 0.2, p50: 0.5,
               p98: 0.8, nan_fraction: 0.0, n_bytes: 40000 }, cache_hit: false }) } }));
  await page.route("**/api/background/probe**", (r) => r.fulfill({
    json: { ok: true, id: "x", layer: LAYER_A, value: 0.5123, finite: true, row: 10, col: 12,
            epsg: 32611, x: 380012.0, y: 3770234.0, lon: -118.25, lat: 34.0, dtype: "float32" } }));
  await page.route("**/api/runs**", (r) => r.fulfill({
    json: { ok: true, runs: [{ id: "run-shot-1", status: "ok", kind: "scan", tile: "11SLT86" }] } }));
  await page.route("**/api/bundles**", (r) => r.fulfill({
    json: { ok: true, bundles: [{ bundle_id: "11SLT86-shot-bundle", tile: "11SLT86", epoch: "2026-08-20T00:00:00Z", status: "ok" }] } }));
  await page.route("**/api/background/runs/*/preprocessing**", (r) => r.fulfill({
    json: { ok: true, run: { id: "run-shot-1", status: "ok", tile: "11SLT86", kind: "scan", bundle_id: "11SLT86-shot-bundle" },
            trace: {
              steps: [
                { code: "P01", name: "granule_resolution", status: "ok", seconds: 0.42, message: "",
                  new_flags: [], new_assumptions: ["engine assumption: 12-day repeat"], n_arrays_total: 3, reads: [], arrays: [] },
                { code: "P06", name: "phase_to_los", status: "ok", seconds: 0.8, message: "", new_flags: [],
                  new_assumptions: [], n_arrays_total: 21, reads: ["harm_pairs"],
                  arrays: [{ aid: "t2", path: "ifg/los_m", label: "los_m", group: "ifg", step: "P06",
                             first_step: "P06", shape: [125, 125], dtype: "float32", kind: "float",
                             stats: { min: -0.05, max: 0.05 }, stride: 1, epsg: 32611,
                             extent: [350000, 3999680, 360000, 4009680], note: null, categories: null,
                             thumb_shape: [2, 2] }] },
                { code: "P13", name: "provenance", status: "ok", seconds: 0.02, message: "", new_flags: [],
                  new_assumptions: [], n_arrays_total: 24, reads: [], arrays: [] },
              ],
              arrays: {}, n_arrays: 1,
              bundle_summary: { bundle_id: "11SLT86-shot-bundle", status: "ok", epoch: "2026-08-20T00:00:00Z",
                capabilities: ["M1", "M2", "geometry"], depth: { n_gunw_pairs: 3, n_gcov_epochs: 4 },
                provenance: { created_at: "2026-08-20T00:00:00Z" } },
              handoff: { rows: [
                { name: "earthquake_coseismic", code: "3.01", sensor: "sar", min_epochs: 2,
                  routing: { route: "run" }, registry_status: "ok", reasons: ["pair window present"], feed: "READY",
                  uses: [{ code: "M1", name: "GUNW", state: "present", why: "capability M1 held" }],
                  own_fetch: [{ code: "R1", name: "ComCat", state: "present", why: "adapter" }],
                  missing_uses: [], own_missing: [], implemented: true, verdict_state: "run" },
                { name: "tsunami_inundation", code: "4.1", sensor: "heavy", min_epochs: null, routing: null,
                  registry_status: "NOT_RUN", reasons: [], feed: "BLOCKED",
                  uses: [{ code: "M1", name: "GUNW", state: "absent", why: "not held" }],
                  own_fetch: [{ code: "R11", name: "tsunami db", state: "no_producer", why: "no downloader" }],
                  missing_uses: ["M1"], own_missing: ["R11"], implemented: false, verdict_state: "manifest-only" },
              ], tally: { READY: 1, BLOCKED: 1 }, n_implemented: 1 },
            }, trace_arrays: true } }));
  await page.route("**/api/background/runs/*/pipelines/*", (r) => r.fulfill({
    json: { ok: true, run: { id: "run-shot-1", status: "ok", tile: "11SLT86", kind: "scan", bundle_id: null },
      manifest: { name: "earthquake_coseismic", code: "3.01", sensor: "sar", applies: "coseismic earthquakes",
        uses_codes: ["M1"], fetch_codes: ["R1", "R2"], min_epochs: 2, status_values: ["SUPPORTED"],
        execution: "normal", method: ["status decision table + within-tier score"],
        own_prep: ["cycle-jump screen"], own_fetch: ["v1 implements R1+R2"],
        assumptions: ["LOS sign convention from the manifest"] },
      routing: { route: "run", reason: "pair window present" },
      feed: { name: "earthquake_coseismic", code: "3.01", sensor: "sar", min_epochs: 2, routing: { route: "run" },
        registry_status: "ok", reasons: ["pair window present"], feed: "READY",
        uses: [{ code: "M1", name: "GUNW", state: "present", why: "capability M1 held" }],
        own_fetch: [{ code: "R1", name: "ComCat", state: "present", why: "adapter" }],
        missing_uses: [], own_missing: [], implemented: true, verdict_state: "run" },
      bundle_summary: { bundle_id: "11SLT86-shot-bundle" }, bundle_error: null,
      result: { pipeline: "earthquake_coseismic", tile: "11SLT86", epoch: "2026-08-20T00:00:00Z",
        status: "NOT_SUPPORTED", summary: "screen correlation -0.36 below the +0.25 gate",
        raw_p: 0.42, confidence: null, runtime_s: 1.4, warnings: [],
        evidence: [{ id: "screen", kind: "insufficient", claim: "the synthetic scene's pattern correlation screen returned -0.36", level: "caveat" }],
        layers: [], data_used: [], references_used: [], min_epochs_met: true, bundle_id: "11SLT86-shot-bundle", detail: {} },
      result_missing: null,
      process: { runtime_s: 1.4, diagnostics: { screen: { correlation: -0.36 } }, detail_steps: null, warnings: [], versions: {} } } }));
  await page.route(/\/api\/background\/runs\/[^/]+\/arrays\//, (r) => {
    if (r.request().url().includes("/probe")) {
      return r.fulfill({ json: { ok: true, row: 4, col: 5, stride: 1, value: -0.021, finite: true, lon: -118.3, lat: 34.1, epsg: 32611 } });
    }
    return r.fulfill({ status: 200, body: PNG, contentType: "image/png",
      headers: { "X-Img-Meta": JSON.stringify({ cmap: "viridis", lo: -0.05, hi: 0.05 }) } });
  });
  await page.route("**/api/pipelines**", (r) => r.fulfill({
    json: { ok: true, implemented_count: 1, pipelines: [
      { name: "earthquake_coseismic", code: "3.01", sensor: "sar", applies: "coseismic earthquakes",
        uses_codes: ["M1"], fetch_codes: ["R1"], min_epochs: 2, status_values: ["SUPPORTED"],
        implemented: true, execution: "normal", needs_sar: true },
      { name: "tsunami_inundation", code: "4.1", sensor: "heavy", applies: "tsunami inundation",
        uses_codes: ["M1"], fetch_codes: ["R11"], min_epochs: null, status_values: [],
        implemented: false, execution: "heavy", needs_sar: true },
      { name: "flood_mapping", code: "2.1", sensor: "sar", applies: "floods",
        uses_codes: ["M2"], fetch_codes: ["R9"], min_epochs: 1, status_values: [],
        implemented: false, execution: "normal", needs_sar: true },
    ] } }));
}

test("UI-build-2 screenshot set", async ({ browser }) => {
  test.setTimeout(420_000);
  mkdirSync(SHOTS, { recursive: true });

  // 1 · Landing (public page; engine offline in this suite → the honest down state)
  const landing = await browser.newPage();
  await landing.goto("/");
  await expect(landing.getByRole("heading", { level: 1 })).toBeVisible();
  for (const [w, h] of SIZES) await snap(landing, "landing", w, h);

  // 2 · Map (the globe at /map)
  const map = await browser.newPage();
  await routeEngine(map);
  await map.goto("/map?lat=34.05&lon=-118.25&z=9");
  await map.waitForTimeout(2500);
  for (const [w, h] of SIZES) await snap(map, "map", w, h);

  // 3 · Data retrieval: GUNW selected
  const page = await browser.newPage();
  await page.addInitScript(() => window.sessionStorage.setItem("nisar_bg_token", "shot-token"));
  await routeEngine(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/background/retrieval?west=-118.45&south=33.7&east=-118.05&north=34.3&level=GUNW,GCOV");
  await expect(page.getByText(/20 granules/)).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("List view").check();
  await page.locator("tr", { hasText: "NISAR_L2_PR_GUNW_031_034" }).first().click();
  await expect(page.getByText("GUNW · pair")).toBeVisible();
  await page.waitForTimeout(600);
  await snap(page, "retrieval-gunw-selected", 1440, 900);

  // 4 · GCOV selected
  await page.locator("tr", { hasText: "NISAR_L2_PR_GCOV_027_034" }).first().click();
  await expect(page.getByText("GCOV · single date")).toBeVisible();
  await snap(page, "retrieval-gcov-selected", 1440, 900);
  await snap(page, "retrieval-gcov-selected", 390, 844);
  await page.setViewportSize({ width: 1440, height: 900 });

  // 5 · variant group + picker
  await page.locator("tr", { hasText: VARIANT_A.slice(0, 40) }).first().click();
  await expect(page.getByText("2 variants", { exact: true })).toBeVisible();
  await snap(page, "retrieval-variant-group", 1440, 900);
  await snap(page, "retrieval-variant-group", 390, 844);
  await page.setViewportSize({ width: 1440, height: 900 });

  // 6 · Compare view (before has an image; after honestly empty)
  await page.locator("tr", { hasText: "NISAR_L2_PR_GUNW_031_034" }).first().click();
  await page.getByRole("button", { name: /compare the two ends/ }).click();
  await expect(page.locator('[data-pane-title^="before"] img')).toBeVisible({ timeout: 20_000 });
  await snap(page, "retrieval-compare", 1440, 900);
  await snap(page, "retrieval-compare", 390, 844);
  await page.setViewportSize({ width: 1440, height: 900 });

  // 7 · Preprocessing (traced run selected)
  const pre = await browser.newPage();
  await pre.addInitScript(() => window.sessionStorage.setItem("nisar_bg_token", "shot-token"));
  await routeEngine(pre);
  await pre.setViewportSize({ width: 1440, height: 900 });
  await pre.goto("/background/preprocessing");
  await pre.getByLabel("pick a run").selectOption("run-shot-1");
  await expect(pre.getByText("P01").first()).toBeVisible({ timeout: 20_000 });
  await snap(pre, "preprocessing", 1440, 900);
  await snap(pre, "preprocessing", 390, 844);

  // 8 · Pipelines list + detail
  const pipes = await browser.newPage();
  await pipes.addInitScript(() => window.sessionStorage.setItem("nisar_bg_token", "shot-token"));
  await routeEngine(pipes);
  await pipes.setViewportSize({ width: 1440, height: 900 });
  await pipes.goto("/background/pipelines");
  await expect(pipes.getByRole("link", { name: /earthquake_coseismic implemented/ })).toBeVisible({ timeout: 20_000 });
  await snap(pipes, "pipelines-list", 1440, 900);
  await snap(pipes, "pipelines-list", 390, 844);
  await pipes.goto("/background/pipelines/earthquake_coseismic?run=run-shot-1");
  await expect(pipes.getByRole("heading", { name: "Output" })).toBeVisible({ timeout: 20_000 });
  await snap(pipes, "pipelines-detail", 1440, 900);
  await snap(pipes, "pipelines-detail", 390, 844);

  // 9 · gate prompt (no token stored)
  const gate = await browser.newPage();
  await routeEngine(gate);
  // this page's background calls answer 401 (no token stored) — the prompt is the shot
  await gate.route("**/api/background/runs/*/preprocessing**", (r) =>
    r.fulfill({ status: 401, json: { ok: false, error: "missing or wrong background token — send 'Authorization: Bearer <NISAR_BACKGROUND_TOKEN>'" } }));
  await gate.addInitScript(() => window.sessionStorage.removeItem("nisar_bg_token"));
  await gate.setViewportSize({ width: 1440, height: 900 });
  await gate.goto("/background/preprocessing");
  await gate.getByLabel("pick a run").selectOption("run-shot-1");
  await expect(gate.getByRole("dialog", { name: /background token required/i })).toBeVisible({ timeout: 20_000 });
  await snap(gate, "gate-prompt", 1440, 900);
  await snap(gate, "gate-prompt", 390, 844);

  // 10 · engine offline (retrieval tab)
  const off = await browser.newPage();
  await routeEngine(off, { offline: true });
  await off.setViewportSize({ width: 1440, height: 900 });
  await off.goto("/background/retrieval?west=-118.45&south=33.7&east=-118.05&north=34.3");
  await expect(off.getByText(/the engine API is not reachable at/)).toBeVisible({ timeout: 20_000 });
  await snap(off, "engine-offline", 1440, 900);
  await snap(off, "engine-offline", 390, 844);
});
