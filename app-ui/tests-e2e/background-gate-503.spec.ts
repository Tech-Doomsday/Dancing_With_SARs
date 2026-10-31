import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/** Post-audit (Problem 2) — the Background views must tell the gate's answers apart from the
 *  engine's other 503. Engine routes are intercepted (fixtures, not a live engine):
 *    (a) 401                                   -> the token dialog (kind "unauthorized")
 *    (b) 503 naming NISAR_BACKGROUND_TOKEN     -> the token dialog (kind "unconfigured")
 *    (c) 503 "no Earthdata credentials"        -> the engine's own text, and NO token dialog
 *  Covered on a retrieval route (granule detail), a preprocessing route and a pipelines route. */

const FIXTURE = JSON.parse(readFileSync(join(__dirname, "fixtures", "granules-bbox.json"), "utf8")) as {
  granules: unknown[]; groups: unknown[];
};
const TOKEN = "e2e-bg-token";
const BOX = "west=-118.45&south=33.7&east=-118.05&north=34.3&level=GUNW,GCOV";

const MSG_401 = "missing or wrong background token — send 'Authorization: Bearer <NISAR_BACKGROUND_TOKEN>'";
const MSG_UNCONFIGURED =
  "background endpoints are gated and no gate is configured: set NISAR_BACKGROUND_TOKEN (a stopgap until real authentication exists)";
const MSG_NO_EARTHDATA =
  "no Earthdata credentials in the environment (set EARTHDATA_TOKEN, or EARTHDATA_USERNAME and EARTHDATA_PASSWORD)";

type Answer = { status: number; error: string };
const A401: Answer = { status: 401, error: MSG_401 };
const A503_GATE: Answer = { status: 503, error: MSG_UNCONFIGURED };
const A503_EARTHDATA: Answer = { status: 503, error: MSG_NO_EARTHDATA };

const gate = (page: Page) => page.getByRole("dialog", { name: /background token required/i });

async function seedToken(page: Page, token: string | null) {
  await page.addInitScript((t) => {
    try {
      if (t) window.sessionStorage.setItem("nisar_bg_token", t);
      else window.sessionStorage.removeItem("nisar_bg_token");
    } catch { /* ignore */ }
  }, token);
}

function answer(a: Answer) {
  return { status: a.status, json: { detail: { ok: false, error: a.error } } };
}

/** what the dialog / error state must show for each engine answer */
async function expectOutcome(page: Page, a: Answer) {
  if (a === A401) {
    await expect(gate(page)).toBeVisible({ timeout: 15_000 });
    await expect(gate(page).getByText(/the engine requires a token/)).toBeVisible();
  } else if (a === A503_GATE) {
    await expect(gate(page)).toBeVisible({ timeout: 15_000 });
    await expect(gate(page).getByText(/no gate is configured/)).toBeVisible();
  } else {
    // the engine's verbatim text is shown in the view's own error state...
    await expect(page.getByText(MSG_NO_EARTHDATA).first()).toBeVisible({ timeout: 15_000 });
    // ...and the token prompt is NOT shown (the user's token is fine)
    await expect(gate(page)).toHaveCount(0);
    await expect(page.getByText(/the engine requires a token/)).toHaveCount(0);
  }
}

const CASES: { label: string; answer: Answer }[] = [
  { label: "401 -> token dialog", answer: A401 },
  { label: "503 naming NISAR_BACKGROUND_TOKEN -> 'unconfigured' dialog", answer: A503_GATE },
  { label: "503 no Earthdata credentials -> engine text, no token dialog", answer: A503_EARTHDATA },
];

test.describe("retrieval route (granule detail)", () => {
  for (const c of CASES) {
    test(c.label, async ({ page }) => {
      // the 401 case has no stored token; the 503 cases hold a CORRECT token
      await seedToken(page, c.answer === A401 ? null : TOKEN);
      await page.route("**/api/health**", (r) => r.fulfill({ json: { ok: true, scans_enabled: false,
        earthdata_credentials: "present", cache_write: { status: "disabled", reason: null } } }));
      await page.route("**/api/granules**", (r) => r.fulfill({ json: FIXTURE }));
      await page.route("**/api/tile-at**", (r) => r.fulfill({
        json: { ok: true, tile: { id: "11SLT86", epsg: 32611, size_m: 10000,
          bbox_lonlat: [-118.45, 33.7, -118.05, 34.3], center_lonlat: [-118.25, 34.05],
          polygon_lonlat: [[-118.45, 33.7], [-118.05, 33.7], [-118.05, 34.3], [-118.45, 34.3], [-118.45, 33.7]] } } }));
      await page.route("**/api/background/granule**", (r) => r.fulfill(answer(c.answer)));
      await page.goto(`/background/retrieval?${BOX}`);
      await page.getByLabel("List view").check();
      await page.locator("tr", { hasText: "NISAR_L2_PR_GCOV_027_034" }).first().click();
      await page.getByRole("tab", { name: "Layers" }).click();
      await expectOutcome(page, c.answer);
    });
  }
});

async function routeRunsEngine(page: Page, onBackground: (path: "preprocessing" | "pipelines") => Answer) {
  await page.route("**/api/health**", (r) => r.fulfill({ json: { ok: true } }));
  await page.route("**/api/runs**", (r) => r.fulfill({
    json: { ok: true, runs: [{ id: "run-e2e-1", status: "ok", kind: "scan", tile: "11SLT66" }] } }));
  await page.route("**/api/bundles**", (r) => r.fulfill({ json: { ok: true, bundles: [] } }));
  await page.route("**/api/pipelines**", (r) => r.fulfill({
    json: { ok: true, implemented_count: 1, pipelines: [
      { name: "earthquake_coseismic", code: "3.01", sensor: "sar", applies: "coseismic earthquakes",
        uses_codes: ["M1"], fetch_codes: ["R1"], min_epochs: 2, status_values: ["SUPPORTED"],
        implemented: true, execution: "normal", needs_sar: true },
    ] } }));
  await page.route("**/api/background/runs/*/preprocessing**", (r) => r.fulfill(answer(onBackground("preprocessing"))));
  await page.route("**/api/background/runs/*/pipelines/*", (r) => r.fulfill(answer(onBackground("pipelines"))));
}

test.describe("preprocessing route", () => {
  for (const c of CASES) {
    test(c.label, async ({ page }) => {
      await seedToken(page, c.answer === A401 ? null : TOKEN);
      await routeRunsEngine(page, () => c.answer);
      await page.goto("/background/preprocessing");
      await page.getByLabel("pick a run").selectOption("run-e2e-1");
      await expectOutcome(page, c.answer);
    });
  }
});

test.describe("pipelines route", () => {
  for (const c of CASES) {
    test(c.label, async ({ page }) => {
      await seedToken(page, c.answer === A401 ? null : TOKEN);
      await routeRunsEngine(page, () => c.answer);
      await page.goto("/background/pipelines/earthquake_coseismic?run=run-e2e-1");
      await expectOutcome(page, c.answer);
    });
  }
});
