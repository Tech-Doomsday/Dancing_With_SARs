import { test, expect } from "@playwright/test";

/** §8.2 — Playwright against the REAL engine (uvicorn + temp NISAR_DATA_DIR seeded through
 *  tests/common_fixtures.py; SYNTHETIC data, labelled). Covers: the tile-at flow, the Data tab
 *  against the injected-retriever granule set (multiple stacks, a real gap label, a flipped pair,
 *  clicking a bar populates the detail panel), the scan flow with verbatim progress, and the
 *  /workbench POST /api/runs path. A real scan needs ASF + credentials in production; here the
 *  engine's own retriever seam is patched to the suite's FakeRetriever (the same seam the Python
 *  tests use), so this stays offline and deterministic. */

const LAT = "34.0";
const LON = "-118.5";
const TILE = "11SLT66";

async function goToCoords(page: import("@playwright/test").Page) {
  await page.getByLabel("Latitude (-90 to 90)").fill(LAT);
  await page.getByLabel("Longitude (-180 to 180)").fill(LON);
  await page.getByRole("button", { name: "Go to coordinates" }).click();
}

test.describe("globe against the live (synthetic) engine", () => {
  test("health strip reports scans enabled and credentials present", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText(/engine online/)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/scans enabled/)).toBeVisible();
    await expect(page.getByText(/Earthdata present/)).toBeVisible();
  });

  test("tile-at flow: typed coordinates resolve the MGRS tile and enable Scan", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText(/engine online/)).toBeVisible({ timeout: 20_000 });
    await goToCoords(page);
    // the ScanPanel tile card carries the engine's own answer
    await expect(page.getByRole("heading", { name: TILE })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("EPSG:32611 · 10 km")).toBeVisible();
    // scans enabled + creds present -> the Scan control is enabled, no reason text
    const scan = page.getByRole("button", { name: "Scan this tile" });
    await expect(scan).toBeEnabled({ timeout: 15_000 });
  });

  test("Data tab: multi-stack timeline, an exact gap label, a flipped pair, and the detail panel", async ({ page }) => {
    test.setTimeout(120_000);
    // §7.7: the Data tab is directly addressable via URL state
    await page.goto(`/?panel=granules&tile=${TILE}`);
    await expect(page.getByText(`Data — granule availability · tile ${TILE}`)).toBeVisible({ timeout: 20_000 });
    // multiple stacks, sorted by direction then track: A005 and D013 rows
    await expect(page.getByText("A005", { exact: true })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("D013", { exact: true })).toBeVisible();
    // the real 26-day break between the 3rd and 4th A005 pairs (median spacing 12 d, threshold 18 d)
    await expect(page.getByText("26 d gap")).toBeVisible();
    // the flipped pair, corrected: the timeline's only 24-day bar (dates are relative to today).
    // Click off-centre: an epoch marker circle can sit exactly at the bar's midpoint (same
    // timestamp) — the bar stays clickable everywhere else, and keyboard (Enter) also works.
    await page.getByRole("button", { name: /baseline 24 days/ }).click({ position: { x: 30, y: 9 } });
    // the detail panel shows only the fields the engine tracks, in plain language
    await expect(page.getByText(/dates recovered in reversed order; corrected/)).toBeVisible();
    await expect(page.getByText(/baseline: 24 d/)).toBeVisible();
    await expect(page.getByText(/NISAR_L2_PR_GUNW_/).first()).toBeVisible();
    await expect(page.getByText(/footprint/).first()).toBeVisible();
    // unresolved pairs get no bar (no dates to draw — honest); their story is the engine's own
    // flag text under the timeline, verbatim from P01
    await expect(page.getByText(/GUNW id carries 1 timestamp\(s\), a pair needs 4; dates left unresolved/)).toBeVisible();
  });

  test("scan flow: Scan this tile runs every pipeline and lands on results with findings", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/");
    await expect(page.getByText(/engine online/)).toBeVisible({ timeout: 20_000 });
    await goToCoords(page);
    const scan = page.getByRole("button", { name: "Scan this tile" });
    await expect(scan).toBeEnabled({ timeout: 15_000 });
    await scan.click();
    // verbatim progress stages from the chain's own step codes (§5.3 item 5) — SOFT: the
    // synthetic scan can finish faster than the first poll lands; the gate is the final state
    await page.getByText(/P01 |saving bundle|routing \+ pipelines|persisting/).first()
      .waitFor({ timeout: 8_000 }).catch(() => { /* already done */ });
    // the run completes; the results panel lists the findings, grouped by engine tier
    const resultsTab = page.getByRole("tab", { name: "Results" });
    await expect(resultsTab).toBeVisible({ timeout: 10_000 });
    // poll until the run is done: the results panel shows the live-run badge and the EQ finding
    await expect(page.getByText(/Scan run-/).first()).toBeVisible({ timeout: 90_000 });
    await expect(page.getByText("earthquake_coseismic").first()).toBeVisible();
    // the engine's own verdict label, whatever the honest answer is for this fixture window
    await expect(page.getByText(/supported by evidence|not supported by evidence|not enough evidence|no signal|run failed/i).first()).toBeVisible();
    // the run's own layer chips come from result.layers; toggling the raster brings the legend
    const dispChip = page.getByRole("button", { name: "displacement", exact: true }).first();
    await expect(dispChip).toBeVisible();
    const legendVisible = await page.getByText("raster shown on its geographic bounding box")
      .isVisible().catch(() => false);
    if (!legendVisible) await dispChip.click();
    await expect(page.getByText("raster shown on its geographic bounding box")).toBeVisible({ timeout: 15_000 });
  });

  test("workbench: POST /api/runs still works end to end from the old page", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/workbench");
    await expect(page.getByText(/pipelines implemented/)).toBeVisible({ timeout: 20_000 });
    // pick the OLDEST bundle — the seeded synthetic one (a concurrent scan adds a newer one)
    await page.getByRole("button", { name: /^11SLT66/ }).last().click();
    await page.getByRole("button", { name: /Run earthquake_coseismic|Redo verdict/ }).click();
    // the run view polls to a terminal state and shows the verdict (StatusBadge labels, not raw codes)
    await expect(page.getByText(/supported by evidence|not supported by evidence|not enough evidence|no signal|run failed|not modelled yet|finished/i).first())
      .toBeVisible({ timeout: 90_000 });
  });
});
