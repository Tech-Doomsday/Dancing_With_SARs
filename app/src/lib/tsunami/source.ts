/**
 * Tsunami source initialization: earthquake → seafloor displacement →
 * initial free-surface elevation.
 *
 * Pipeline (each step documented + unit-tested):
 *  1. Fault geometry: USGS finite-fault FFM.geojson subfaults (preferred) or a
 *     single uniform plane from the USGS moment-tensor nodal plane with
 *     Wells–Coppersmith (1994) area scaling (labelled approximation).
 *  2. Elastic dislocation: the project's verified Okada/DC3D port, summed
 *     over subfaults (static approximation — rupture duration ≪ wave period;
 *     standard for far-field modelling).
 *  3. Tanioka & Seno (1997) bathymetric-slope correction:
 *         Δh = u_z + u_x ∂b/∂x + u_y ∂b/∂y
 *     with smoothed bed gradients (3×3 box average) to avoid amplifying
 *     DEM noise.
 *  4. Kajiura (1963) water-column transfer: η̂(k) = Δĥ(k)/cosh(k·d) applied
 *     per row/column with the local mean still-water depth (local-depth form
 *     of the constant-depth filter, separable approximation of the radial
 *     response). Long waves (k·d ≪ 1) pass unchanged.
 *
 * The initial condition handed to the solver is η(x,y,0) = filtered Δh on
 * wet cells, with the bed UNCHANGED (the static-transfer formulation); cells
 * where the filtered surface would fall below the bed are clamped to the bed
 * (h = 0) — the solver does the rest via hydrostatic reconstruction.
 */

import {
  geoFaultDisplacementENUTopEdge,
  momentFromMw,
  ruptureAreaFromMw,
  slipFromMoment,
  type GeoFault,
} from '@/lib/science/okada';
import { fftInPlace, nextPow2 } from './fft';
import type { BathymetryGrid, SourceField, TsunamiProvenance } from './types';

const R_EARTH = 6371.0088e3; // m

/* ------------------------------------------------------------------ */
/* Finite-fault model (USGS FFM.geojson)                               */
/* ------------------------------------------------------------------ */

export interface Subfault {
  /** centroid */
  clat: number;
  clon: number;
  /** top-edge depth (m) */
  topDepthM: number;
  strikeDeg: number;
  dipDeg: number;
  rakeDeg: number;
  slipM: number;
  lengthM: number;
  widthM: number;
  /** seismic moment of this subfault (N·m), from properties when present */
  momentNM: number;
  /** rise/rupture times when the model provides them (s) */
  riseS?: number;
  trupS?: number;
}

interface FfmFeature {
  geometry: { coordinates: number[][][] };
  properties: Record<string, number | string>;
}

/** Azimuth (deg clockwise from north) from point A to B on the sphere. */
function azimuthDeg(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const y = Math.sin(dl) * Math.cos(p2);
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return (Math.atan2(y, x) * 180) / Math.PI;
}

/** Local metre offsets (east, north) of a lat/lon point relative to a reference. */
function enOffsets(refLat: number, refLon: number, lat: number, lon: number): [number, number] {
  const meanLat = ((refLat + lat) / 2) * (Math.PI / 180);
  const east = (((lon - refLon) * Math.PI) / 180) * R_EARTH * Math.cos(meanLat);
  const north = (((lat - refLat) * Math.PI) / 180) * R_EARTH;
  return [east, north];
}

/**
 * Parse a USGS FFM.geojson feature collection into subfaults.
 *
 * Each feature is a closed 5-point polygon: corners [0]-[1] are the TOP edge
 * (shallower), [3]-[2] the bottom edge. Strike comes from the top-edge
 * azimuth, dip from the depth contrast across the plane, and the top-edge
 * depth from the shallow corners. (Format verified against the Maule 2010
 * product, 236 subfaults.)
 */
