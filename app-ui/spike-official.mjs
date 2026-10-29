// controlled experiment: official-style image source in mercator, no resampling option,
// small map, only the image layer — plus readback via preserveDrawingBuffer
import { chromium } from "@playwright/test";
import fs from "node:fs";

const b = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await b.newPage({ viewport: { width: 900, height: 700 } });
await page.setContent(`<!doctype html><html><head>
<link href="/node_modules/maplibre-gl/dist/maplibre-gl.css" rel="stylesheet"/>
<script src="/node_modules/maplibre-gl/dist/maplibre-gl.js"></script>
<style>body{margin:0}#m{width:900px;height:700px}</style></head>
<body><div id="m"></div>
<div id="out" style="position:fixed;top:0;left:0;background:#fff;z-index:9">booting</div>
<script>
const map = new maplibregl.Map({
  container: "m",
  style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#334" } }] },
  center: [-118.25, 34.09], zoom: 11, preserveDrawingBuffer: true,
});
map.on("error", (e) => { document.getElementById("out").textContent = "ERR: " + (e.error && e.error.message); });
map.on("load", () => {
  map.addSource("img", {
    type: "image",
    url: "/test-gradient.png",
    coordinates: [[-118.3134, 34.1466], [-118.1866, 34.1466], [-118.1866, 34.0408], [-118.3134, 34.0408]],
  });
  map.addLayer({ id: "img", type: "raster", source: "img" });
  document.getElementById("out").textContent = "loaded";
});
window.__read = () => {
  const gl = map.getCanvas().getContext("webgl2") || map.getCanvas().getContext("webgl");
  const w = gl.drawingBufferWidth, h = gl.drawingBufferHeight;
  const px = new Uint8Array(4 * w * h);
  gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
  // sample center of the image box: project [-118.25, 34.09] to canvas px
  const p = map.project([-118.25, 34.09]);
  const x = Math.round(p.x), y = Math.round(h - p.y);
  const i = (y * w + x) * 4;
  return { w, h, at: [px[i], px[i+1], px[i+2], px[i+3]], x, y };
};
</script></body></html>`, { waitUntil: "domcontentloaded" });
await page.waitForFunction(() => document.getElementById("out").textContent === "loaded", null, { timeout: 30000 });
await page.waitForTimeout(2500);
console.log("readback:", JSON.stringify(await page.evaluate("window.__read()")));
fs.mkdirSync("test-results/screens", { recursive: true });
await page.screenshot({ path: "test-results/screens/spike-official.png" });
console.log("saved");
await b.close();
