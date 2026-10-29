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
    await page.goto("/");
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
    await page.goto("/");
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
    await page.goto("/?panel=granules&tile=11SLT66");
    await expect(page.getByText("A005", { exact: true })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("26 d gap")).toBeVisible();
    await page.getByRole("button", { name: /baseline 24 days/ }).click({ position: { x: 30, y: 9 } });
    await expect(page.getByText(/dates recovered in reversed order; corrected/)).toBeVisible();
    for (const [w, h] of SIZES) await snap(page, "data-tab-timeline", w, h);
    await page.close();
  }
});