export function parseFfmGeojson(geo: { features: FfmFeature[] }): Subfault[] {
  const out: Subfault[] = [];
  for (const f of geo.features) {
    const ring = f.geometry.coordinates[0];
    if (!ring || ring.length < 4) continue;
    const p = f.properties;
    const slip = Number(p.slip);
    const rake = Number(p.rake);
    if (!Number.isFinite(slip) || !Number.isFinite(rake)) continue;

    const c0 = ring[0];
    const c1 = ring[1];
    const c2 = ring[2];
    const c3 = ring[3];
    const clat = (c0[1] + c1[1] + c2[1] + c3[1]) / 4;
    const clon = (c0[0] + c1[0] + c2[0] + c3[0]) / 4;

    const strike = azimuthDeg(c0[1], c0[0], c1[1], c1[0]);
    const [e0, n0] = enOffsets(clat, clon, c0[1], c0[0]);
    const [e1, n1] = enOffsets(clat, clon, c1[1], c1[0]);
    const lengthM = Math.hypot(e1 - e0, n1 - n0);
    if (lengthM < 1) continue;

    const [e3, n3] = enOffsets(clat, clon, c3[1], c3[0]);
    const horiz = Math.hypot(e3 - e0, n3 - n0);
    const dTop = (c0[2] + c1[2]) / 2;
    const dBot = (c2[2] + c3[2]) / 2;
    const widthM = Math.hypot(horiz, dBot - dTop);
    if (widthM < 1) continue;
    const dip = (Math.atan2(Math.abs(dBot - dTop), Math.max(horiz, 1e-6)) * 180) / Math.PI;
    if (!Number.isFinite(dip) || dip <= 0.05 || dip >= 179.9) continue;

    const moment = Number(p.sf_moment);
    out.push({
      clat,
      clon,
      topDepthM: Math.max(0, Math.min(dTop, dBot)),
      strikeDeg: ((strike % 360) + 360) % 360,
      dipDeg: Math.min(dip, 90),
      rakeDeg: rake,
      slipM: slip,
      lengthM,
      widthM,
      momentNM: Number.isFinite(moment) ? moment : 0,
      riseS: Number.isFinite(Number(p.rise)) ? Number(p.rise) : undefined,
      trupS: Number.isFinite(Number(p.trup)) ? Number(p.trup) : undefined,
    });
  }
  return out;
}

/** Fetch + parse the USGS finite-fault FFM.geojson for an event. */
export async function fetchUsgsFiniteFault(
  usgsEventId: string,
  timeoutMs = 60_000,
): Promise<{ subfaults: Subfault[]; url: string } | null> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const detailUrl = `https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&eventid=${usgsEventId}`;
    const res = await fetch(detailUrl, { signal: ctrl.signal });
    if (!res.ok) throw new Error(`USGS detail HTTP ${res.status}`);
    const detail = (await res.json()) as {
      properties?: { products?: Record<string, Array<{ contents?: Record<string, { url: string }> }>> };
    };
    const ff = detail.properties?.products?.['finite-fault']?.[0];
    const ffmUrl = ff?.contents?.['FFM.geojson']?.url;
    if (!ffmUrl) return null;
    const ffmRes = await fetch(ffmUrl, { signal: ctrl.signal });
    if (!ffmRes.ok) throw new Error(`FFM.geojson HTTP ${ffmRes.status}`);
    const geo = (await ffmRes.json()) as { features: FfmFeature[] };
    const subfaults = parseFfmGeojson(geo);
    if (subfaults.length === 0) return null;
    return { subfaults, url: ffmUrl };
  } finally {
    clearTimeout(timer);
  }
}

/* ------------------------------------------------------------------ */
/* Uniform single-plane fallback                                       */
/* ------------------------------------------------------------------ */

export interface UniformFaultInput {
  magnitude: number;
  depthKm: number;
  lat: number;
  lon: number;
  nodalPlane: { strike: number; dip: number; rake: number };
}

/**
 * Single rectangular plane from the MT nodal plane with Wells–Coppersmith
 * scaling. Honest about the large uncertainty: WC94 regression scatter is
 * ~0.25 orders of magnitude in area (factor ≈ 1.8), and uniform slip
 * concentrates the peak uplift. The UI labels this an approximation.
 */
