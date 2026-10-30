import { expect, test } from "@playwright/test";

/** §2 Landing — public, plain language, live status strip that never invents numbers.
 *  This suite runs with the ENGINE OFFLINE, so the honest state is: "down" + no numbers. */

test("landing renders the promise, the Map CTA, and the four steps", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/NISAR radar data/i);
  const cta = page.getByRole("link", { name: /Open the Map/ });
  await expect(cta).toBeVisible();
  await expect(cta).toHaveAttribute("href", "/map");
  await expect(page.getByText("1 · Find radar scenes")).toBeVisible();
  await expect(page.getByText("2 · Prepare them")).toBeVisible();
  await expect(page.getByText("3 · Run each hazard check")).toBeVisible();
  await expect(page.getByText("4 · Rank the evidence")).toBeVisible();
});

test("landing explains how to read a result (tiers in words; rank score is not a probability)", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/It is .*not.* a probability/i)).toBeVisible();
  await expect(page.getByText(/labels.*SYNTHETIC|SYNTHETIC/).first()).toBeVisible();
});

test("mini glossary defines GUNW, GCOV, tile, stack and tier in one line each", async ({ page }) => {
  await page.goto("/");
  for (const term of ["GUNW", "GCOV", "tile", "stack", "tier"]) {
    await expect(page.getByText(term, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByText(/a pair of two dates compared/)).toBeVisible();
  await expect(page.getByText(/one date's backscatter picture/)).toBeVisible();
});

test("live status strip: engine down is SAID, and no numbers are shown (never invented)", async ({ page }) => {
  await page.goto("/");
  // the default suite has the engine deliberately absent — the strip must say so
  await expect(page.getByText(/the engine is down right now/)).toBeVisible({ timeout: 20_000 });
  // and it must not render the up-state numbers
  await expect(page.getByText("cached areas")).toHaveCount(0);
});

test("footer carries the data sources and the not-for-emergency line", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/not for emergency decisions/).first()).toBeVisible();
  await expect(page.getByText(/NISAR \(JPL\/ISRO\) via ASF DAAC/)).toBeVisible();
});

test("shared nav offers Landing · Map · Background from the landing page", async ({ page }) => {
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "App sections" });
  await expect(nav.getByRole("link", { name: "Landing" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Map" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Background" })).toBeVisible();
});
