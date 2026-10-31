import { expect, test } from "@playwright/test";

/** §1 compatibility — every legacy URL keeps working with its query string. */

test("legacy /?lat=&lon=&z= redirects to /map with the same query string", async ({ page }) => {
  await page.goto("/?lat=34.05&lon=-118.25&z=9");
  await expect(page).toHaveURL(/\/map\?lat=34\.05&lon=-118\.25&z=9/);
});

test("legacy /?tile=&panel= redirects to /map with the same query string", async ({ page }) => {
  await page.goto("/?tile=11SLT66&panel=pin");
  await expect(page).toHaveURL(/\/map\?tile=11SLT66&panel=pin/);
});

test("legacy /granules?west=… redirects to /background/retrieval with the same query", async ({ page }) => {
  await page.goto("/granules?west=-118.45&south=33.7&east=-118.05&north=34.3&level=GUNW,GCOV&track=34&dir=A&sel=x");
  await expect(page).toHaveURL(/\/background\/retrieval\?west=-118\.45&south=33\.7&east=-118\.05&north=34\.3&level=GUNW%2CGCOV&track=34&dir=A&sel=x/);
});

test("legacy /workbench redirects to /background/pipelines", async ({ page }) => {
  await page.goto("/workbench");
  await expect(page).toHaveURL(/\/background\/pipelines/);
});

test("/background redirects to /background/retrieval", async ({ page }) => {
  await page.goto("/background");
  await expect(page).toHaveURL(/\/background\/retrieval/);
});

test("the plain Landing stays at / (no legacy query, no redirect)", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});