export function uniformFaultFromScaling(inp: UniformFaultInput): { fault: Subfault; notes: string[] } {
  const notes: string[] = [];
  const area = ruptureAreaFromMw(inp.magnitude); // m²
  const m0 = momentFromMw(inp.magnitude);
  const ratio = inp.magnitude >= 7.5 ? 2 : 1;
  const w = Math.sqrt(area / ratio);
  const l = area / w;
  const slip = slipFromMoment(m0, area);
  const topDepthM = Math.max(
    0,
    inp.depthKm * 1000 - (w / 2) * Math.sin((inp.nodalPlane.dip * Math.PI) / 180),
  );
  notes.push(
    `Wells & Coppersmith (1994) all-slip-types scaling: log10(A/km²) = 0.91·Mw − 3.49 → A = ${(area / 1e6).toFixed(0)} km² (scatter ≈ ×1.8)`,
    `uniform slip ${(slip * 100).toFixed(0)} cm from M0 = μ·A·s with μ = 30 GPa`,
    `plane ${l.toFixed(0)} m × ${w.toFixed(0)} m, top edge at ${(topDepthM / 1000).toFixed(1)} km below sea level`,
  );
  return {
    fault: {
      clat: inp.lat,
      clon: inp.lon,
      topDepthM,
      strikeDeg: inp.nodalPlane.strike,
      dipDeg: inp.nodalPlane.dip,
      rakeDeg: inp.nodalPlane.rake,
      slipM: slip,
      lengthM: l,
      widthM: w,
      momentNM: m0,
    },
    notes,
  };
}

/* ------------------------------------------------------------------ */
/* Deformation on the simulation grid                                  */
/* ------------------------------------------------------------------ */

export interface SourceOptions {
  /** include the Tanioka-Seno horizontal-motion slope correction */
  slopeCorrection: boolean;
  /** apply the Kajiura water-column filter */
  kajiuraFilter: boolean;
}

export const DEFAULT_SOURCE_OPTIONS: SourceOptions = {
  slopeCorrection: true,
  kajiuraFilter: true,
};

/** Smoothed bed gradients (3×3 box average), m/m. */
function bedGradients(grid: BathymetryGrid): { bx: Float32Array; by: Float32Array } {
  const { nx, ny, bed } = grid;
  const bx = new Float32Array(nx * ny);
  const by = new Float32Array(nx * ny);
  const dLatM = ((grid.resArcmin / 60) * Math.PI * R_EARTH) / 180;
  for (let j = 1; j < ny - 1; j++) {
    const meanLat = grid.lat0 + (j * grid.resArcmin) / 60;
    const dLonM =
      (((grid.resArcmin / 60) * Math.PI * R_EARTH * Math.cos((meanLat * Math.PI) / 180)) / 180);
    for (let i = 1; i < nx - 1; i++) {
      let dxSum = 0;
      let dySum = 0;
      for (let dj = -1; dj <= 1; dj++) {
        for (let di = -1; di <= 1; di++) {
          dxSum += bed[(j + dj) * nx + (i + di + 1)] - bed[(j + dj) * nx + (i + di - 1)];
          dySum += bed[(j + dj + 1) * nx + (i + di)] - bed[(j + dj - 1) * nx + (i + di)];
        }
      }
      bx[j * nx + i] = dxSum / (9 * 2 * dLonM);
      by[j * nx + i] = dySum / (9 * 2 * dLatM);
    }
  }
  return { bx, by };
}

/**
 * Compute the combined vertical seafloor displacement on the grid.
 * Subfault contributions are evaluated within a radius of 1.5·(L+W) of each
 * subfault centroid (beyond that the elastic field is sub-cm — irrelevant at
 * tsunami amplitudes and far below ETOPO1 accuracy).
 */
