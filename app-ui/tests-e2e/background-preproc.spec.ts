import { expect, test, type Page } from "@playwright/test";

/** UI-build-2 §5 + §6 — Preprocessing and Pipelines browser specs (fixtures; the engine shapes).
 *  Covers: the §5 step-by-step view with arrays-as-images + probe + enlarge + bundle summary +
 *  hand-off table, the honest empty state for runs without a trace, and the §6 pipeline list
 *  (implemented / not-modelled labels) + detail (Input / Process / Output). */

const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64");

const TRACE = {
  steps: [
    { code: "P01", name: "granule_resolution", status: "ok", seconds: 0.42, message: "",
      new_flags: [{ code: "search_degraded", severity: "warning", message: "fixture flag" }],
      new_assumptions: ["fixture assumption"], n_arrays_total: 3, reads: [],
      arrays: [{ aid: "t1", path: "resolved/stacks", label: "resolved stacks", group: "resolved",
                 step: "P01", first_step: "P01", shape: [5, 1], dtype: "object", kind: "float",
                 stats: { valid: 1 }, stride: 1, epsg: null, extent: null, note: null,
                 categories: null, thumb_shape: [2, 2] }] },
    { code: "P02", name: "clip", status: "ok", seconds: 1.2, message: "", new_flags: [],
      new_assumptions: [], n_arrays_total: 9, reads: ["resolved"], arrays: [] },
    { code: "P06", name: "phase_to_los", status: "ok", seconds: 0.8, message: "", new_flags: [],
      new_assumptions: ["engine's own assumption"], n_arrays_total: 21, reads: ["harm_pairs"],
      arrays: [{ aid: "t2", path: "ifg/los_m", label: "los_m", group: "ifg", step: "P06",
                 first_step: "P06", shape: [125, 125], dtype: "float32", kind: "float",
                 stats: { min: -0.05, max: 0.05 }, stride: 1, epsg: 32611,
                 extent: [350000, 3999680, 360000, 4009680], note: null, categories: null,
                 thumb_shape: [2, 2] }] },
  ],
  arrays: {},
  n_arrays: 2,
  bundle_summary: { bundle_id: "11SLT66-abc123", status: "ok", epoch: "2026-08-20T00:00:00Z",
    capabilities: ["M1", "M2", "geometry"], depth: { n_gunw_pairs: 3, n_gcov_epochs: 4 },
    provenance: { created_at: "2026-08-20T00:00:00Z" } },
  handoff: {
    rows: [
      { name: "earthquake_coseismic", code: "3.01", sensor: "sar", min_epochs: 2,
        routing: { route: "run" }, registry_status: "WOULD_LAUNCH", reasons: ["pair window present"],
        feed: "READY", uses: [{ code: "M1", name: "GUNW unwrapped stack", state: "present", why: "capability M1 held" }],
        own_fetch: [{ code: "R1", name: "ComCat", state: "present", why: "adapter" }],
        missing_uses: [], own_missing: [], implemented: true, verdict_state: "run" },
      { name: "tsunami_inundation", code: "4.1", sensor: "heavy", min_epochs: null,
        routing: null, registry_status: "NOT_RUN", reasons: [], feed: "BLOCKED",
        uses: [{ code: "M1", name: "GUNW unwrapped stack", state: "absent", why: "not held" }],
        own_fetch: [{ code: "R11", name: "tsunami db", state: "no_producer", why: "no downloader" }],
        missing_uses: ["M1"], own_missing: ["R11"], implemented: false, verdict_state: "manifest-only" },
    ],
    tally: { READY: 1, BLOCKED: 1 }, n_implemented: 1,
  },
};

