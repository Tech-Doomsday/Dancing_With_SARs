/**
 * Mercator-exact raster warping for UTM-projected science grids onto a
 * Leaflet (Web Mercator) map.
 *
 * The science grids arrive in a UTM projection (EPSG 326xx/327xx). Simply
 * treating the grid as a flat image inside a lat/lon bounding box is NOT
 * accurate. Instead we render an offscreen canvas whose pixel grid is
 * defined IN Web Mercator: for each canvas pixel we invert Mercator to get
 * lat/lon, convert to UTM (Snyder 1987 series, imported from the science
 * core), and sample the science grid with nearest-neighbour lookup. The
 * resulting image is then placed as an L.imageOverlay rectangle — exact by
 * construction.
 */

import { latLonToUtm, utmToLatLon } from '@/lib/science/utm';
import { displacementColor, coherenceColor, sigmaColor, phaseColor, niceScaleMax } from './colormap';
import type { DecodedGrids } from './decode';

export interface GridGeo {
  latMin: number;
  latMax: number;
  lonMin: number;
  lonMax: number;
}

export type LayerKind = 'velocity' | 'displacement' | 'detected' | 'phase' | 'coherence' | 'sigma' | 'coverage';

export interface RenderedOverlay {
  dataUrl: string;
  geo: GridGeo;
  scaleMaxM?: number;
}

function merc(latDeg: number): number {
  const clamped = Math.max(-85.05, Math.min(85.05, latDeg));
  return Math.log(Math.tan(Math.PI / 4 + (clamped * Math.PI) / 360));
}

function invMerc(y: number): number {
  return ((Math.atan(Math.exp(y)) - Math.PI / 4) * 360) / Math.PI;
}

/** Compute the geographic bounds of a UTM grid (with a safe margin). */
export function gridBounds(
  meta: { xStart: number; xSpacing: number; yStart: number; ySpacing: number; width: number; height: number; epsg: number },
): GridGeo {
  // Inverse UTM (Snyder series, shared science core) at the four grid corners.
  const zone = meta.epsg > 32600 && meta.epsg <= 32660 ? meta.epsg - 32600 : meta.epsg - 32700;
  const southern = meta.epsg > 32700;
  const corners: Array<[number, number]> = [
    [meta.xStart, meta.yStart],
    [meta.xStart + (meta.width - 1) * meta.xSpacing, meta.yStart],
    [meta.xStart, meta.yStart + (meta.height - 1) * meta.ySpacing],
    [meta.xStart + (meta.width - 1) * meta.xSpacing, meta.yStart + (meta.height - 1) * meta.ySpacing],
  ];
  const lls = corners.map(([e, n]) => utmToLatLon(e, n, zone, southern));
  const lats = lls.map((x) => x.lat);
  const lons = lls.map((x) => x.lon);
  return {
    latMin: Math.min(...lats),
    latMax: Math.max(...lats),
    lonMin: Math.min(...lons),
    lonMax: Math.max(...lons),
  };
}

/**
 * Render the grid into a Mercator-true canvas.
 * @param grids decoded transport grids
 * @param meta grid georeferencing (from TransportGrid.meta)
 * @param layer which layer to colour
 * @param vmaxM explicit symmetric max (metres) for displacement; auto if omitted
 * @param maxCanvasPx canvas size bound
 * @param wavelengthM radar wavelength (metres) — required only by the 'phase'
 *   layer, to convert displacement back into wrapped phase fringes
 */