export function computeDeformation(
  grid: BathymetryGrid,
  subfaults: Subfault[],
  opts: SourceOptions,
): { dz: Float32Array; maxAbsDz: number; notes: string[] } {
  const { nx, ny } = grid;
  const dz = new Float32Array(nx * ny);
  const notes: string[] = [];

  let bx: Float32Array | null = null;
  let by: Float32Array | null = null;
  if (opts.slopeCorrection) {
    const g = bedGradients(grid);
    bx = g.bx;
    by = g.by;
  }

  for (const sf of subfaults) {
    const fault: GeoFault = {
      strikeDeg: sf.strikeDeg,
      dipDeg: sf.dipDeg,
      rakeDeg: sf.rakeDeg,
      lengthM: sf.lengthM,
      widthM: sf.widthM,
      topDepthM: sf.topDepthM,
      slipM: sf.slipM,
      centroidLat: sf.clat,
      centroidLon: sf.clon,
    };
    const radius = 1.5 * (sf.lengthM + sf.widthM);
    const dLatDeg = (radius / R_EARTH) * (180 / Math.PI);
    const dLonDeg = dLatDeg / Math.max(0.1, Math.cos((sf.clat * Math.PI) / 180));
    const jMin = Math.max(0, Math.floor(((sf.clat - dLatDeg - grid.lat0) * 60) / grid.resArcmin) - 1);
    const jMax = Math.min(ny - 1, Math.ceil(((sf.clat + dLatDeg - grid.lat0) * 60) / grid.resArcmin) + 1);
    const iMin = Math.max(0, Math.floor(((sf.clon - dLonDeg - grid.lon0) * 60) / grid.resArcmin) - 1);
    const iMax = Math.min(nx - 1, Math.ceil(((sf.clon + dLonDeg - grid.lon0) * 60) / grid.resArcmin) + 1);

    for (let j = jMin; j <= jMax; j++) {
      const lat = grid.lat0 + (j * grid.resArcmin) / 60;
      for (let i = iMin; i <= iMax; i++) {
        const lon = grid.lon0 + (i * grid.resArcmin) / 60;
        const [e, nOff] = enOffsets(sf.clat, sf.clon, lat, lon);
        if (e * e + nOff * nOff > radius * radius) continue;
        const idx = j * nx + i;
        // The top-edge midpoint sits UP-DIP of the centroid by
        // (W/2)·cos(dip) along the dip direction (cos strike, −sin strike).
        const dipDirE = Math.cos((sf.strikeDeg * Math.PI) / 180);
        const dipDirN = -Math.sin((sf.strikeDeg * Math.PI) / 180);
        const halfHoriz = (sf.widthM / 2) * Math.cos((sf.dipDeg * Math.PI) / 180);
        const disp = geoFaultDisplacementENUTopEdge(
          fault,
          sf.topDepthM,
          e + halfHoriz * dipDirE,
          nOff + halfHoriz * dipDirN,
        );
        if (disp.singular) continue;
        let v = disp.up;
        if (bx && by) {
          v += disp.east * bx[idx] + disp.north * by[idx];
        }
        dz[idx] += v;
      }
    }
  }

  notes.push(
    opts.slopeCorrection
      ? 'Tanioka & Seno (1997) slope correction u_h·∇b applied (3×3-smoothed bed gradients)'
      : 'vertical displacement only (slope correction disabled)',
  );

  let maxAbs = 0;
  for (let k = 0; k < dz.length; k++) {
    const a = Math.abs(dz[k]);
    if (a > maxAbs) maxAbs = a;
  }
  return { dz, maxAbsDz: maxAbs, notes };
}

/* ------------------------------------------------------------------ */
/* Kajiura filter                                                      */
/* ------------------------------------------------------------------ */

/** Mean still-water depth over wet cells (m) of a padded depth scratch row. */
function meanDepth(depth: Float64Array, n: number): number {
  let s = 0;
  let c = 0;
  for (let i = 0; i < n; i++) {
    if (depth[i] > 0) {
      s += depth[i];
      c++;
    }
  }
  return c > 0 ? s / c : 0;
}

/**
 * Kajiura (1963) water-column transfer 1/cosh(k·d) as a separable
 * row-then-column pass with each line's mean wet depth (documented
 * approximation of the radial constant-depth response; identical to leading
 * order for long waves, which is the regime of seismic sources).
 */