const RUN_DETAIL = {
  ok: true,
  run: { id: "run-e2e-1", status: "ok", tile: "11SLT66", kind: "scan", bundle_id: "11SLT66-abc123" },
  manifest: {
    name: "earthquake_coseismic", code: "3.01", sensor: "sar", applies: "coseismic earthquakes",
    uses_codes: ["M1"], fetch_codes: ["R1", "R2"], min_epochs: 2,
    status_values: ["SUPPORTED", "NOT_SUPPORTED"], execution: "normal",
    method: ["status decision table + within-tier score"], own_prep: ["cycle-jump screen"],
    own_fetch: ["v1 implements R1+R2"], assumptions: ["LOS sign convention from the manifest"],
  },
  routing: { route: "run", reason: "pair window present" },
  feed: {
    name: "earthquake_coseismic", code: "3.01", sensor: "sar", min_epochs: 2,
    routing: { route: "run" }, registry_status: "ok",
    reasons: ["pair window present"], feed: "READY",
    uses: [{ code: "M1", name: "GUNW", state: "present", why: "capability M1 held" }],
    own_fetch: [{ code: "R1", name: "ComCat", state: "present", why: "adapter" }],
    missing_uses: [], own_missing: [], implemented: true, verdict_state: "run",
  },
  bundle_summary: { bundle_id: "11SLT66-abc123" },
  bundle_error: null,
  result: {
    pipeline: "earthquake_coseismic", tile: "11SLT66", epoch: "2026-08-20T00:00:00Z",
    status: "NOT_SUPPORTED", summary: "screen correlation -0.36 below the +0.25 gate",
    raw_p: 0.42, confidence: null, runtime_s: 1.4, warnings: [],
    evidence: [{ id: "screen", kind: "insufficient", claim: "the synthetic scene's pattern correlation screen returned -0.36", level: "caveat" }],
    layers: [], data_used: [], references_used: [], min_epochs_met: true, bundle_id: "11SLT66-abc123",
    detail: {},
    diagnostics: null, versions: {},
  },
  result_missing: null,
  process: {
    runtime_s: 1.4,
    diagnostics: { screen: { correlation: -0.36 } },
    detail_steps: null,
    warnings: [],
    versions: {},
  },
};

async function routeEngine(page: Page) {
  await page.route("**/api/health**", (r) => r.fulfill({ json: { ok: true } }));
  await page.route("**/api/runs**", (r) => r.fulfill({
    json: { ok: true, runs: [{ id: "run-e2e-1", status: "ok", kind: "scan", tile: "11SLT66" }] } }));
  await page.route("**/api/bundles**", (r) => r.fulfill({
    json: { ok: true, bundles: [{ bundle_id: "11SLT66-abc123", tile: "11SLT66", epoch: "2026-08-20T00:00:00Z", status: "ok" }] } }));
  await page.route("**/api/pipelines**", (r) => r.fulfill({
    json: { ok: true, implemented_count: 1, pipelines: [
      { name: "earthquake_coseismic", code: "3.01", sensor: "sar", applies: "coseismic earthquakes",
        uses_codes: ["M1"], fetch_codes: ["R1"], min_epochs: 2, status_values: ["SUPPORTED"],
        implemented: true, execution: "normal", needs_sar: true },
      { name: "tsunami_inundation", code: "4.1", sensor: "heavy", applies: "tsunami inundation",
        uses_codes: ["M1"], fetch_codes: ["R11"], min_epochs: null, status_values: [],
        implemented: false, execution: "heavy", needs_sar: true },
    ] } }));
  await page.route("**/api/background/runs/*/preprocessing**", (r) => r.fulfill({
    json: { ok: true, run: { id: "run-e2e-1", status: "ok", tile: "11SLT66", kind: "scan", bundle_id: "11SLT66-abc123" },
            trace: TRACE, trace_arrays: true } }));
  await page.route("**/api/background/runs/*/arrays/**probe**", (r) => r.fulfill({
    json: { ok: true, row: 4, col: 5, stride: 1, value: -0.021, finite: true, lon: -118.3, lat: 34.1, epsg: 32611 } }));
  await page.route(/\/api\/background\/runs\/[^/]+\/arrays\/[^/]+(\?.*)?$/, (r) => r.fulfill({
    status: 200, body: PNG, contentType: "image/png",
    headers: { "X-Img-Meta": JSON.stringify({ cmap: "viridis", lo: -0.05, hi: 0.05 }) } }));
  await page.route("**/api/background/runs/*/pipelines/*", (r) => r.fulfill({ json: RUN_DETAIL }));
  await page.route("**/api/background/granule**", (r) => r.fulfill({ json: { ok: true } }));
}

