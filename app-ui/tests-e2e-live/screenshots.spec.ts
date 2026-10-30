import { test, expect, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";

/** §8.3 visual verification — LIVE-engine shots (synthetic). Output:
 *  test-results/screens/<name>-{1440x900,390x844}.png — initial globe (online), pin + footprint,
 *  scan progress, results with overlay + legend, Data tab with a populated multi-stack timeline
 *  (including the 26 d gap and the flipped pair). */

const SHOTS = "test-results/screens";
const ENGINE = "http://127.0.0.1:8907";
const SIZES: [number, number][] = [[1440, 900], [390, 844]];

async function snap(page: Page, name: string, w: number, h: number) {
  await page.setViewportSize({ width: w, height: h });
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${SHOTS}/${name}-${w}x${h}.png` });
}

async function goToCoords(page: Page) {
  await page.getByLabel("Latitude (-90 to 90)").fill("34.0");
  await page.getByLabel("Longitude (-180 to 180)").fill("-118.5");
  await page.getByRole("button", { name: "Go to coordinates" }).click();
}

async function waitForRun(id: string, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const r = await fetch(`${ENGINE}/api/runs/${id}`);
    if (r.ok) {
      const st = (await r.json()) as { status: string };
      if (st.status === "ok" || st.status === "error") return st.status;
    }
    await new Promise((res) => setTimeout(res, 500));
  }
  throw new Error(`run ${id} did not finish`);
}

test("initial globe, pin + footprint, scan progress, results, Data tab", async ({ browser }) => {
  test.setTimeout(240_000);
  mkdirSync(SHOTS, { recursive: true });

  // initial globe (engine online) — give the imagery a few seconds to load before judging
  {
    const page = await browser.newPage();
    await page.goto("/map");
    await expect(page.getByText(/engine online/)).toBeVisible({ timeout: 30_000 });
    await page.waitForTimeout(6_000);
    for (const [w, h] of SIZES) await snap(page, "initial-globe", w, h);
    // pin + footprint: wait out the 1600 ms flyTo so the tile polygon is actually framed
    await goToCoords(page);
    await expect(page.getByRole("heading", { name: "11SLT66" })).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(2_200);
    await expect(page.getByText(/EPSG:32611/)).toBeVisible();
    for (const [w, h] of SIZES) await snap(page, "pin-footprint", w, h);
    await page.close();
  }

  // scan progress: start the scan through the engine, re-attach via ?scan= (the leave-and-return flow)
  {
    const page = await browser.newPage();
    await page.goto("/map");
    await expect(page.getByText(/engine online/)).toBeVisible({ timeout: 30_000 });
    const res = await fetch(`${ENGINE}/api/scans`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ lon: -118.5, lat: 34.0 }),
    });
    const body = (await res.json()) as { ok: boolean; run_id?: string };
    if (body.ok && body.run_id) {
      await page.goto(`/?scan=${body.run_id}`);
      // the running stepper (verbatim stage text) — SOFT: a synthetic run may finish before
      // the first poll lands; the shot then just shows the completed pin panel instead
      await page.getByText(/P01 |saving bundle|routing \+ pipelines|persisting|done/).first()
        .waitFor({ timeout: 5_000 }).catch(() => { /* already done */ });
      await snap(page, "scan-progress", 1440, 900);
    }
    await waitForRun(body.run_id as string);
    // results with overlay + legend (toggle the raster chip if the auto-overlay did not fire)
    await page.goto(`/?panel=results&scan=${body.run_id}`);
    await expect(page.getByText("earthquake_coseismic").first()).toBeVisible({ timeout: 30_000 });
    const chip = page.getByRole("button", { name: "displacement", exact: true }).first();
    await expect(chip).toBeVisible({ timeout: 15_000 });
    const legendVisible = await page.getByText("raster shown on its geographic bounding box")
      .isVisible().catch(() => false);
    if (!legendVisible) await chip.click();
    await expect(page.getByText("raster shown on its geographic bounding box")).toBeVisible({ timeout: 15_000 });
    // the re-attach path re-centres on the scanned tile — let the fly finish so the overlay is framed
    await page.waitForTimeout(2_200);
    for (const [w, h] of SIZES) await snap(page, "results-overlay-legend", w, h);
    await page.close();
  }

  // Data tab with a populated multi-stack timeline (gap label + flipped pair)
  {
    const page = await browser.newPage();
    await page.goto("/map?panel=granules&tile=11SLT66");
    await expect(page.getByText("A005", { exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("26 d gap")).toBeVisible();
    await page.getByRole("button", { name: /baseline 24 days/ }).click({ position: { x: 30, y: 9 } });
    await expect(page.getByText(/dates recovered in reversed order; corrected/)).toBeVisible();
    for (const [w, h] of SIZES) await snap(page, "data-tab-timeline", w, h);
    await page.close();
  }
});

test("granule explorer: full range, zoomed gap brackets, selected pair with map, truncated, List, mobile drawer", async ({ browser }) => {
  test.setTimeout(300_000);
  mkdirSync(SHOTS, { recursive: true });

  // full range against the LIVE (synthetic) engine — the seed's FakeRetriever answers any box
  const page = await browser.newPage();
  await page.goto("/background/retrieval?west=-118.45&south=33.7&east=-118.05&north=34.3&level=GUNW,GCOV");
  await expect(page.getByText(/granules/).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('canvas[role="application"]')).toBeVisible();
  await page.waitForTimeout(2_000);
  for (const [w, h] of SIZES) await snap(page, "explorer-full-range", w, h);
  await page.setViewportSize({ width: 1440, height: 900 });   // back to desktop before interacting

  // zoomed to days with gap brackets: zoom in a few steps, screenshot
  // (click a header strip spot, NOT a bar end — an end click opens the gated Compare view)
  await page.locator('canvas[role="application"]').click({ position: { x: 120, y: 8 } });
  for (let i = 0; i < 5; i++) await page.keyboard.press("+");
  await page.waitForTimeout(800);
  await snap(page, "explorer-zoomed-days", 1440, 900);

  // selection fills the detail panel (with the footprint mini-map) — deterministic via List view
  await page.getByLabel("List view").check();
  const row = page.locator("tr", { hasText: "GUNW" }).filter({ hasNotText: "notimes" }).first();
  await row.click();
  await expect(page.getByText(/Show on globe/)).toBeVisible();
  await page.waitForTimeout(2_000); // mini-map tiles
  await snap(page, "explorer-selected-pair", 1440, 900);
  await snap(page, "explorer-selected-pair", 390, 844);

  // truncated banner: the real engine never truncates this set, so the override is a LABELLED FIXTURE
  await page.route("**/api/granules**", (r) => r.fulfill({
    json: { ok: true, bbox: [-118.45, 33.7, -118.05, 34.3], window: { t0: "2026-06-01T00:00:00Z", t1: "2026-10-30T00:00:00Z" },
            n_total: 250, truncated: true, degraded: false, search_budget: 250,
            generated_at: new Date().toISOString(), granules: [], stacks: [] },
  }));
  await page.goto("/background/retrieval?west=-118.45&south=33.7&east=-118.05&north=34.3");
  await expect(page.getByText("result budget hit — this list is incomplete")).toBeVisible({ timeout: 20_000 });
  await snap(page, "explorer-truncated", 1440, 900);

  // mobile filters drawer
  const mobile = await browser.newPage();
  await mobile.setViewportSize({ width: 390, height: 844 });
  await mobile.goto("/background/retrieval?west=-118.45&south=33.7&east=-118.05&north=34.3&level=GUNW,GCOV");
  await expect(mobile.getByText(/granules/).first()).toBeVisible({ timeout: 30_000 });
  await mobile.getByRole("button", { name: /filters/ }).click();
  await mobile.waitForTimeout(400);
  await mobile.screenshot({ path: `${SHOTS}/explorer-mobile-drawer-390x844.png` });
  await page.close();
  await mobile.close();
});