export function kajiuraFilter(grid: BathymetryGrid, dz: Float32Array): Float32Array {
  const { nx, ny, bed } = grid;
  const out = new Float32Array(nx * ny);
  const nxp = nextPow2(nx);
  const nyp = nextPow2(ny);
  const dyM = ((grid.resArcmin / 60) * Math.PI * R_EARTH) / 180;

  const filterLine = (
    re: Float64Array,
    im: Float64Array,
    nPhys: number,
    nPad: number,
    dMean: number,
    cellM: number,
  ) => {
    fftInPlace(re, im, false);
    const half = nPad >> 1;
    for (let k = 1; k <= half; k++) {
      const kx = (2 * Math.PI * k) / (nPad * cellM);
      const arg = kx * dMean;
      const scale = arg > 50 ? 0 : 1 / Math.cosh(arg);
      const mirror = nPad - k;
      re[k] *= scale;
      im[k] *= scale;
      if (mirror !== k) {
        re[mirror] *= scale;
        im[mirror] *= scale;
      }
    }
    fftInPlace(re, im, true);
  };

  // Row pass (x)
  {
    const re = new Float64Array(nxp);
    const im = new Float64Array(nxp);
    const depth = new Float64Array(nxp);
    for (let j = 0; j < ny; j++) {
      const meanLat = grid.lat0 + (j * grid.resArcmin) / 60;
      const dxM =
        (((grid.resArcmin / 60) * Math.PI * R_EARTH * Math.cos((meanLat * Math.PI) / 180)) / 180);
      re.fill(0);
      im.fill(0);
      depth.fill(0);
      for (let i = 0; i < nx; i++) {
        re[i] = dz[j * nx + i];
        depth[i] = Math.max(0, -bed[j * nx + i]);
      }
      filterLine(re, im, nx, nxp, meanDepth(depth, nx), dxM);
      for (let i = 0; i < nx; i++) out[j * nx + i] = re[i];
    }
  }

  // Column pass (y)
  {
    const re = new Float64Array(nyp);
    const im = new Float64Array(nyp);
    const depth = new Float64Array(nyp);
    for (let i = 0; i < nx; i++) {
      re.fill(0);
      im.fill(0);
      depth.fill(0);
      for (let j = 0; j < ny; j++) {
        re[j] = out[j * nx + i];
        depth[j] = Math.max(0, -bed[j * nx + i]);
      }
      filterLine(re, im, ny, nyp, meanDepth(depth, ny), dyM);
      for (let j = 0; j < ny; j++) out[j * nx + i] = re[j];
    }
  }

  return out;
}

/* ------------------------------------------------------------------ */
/* Full source field                                                   */
/* ------------------------------------------------------------------ */

/**
 * Full source construction: deformation + Kajiura filter → SourceField.
 * η0 is clamped so the initial surface never lies below the bed (cells that
 * subside below the bed start dry).
 */
export function buildSourceField(
  grid: BathymetryGrid,
  subfaults: Subfault[],
  sourceKind: SourceField['sourceKind'],
  opts: SourceOptions,
  detail: string,
  momentNM: number | null,
): SourceField {
  const { dz, maxAbsDz, notes } = computeDeformation(grid, subfaults, opts);
  let eta0: Float32Array;
  if (opts.kajiuraFilter) {
    eta0 = kajiuraFilter(grid, dz);
    notes.push('Kajiura (1963) water-column transfer 1/cosh(k·d) applied (separable local-depth form)');
  } else {
    eta0 = Float32Array.from(dz);
    notes.push('no water-column filtering (instantaneous static transfer)');
  }
  let clamped = 0;
  for (let k = 0; k < eta0.length; k++) {
    if (eta0[k] < grid.bed[k]) {
      eta0[k] = grid.bed[k];
      clamped++;
    }
  }
  if (clamped > 0) {
    notes.push(`${clamped} cells where the filtered surface fell below the bed were set dry (η = b)`);
  }
  return { dz, eta0, maxAbsDz, sourceKind, momentNM, detail, notes };
}

/** Source provenance helper for the run record. */
export function sourceProvenance(
  kind: SourceField['sourceKind'],
  url: string,
  detail: string,
): TsunamiProvenance {
  return {
    datasetId: `tsunami-source-${kind}`,
    provider:
      kind === 'usgs-finite-fault'
        ? 'USGS NEIC (finite-fault product)'
        : 'USGS NEIC (moment tensor) + Wells–Coppersmith scaling',
    productId: detail,
    productType: 'earthquake source model',
    sourceUrl: url,
    retrievedAt: new Date().toISOString(),
    observationStart: 'earthquake origin time',
    observationEnd: 'earthquake origin time',
    units: 'slip m; displacement m',
    crs: 'WGS84 geographic',
    verticalDatum: 'elastic half-space surface, no datum shift',
    processing: [
      'Okada (1985/1992 DC3D) dislocation summed over subfaults (static approximation)',
      'Tanioka & Seno (1997) horizontal-slope correction with smoothed bed gradients',
      'Kajiura (1963) 1/cosh(k·d) water-column filter (separable local-depth form)',
    ],
    dataClass: 'modelled',
    qualityNotes: [
      'static (instantaneous) rupture approximation: valid for far-field phases whose period ≫ rupture duration',
      'uniform-slip fallback concentrates peak uplift relative to heterogeneous inversions',
    ],
  };
}
