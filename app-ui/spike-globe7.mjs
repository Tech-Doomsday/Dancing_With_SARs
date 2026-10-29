import { chromium } from "@playwright/test";
import fs from "node:fs";
const b = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await b.newPage({ viewport: { width: 900, height: 700 } });
page.on("pageerror", (e) => console.log("PAGEERROR:", String(e).slice(0, 200)));
await page.goto("http://127.0.0.1:3199/spike-official.html");
await page.waitForFunction(() => document.getElementById("out").textContent !== "booting", null, { timeout: 30000 });
await page.waitForTimeout(4000);
fs.mkdirSync("test-results/screens", { recursive: true });
await page.screenshot({ path: "test-results/screens/spike-lowz.png" });
await page.evaluate(() => window.__read && mapFly());
function mapFly() {}
await page.evaluate("window.__fly2 = () => {}");
// fly via keyboard-free: use maplob global — we exposed nothing; do a click-zoom instead:
for (let i = 0; i < 8; i++) { await page.mouse.click(450, 350); await page.waitForTimeout(400); }
await page.waitForTimeout(2000);
await page.screenshot({ path: "test-results/screens/spike-lowz-zoomed.png" });
console.log("saved both");
await b.close();