test.describe("preprocessing (§5)", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => window.sessionStorage.setItem("nisar_bg_token", "e2e-bg-token"));
    await routeEngine(page);
  });

  test("step-by-step view: names, reads/writes, flags, assumptions, timing, arrays with probe + enlarge", async ({ page }) => {
    await page.goto("/background/preprocessing");
    await page.getByLabel("pick a run").selectOption("run-e2e-1");
    await expect(page.getByText("P01").first()).toBeVisible();
    await expect(page.getByText("granule_resolution")).toBeVisible();
    // the step opens by default; reads + flags + assumptions are the engine's own words
    await expect(page.getByText("read:").first()).toBeVisible();
    await expect(page.getByText("fixture flag")).toBeVisible();
    await expect(page.getByText("fixture assumption")).toBeVisible();
    // open the P06 step (the second recorded array lives there), then the card renders
    await page.getByRole("button", { name: /P06 phase_to_los/ }).click();
    // array card renders from the engine PNG, probes on hover, enlarges on click
    const card = page.getByRole("button", { name: /enlarge los_m/ });
    await expect(card.locator("img")).toBeVisible({ timeout: 15_000 });
    await card.hover();
    await expect(page.getByText(/NaN \(no data here\)|-0\.021/).first()).toBeVisible();
    await card.click();
    await expect(page.getByRole("dialog", { name: /enlarged array/ })).toBeVisible();
    await expect(page.getByText(/ifg\/los_m/)).toBeVisible();
  });

  test("SceneBundle summary and the hand-off table with present/absent chips", async ({ page }) => {
    await page.goto("/background/preprocessing");
    await page.getByLabel("pick a run").selectOption("run-e2e-1");
    const summary = page.locator("section", { hasText: "SceneBundle summary" }).first();
    await expect(summary).toBeVisible();
    await expect(summary.getByText("11SLT66-abc123").first()).toBeVisible();
    await expect(page.getByText("Hand-off — what Registry.plan would do")).toBeVisible();
    await expect(page.getByText("M1 present")).toBeVisible();
    await expect(page.getByText("R11 no_producer")).toBeVisible();
    await expect(page.getByText("tsunami_inundation").first()).toBeVisible();
    await expect(page.getByText(/\(not modelled yet\)/)).toBeVisible();
  });

  test("cached bundles list in the selector and load the bundle records variant", async ({ page }) => {
    let bundleCalled = false;
    await page.route("**/api/background/runs/*/preprocessing**", (r) => {
      if (r.request().url().includes("bundle_id=")) {
        bundleCalled = true;
        return r.fulfill({
          json: { ok: true, run: { id: null, bundle_id: "11SLT66-abc123", tile: "11SLT66", kind: "bundle", status: "cached" },
                  trace: { ...TRACE, source: "bundle records (built without a live trace — array thumbnails are not available)" },
                  trace_arrays: false,
                  note: "records read from the cached bundle itself" } });
      }
      return r.fulfill({ json: { ok: true, run: { id: "run-e2e-1" }, trace: TRACE, trace_arrays: true } });
    });
    await page.goto("/background/preprocessing");
    await page.getByLabel("pick a cached bundle").selectOption("11SLT66-abc123");
    await expect(page.getByText(/bundle records \(built without a live trace/)).toBeVisible({ timeout: 15_000 });
    expect(bundleCalled).toBe(true);
  });

  test("a run without a recorded trace shows the honest empty state", async ({ page }) => {
    await page.route("**/api/runs**", (r) => r.fulfill({
      json: { ok: true, runs: [{ id: "run-old", status: "ok", kind: "scan", tile: "11SLT66" }] } }));
    await page.route("**/api/background/runs/*/preprocessing**", (r) => r.fulfill({
      json: { ok: true, run: { id: "run-old", status: "ok", tile: "11SLT66", kind: "scan", bundle_id: null },
              trace: null,
              empty_reason: "no recorded preprocessing trace for this run — runs started before tracing (or restored from the store after a restart) carry none; run a fresh scan to see one" } }));
    await page.goto("/background/preprocessing");
    await page.getByLabel("pick a run").selectOption("run-old");
    await expect(page.getByText(/no recorded preprocessing trace for this run/)).toBeVisible({ timeout: 15_000 });
  });

  test("gate prompt appears when the engine answers 401", async ({ page }) => {
    await page.addInitScript(() => window.sessionStorage.removeItem("nisar_bg_token"));
    await page.route("**/api/background/runs/*/preprocessing**", (r) =>
      r.fulfill({ status: 401, json: { ok: false, error: "missing or wrong background token" } }));
    await page.goto("/background/preprocessing");
    await page.getByLabel("pick a run").selectOption("run-e2e-1");
    await expect(page.getByRole("dialog", { name: /background token required/i })).toBeVisible({ timeout: 15_000 });
  });
});

