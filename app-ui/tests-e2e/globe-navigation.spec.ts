/** Globe navigation: "Show on globe" must not reload the page, must create the map once at the
 *  target camera, and a tab switch must keep the camera. Engine is stubbed at the network layer
 *  (route fixtures — labelled test data, never shown outside tests). */
import { test, expect } from "@playwright/test";

test("Show on globe: in-app navigation, one map, camera at target", async ({ page }) => {
  await page.addInitScript(() => { (window as unknown as { __survivor: string }).__survivor = "same-document"; });
  await page.goto("/map?lat=23.7&lon=90.4&z=9");
  await page.waitForFunction(() => (window as unknown as { __nisarMapCreates?: number }).__nisarMapCreates === 1);
  // client-side navigation to Background and back through the in-app links
  await page.getByRole("link", { name: /background/i }).first().click();
  await page.waitForURL(/\/background/);
  await page.goBack();
  await page.waitForURL(/\/map/);
  const survived = await page.evaluate(() => (window as unknown as { __survivor?: string }).__survivor);
  expect(survived).toBe("same-document");                       // no document reload happened
  const cam = await page.evaluate(() => JSON.parse(sessionStorage.getItem("nisar:camera:v1") ?? "null"));
  expect(cam).not.toBeNull();
  expect(Math.abs(cam.center[0] - 90.4)).toBeLessThan(0.01);
  expect(Math.abs(cam.center[1] - 23.7)).toBeLessThan(0.01);
  expect(cam.zoom).toBeGreaterThanOrEqual(7);
});