export function renderOverlay(
  grids: DecodedGrids,
  meta: { xStart: number; xSpacing: number; yStart: number; ySpacing: number; width: number; height: number; epsg: number },
  layer: LayerKind,
  vmaxM?: number,
  maxCanvasPx = 1024,
  wavelengthM?: number,
): RenderedOverlay | undefined {
  const geo = gridBounds(meta);

  // Canvas size proportional to the Mercator aspect. NOTE: merc() returns
  // radians-equivalent natural-log units, so the longitude span must be
  // converted to radians before taking the ratio — mixing units squashed
  // every canvas to a 57x-too-flat strip (760x64), which dense scenes
  // survived visually but sparse valid masks (2% of a decorrelated scene)
  // did not.
  const mercSpan = merc(geo.latMax) - merc(geo.latMin);
  const lonSpanDeg = geo.lonMax - geo.lonMin;
  const lonSpanRad = (lonSpanDeg * Math.PI) / 180;
  if (!(mercSpan > 0) || !(lonSpanRad > 0)) return undefined;
  let W = maxCanvasPx;
  let H = Math.round((mercSpan / lonSpanRad) * W);
  if (H > maxCanvasPx) {
    H = maxCanvasPx;
    W = Math.round((lonSpanRad / mercSpan) * H);
  }
  W = Math.max(64, W);
  H = Math.max(64, H);

  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  if (!ctx) return undefined;
  const img = ctx.createImageData(W, H);

  // Scale factors.
  let scaleMax: number | undefined;
  if (layer === 'displacement' || layer === 'velocity') {
    let absMax = 0;
    for (let i = 0; i < grids.displacement.length; i++) {
      if (grids.mask[i]) {
        const v = Math.abs(grids.displacement[i]);
        if (Number.isFinite(v) && v > absMax) absMax = v;
      }
    }
    scaleMax = vmaxM ?? niceScaleMax(absMax);
  }
  let sigmaMax = 0;
  if (layer === 'sigma') {
    for (let i = 0; i < grids.sigma.length; i++) {
      if (grids.mask[i]) {
        const v = grids.sigma[i];
        if (Number.isFinite(v) && v > sigmaMax) sigmaMax = v;
      }
    }
    if (sigmaMax <= 0) sigmaMax = 0.05;
  }

  const mercMin = merc(geo.latMin);
  const zoneOf = (epsg: number) => (epsg > 32600 && epsg <= 32660 ? epsg - 32600 : epsg - 32700);
  const gridZone = zoneOf(meta.epsg);

  const w = grids.width;
  const h = grids.height;

  for (let py = 0; py < H; py++) {
    const lat = invMerc(mercMin + (1 - py / H) * mercSpan);
    for (let px = 0; px < W; px++) {
      const lon = geo.lonMin + (px / W) * lonSpanDeg;
      const utm = latLonToUtm(lat, lon);
      // Skip points outside the grid's UTM zone frame (grids never straddle
      // zones materially; edge artefacts are masked instead of distorted).
      const pzone = utm.zone;
      const idx = (py * W + px) * 4;
      if (pzone !== gridZone) {
        img.data[idx + 3] = 0;
        continue;
      }
      const j = Math.floor((utm.easting - meta.xStart) / meta.xSpacing);
      const i = Math.floor((meta.yStart - utm.northing) / -meta.ySpacing);
      if (i < 0 || i >= h || j < 0 || j >= w) {
        img.data[idx + 3] = 0;
        continue;
      }
      const gi = i * w + j;
      if (!grids.mask[gi]) {
        img.data[idx + 3] = 0;
        continue;
      }
      if (layer === 'coverage') {
        // Flat neutral tone wherever a VALID measurement exists — shows the
        // analysed footprint honestly for scenes whose displacement is ~zero
        // (near-white on the diverging scale) or whose valid mask is sparse.
        img.data[idx] = 148;
        img.data[idx + 1] = 140;
        img.data[idx + 2] = 129;
        img.data[idx + 3] = 235;
        continue;
      }
      if (layer === 'detected') {
        // Detected change: solid sign colours ONLY where the server-side
        // rule (|d| > max(k·σ, floor)) passes; everything else transparent.
        // The plainest possible highlight of changed ground.
        if (!grids.detected[gi]) {
          img.data[idx + 3] = 0;
          continue;
        }
        const v = grids.displacement[gi];
        const css = Number.isFinite(v)
          ? displacementColor(v > 0 ? 0.8 : -0.8)
          : 'rgb(148,140,129)';
        const m = css.match(/rgb\((\d+),(\d+),(\d+)\)/);
        if (m) {
          img.data[idx] = parseInt(m[1], 10);
          img.data[idx + 1] = parseInt(m[2], 10);
          img.data[idx + 2] = parseInt(m[3], 10);
          img.data[idx + 3] = 235;
        } else {
          img.data[idx + 3] = 0;
        }
        continue;
      }
      let css: string;
      if (layer === 'displacement' || layer === 'velocity') {
        const v = grids.displacement[gi];
        if (!Number.isFinite(v)) {
          img.data[idx + 3] = 0;
          continue;
        }
        css = displacementColor(v / (scaleMax ?? 1));
      } else if (layer === 'phase') {
        // Wrapped phase fringes (advanced): displacement is a linear image
        // of the unwrapped phase (phase = d / (λ/4π)), so the wrapped phase
        // is exactly recoverable — one full color cycle = λ/2 of LOS motion.
        const v = grids.displacement[gi];
        const wl = wavelengthM ?? 0;
        if (!Number.isFinite(v) || !(wl > 0)) {
          img.data[idx + 3] = 0;
          continue;
        }
        const losPerRad = wl / (4 * Math.PI);
        const ph = (v / losPerRad) % (2 * Math.PI);
        css = phaseColor(ph / (2 * Math.PI));
      } else if (layer === 'coherence') {
        const v = grids.coherence[gi];
        css = coherenceColor(Number.isFinite(v) ? v : 0);
      } else {
        const v = grids.sigma[gi];
        if (!Number.isFinite(v)) {
          img.data[idx + 3] = 0;
          continue;
        }
        css = sigmaColor(v / sigmaMax);
      }
      const m = css.match(/rgb\((\d+),(\d+),(\d+)\)/);
      if (m) {
        img.data[idx] = parseInt(m[1], 10);
        img.data[idx + 1] = parseInt(m[2], 10);
        img.data[idx + 2] = parseInt(m[3], 10);
        img.data[idx + 3] = 255;
      } else {
        img.data[idx + 3] = 0;
      }
    }
  }
  ctx.putImageData(img, 0, 0);
  return { dataUrl: canvas.toDataURL('image/png'), geo, scaleMaxM: scaleMax };
}

/** Sample a decoded grid at a lat/lon (for the hover readout). */
export function sampleAt(
  grids: DecodedGrids,
  meta: { xStart: number; xSpacing: number; yStart: number; ySpacing: number; width: number; height: number; epsg: number },
  lat: number,
  lon: number,
): { displacement: number; coherence: number; sigma: number; detected: boolean } | undefined {
  const utm = latLonToUtm(lat, lon);
  const gridZone = meta.epsg > 32600 && meta.epsg <= 32660 ? meta.epsg - 32600 : meta.epsg - 32700;
  if (utm.zone !== gridZone) return undefined;
  const j = Math.floor((utm.easting - meta.xStart) / meta.xSpacing);
  const i = Math.floor((meta.yStart - utm.northing) / -meta.ySpacing);
  if (i < 0 || i >= grids.height || j < 0 || j >= grids.width) return undefined;
  const gi = i * grids.width + j;
  if (!grids.mask[gi]) return undefined;
  return {
    displacement: grids.displacement[gi],
    coherence: grids.coherence[gi],
    sigma: grids.sigma[gi],
    detected: grids.detected[gi] === 1,
  };
}