test.describe("pipelines (§6)", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => window.sessionStorage.setItem("nisar_bg_token", "e2e-bg-token"));
    await routeEngine(page);
  });

  test("the list shows every pipeline; unimplemented ones are labelled not modelled yet (never faked)", async ({ page }) => {
    await page.goto("/background/pipelines");
    await expect(page.getByRole("link", { name: /earthquake_coseismic implemented/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /tsunami_inundation not modelled yet/ })).toBeVisible();
    await expect(page.getByText("not modelled yet").first()).toBeVisible();
    await expect(page.getByText("implemented").first()).toBeVisible();
  });

  test("the detail view renders Input / Process / Output with the engine's own words", async ({ page }) => {
    await page.goto("/background/pipelines/earthquake_coseismic?run=run-e2e-1");
    await expect(page.getByRole("heading", { name: "Input" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Process" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Output" })).toBeVisible();
    await expect(page.getByText(/routing/).first()).toBeVisible();
    await expect(page.getByText("M1 present")).toBeVisible();
    await expect(page.getByRole("heading", { name: /screen correlation -0\.36 below the \+0\.25 gate/ })).toBeVisible();
    // tier + rank score note (the engine's own words, reused from the globe results panel)
    await expect(page.getByText(/within-tier rank score/i).first()).toBeVisible();
    await expect(page.getByText(/not modelled yet/)).toHaveCount(0);
  });

  test("a pipeline without a result in the chosen run shows the engine's 404 text", async ({ page }) => {
    await page.route("**/api/background/runs/*/pipelines/*", (r) => r.fulfill({
      status: 404, json: { ok: false, error: "pipeline 'tsunami_inundation' has no result in run run-e2e-1 — it was not launched in this run (or the run is still going)" } }));
    await page.goto("/background/pipelines/tsunami_inundation?run=run-e2e-1");
    await expect(page.getByText(/has no result in run/)).toBeVisible({ timeout: 15_000 });
  });

  test("mobile (390×844): list and detail stay usable", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/background/pipelines");
    await expect(page.getByRole("link", { name: /earthquake_coseismic implemented/ })).toBeVisible();
    await page.goto("/background/pipelines/earthquake_coseismic?run=run-e2e-1");
    await expect(page.getByRole("heading", { name: "Output" })).toBeVisible({ timeout: 15_000 });
  });
});
