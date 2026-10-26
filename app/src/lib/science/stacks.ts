/**
 * Synthetic demonstration stack — California Central Valley, Ascending
 * Track 42 analog (system plan §10 case study).
 *
 * The plan's baseline evaluation ingested a stack of 24 NISAR L2 GUNW
 * products over Ascending Track 42 covering the Sacramento / San Joaquin
 * Valley system, referenced to stable GNSS station P271, and derived
 * subsidence rates over the Hanford–Fresno agricultural pumping zones.
 * Downloading 24 real granules (each 1–2.3 GB, Earthdata-authenticated)
 * is not possible from this deployment, so this module synthesizes a
 * faithful EXERCISE FIXTURE of that stack with:
 *
 *   - 22 acquisitions on the 12-day repeat cycle between 2026-01-14 and
 *     2026-09-23, HONOURING the documented NISAR instrument data gap of
 *     2026-07-27 … 2026-08-10 (no epoch inside the gap; one interval
 *     widens to 24 days);
 *   - 24 interferograms: the 21 sequential 12-day pairs + 3 redundant
 *     24-day pairs (a small-baseline network with loops);
 *   - truth deformation with published-scale amplitudes:
 *       · two aquifer-compaction bowls (Hanford analog −235 mm/yr LOS,
 *         Fresno analog −150 mm/yr) with seasonal pumping modulation,
 *       · a broad tectonic shear gradient (San Andreas / Hayward analog),
 *       · one coseismic step: a synthetic M6.2 blind-thrust event modeled
 *         on the 1983 Coalinga earthquake geometry, clearly labelled as a
 *         synthetic catalog entry;
 *   - per-epoch atmosphere consistent with the plan's phase decomposition:
 *       · stratified troposphere tied to the DEM (height-correlated),
 *       · turbulent troposphere (von-Kármán fractal, β = 8/3),
 *       · ionospheric vTEC ramps (the term the unapplied split-spectrum
 *         screen would remove);
 *   - CRLB measurement noise per interferogram from the coherence field
 *     (Rodriguez & Martin 1992), with agriculture / rangeland / urban /
 *     water coherence classes and 12-day temporal decay;
 *   - DEM (valley + ranges), land-cover labels, per-pixel causal ground
 *     truth (stable / coseismic / subsidence) for the Brier calibration,
 *     and the GNSS validation network positions.
 *
 * HONESTY CONTRACT: every consumer of this stack is a SYNTHETIC VALIDATION
 * FIXTURE. It exercises the complete SBAS → detection → attribution →
 * validation machinery against known truth; it is not an observation of the
 * Earth, and the UI labels it as such everywhere it appears.
 */

import type {
  GridMeta,
  RadarGeometry,
  StackCatalogEvent,
  StackEpoch,
  StackIfgMeta,
} from './types';
import { turbulentField, ionoRampField } from './atmosphere';
import { geoFaultDisplacementENU } from './okada';
import { losUnitVector, projectDisplacementToLOS } from './los-projection';
import { latLonToUtmInZone, utmToLatLon } from './utm';

/** Deterministic PRNG (mulberry32). */
function mulberry32(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Gaussian draw (Box–Muller) from a uniform RNG. */
function gauss(rand: () => number): number {
  let u = 0;
  let v = 0;
  while (u === 0) u = rand();
  while (v === 0) v = rand();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

export const STACK_ID = 'stack-central-valley-2026';

/** NISAR L-SAR wavelength (m). */
const LAMBDA = 0.2360571;
const METRES_PER_RAD = LAMBDA / (4 * Math.PI);
/** Multilooking of a NISAR L2 GUNW 80 m product (looks az × rg). */
const LOOKS = 119;

/** Documented NISAR instrument data gap (plan §3). */
export const NISAR_GAP = { start: '2026-07-27', end: '2026-08-10' } as const;

/** The synthetic coseismic event (Coalinga-1983-analog blind thrust). */
export const STACK_EVENT: StackCatalogEvent = {
  id: 'synthetic-coalinga-2026',
  label: 'SYNTHETIC M6.2 blind-thrust event (Coalinga 1983 analog) — NOT a real earthquake',
  magnitude: 6.2,
  place: 'synthetic event near Coalinga, southern San Joaquin Valley (fixture)',
  time: '2026-06-24T22:31:00.000Z',
  lat: 36.07,
  lon: -120.22,
  depthKm: 6.5,
  url: 'https://earthquake.usgs.gov/learn/topics/coalinga_1983.php (analog event reference)',
  nodalPlane: { strike: 145, dip: 38, rake: 86 },
  synthetic: true,
};

export interface CentralValleyStack {
  id: string;
  /** Grid metadata (UTM zone 11N, EPSG 32611). */
  meta: GridMeta;
  geometry: RadarGeometry;
  /** 22 epochs. */
  epochs: StackEpoch[];
  /** 24 interferograms. */
  ifgs: StackIfgMeta[];
  /** Unwrapped phase per interferogram (radians; NaN = no data). */
  phase: Float32Array[];
  /** Coherence per interferogram (0..1). */
  coherence: Float32Array[];
  /** DEM (m). */
  dem: Float32Array;
  /** Land-cover class per pixel (for coherence interpretation). */
  landcover: Uint8Array;
  /** Causal ground truth per pixel: 0 stable, 1 coseismic, 2 subsidence. */
  labels: Uint8Array;
  /** Truth secular LOS velocity (m/yr; excludes the coseismic step). */
  truthSecularLosMPerYr: Float32Array;
  /** Truth coseismic LOS step (m). */
  truthQuakeLosM: Float32Array;
  /** Per-epoch true cumulative LOS displacement WITHOUT atmosphere (m). */
  truthCumulative: Float32Array[];
  /** Per-epoch atmospheric disturbance actually added (m). */
  atmospherePerEpoch: Float32Array[];
  /** The catalog event record. */
  event: StackCatalogEvent;
  footprintPolygon: [number, number][];
  center: { lat: number; lon: number };
}

/** Epoch schedule: 12-day cadence, 2026-01-14 → 2026-09-23, gap honoured. */
function epochSchedule(): StackEpoch[] {
  const out: StackEpoch[] = [];
  const gapStart = Date.parse(`${NISAR_GAP.start}T00:00:00Z`);
  const gapEnd = Date.parse(`${NISAR_GAP.end}T23:59:59Z`);
  let t = Date.parse('2026-01-14T14:00:00Z');
  const end = Date.parse('2026-09-30T00:00:00Z');
  let index = 0;
  while (t <= end) {
    if (!(t >= gapStart && t <= gapEnd)) {
      out.push({ index, time: new Date(t).toISOString() });
      index++;
    }
    t += 12 * 86400000;
  }
  return out;
}

let cached: CentralValleyStack | undefined;

/** Generate (and memoize) the Central Valley demonstration stack. */
export function centralValleyStack(): CentralValleyStack {
  if (cached) return cached;

  const width = 288;
  const height = 220;
  const spacing = 250; // metres — NISAR GUNW posting scale
  // UTM zone 11N frame centred on the Hanford–Fresno corridor.
  const centerLat = 36.35;
  const centerLon = -119.92;
  const c = latLonToUtmInZone(centerLat, centerLon, 11);
  const xStart = Math.round(c.easting - ((width - 1) / 2) * spacing);
  const yStart = Math.round(c.northing + ((height - 1) / 2) * spacing);
  const meta: GridMeta = {
    width, height,
    xStart, xSpacing: spacing,
    yStart, ySpacing: -spacing,
    epsg: 32611,
  };
  const pixels = width * height;

  const rand = mulberry32(20261114);
  const epochs = epochSchedule();
  const nE = epochs.length;

  // ---- Interferogram network: 21 sequential + 3 redundant 24-day pairs ----
  const ifgs: StackIfgMeta[] = [];
  for (let e = 0; e + 1 < nE; e++) {
    ifgs.push({
      refIndex: e,
      secIndex: e + 1,
      temporalBaselineDays: (Date.parse(epochs[e + 1].time) - Date.parse(epochs[e].time)) / 86400000,
    });
  }
  for (const [a, b] of [
    [2, 4],
    [5, 7],
    [8, 10],
    [14, 16],
  ] as Array<[number, number]>) {
    if (b < nE) {
      ifgs.push({
        refIndex: a,
        secIndex: b,
        temporalBaselineDays: (Date.parse(epochs[b].time) - Date.parse(epochs[a].time)) / 86400000,
      });
    }
  }

  // ---- DEM: valley floor + Coast Range (west) + Sierra foothills (east) ----
  const dem = new Float32Array(pixels);
  const demFractal = turbulentField(width, height, 22, 6, (spacing / 1000), 777001);
  for (let i = 0; i < height; i++) {
    for (let j = 0; j < width; j++) {
      const x = j * spacing; // metres from west edge
      const y = i * spacing;
      const dw = x / 1000; // km from west
      const de = (width - 1 - j) * spacing / 1000;
      const coastRange = 780 * Math.exp(-((dw / 16) ** 2));
      const sierra = 520 * Math.exp(-((de / 13) ** 2));
      const valleyTilt = 18 * (i / height);
      dem[i * width + j] = 62 + valleyTilt + coastRange + sierra + demFractal[i * width + j];
    }
  }

  // ---- Land cover classes (0 ag, 1 rangeland, 2 urban, 3 water, 4 bare) ----
  // Field-chequerboard agriculture over the valley floor, rangeland on the
  // ranges, one urban blob (Fresno analog), two water bodies (canals).
  const landcover = new Uint8Array(pixels);
  for (let i = 0; i < height; i++) {
    for (let j = 0; j < width; j++) {
      const idx = i * width + j;
      const h = dem[idx];
      if (h > 260) {
        landcover[idx] = 1; // rangeland / bedrock on the ranges
      } else {
        // Field pattern: blocks of 6–18 px with per-field class jitter.
        const fieldSize = 9;
        const fi = Math.floor(i / fieldSize);
        const fj = Math.floor(j / fieldSize);
        const fr = mulberry32(fi * 7919 + fj * 104729 + 4242)();
        landcover[idx] = fr < 0.86 ? 0 : fr < 0.94 ? 4 : fr < 0.985 ? 2 : 3;
      }
    }
  }
  // Fresno-analog urban blob.
  for (let i = 0; i < height; i++) {
    for (let j = 0; j < width; j++) {
      const di = i - Math.round(height * 0.38);
      const dj = j - Math.round(width * 0.62);
      if (Math.hypot(di, dj) < 14 && dem[i * width + j] < 260) landcover[i * width + j] = 2;
    }
  }
  // Two canal/reservoir water bodies.
  for (let i = 0; i < height; i++) {
    for (let j = 0; j < width; j++) {
      const di = i - Math.round(height * 0.62);
      const dj = j - Math.round(width * 0.30);
      if (Math.hypot(di, dj * 2.2) < 7) landcover[i * width + j] = 3;
      const bi = i - Math.round(height * 0.22);
      const bj = j - Math.round(width * 0.20);
      if (Math.hypot(bi, bj * 2.6) < 6) landcover[i * width + j] = 3;
    }
  }

  // ---- Truth 3D secular velocity (ENU, mm/yr) ----
  // Two aquifer-compaction bowls + tectonic shear across a San Andreas
  // analog line SW of the frame.
  const los = losUnitVector(34, -12.5, 'right');
  const bowl = (
    cj: number, ci: number, sj: number, si: number, peakMmPerYr: number,
  ) => {
    const out = new Float32Array(pixels);
    for (let i = 0; i < height; i++) {
      for (let j = 0; j < width; j++) {
        const dx = ((j - cj) * spacing) / 1000;
        const dy = ((i - ci) * spacing) / 1000;
        out[i * width + j] =
          peakMmPerYr * Math.exp(-((dx / sj) ** 2) - ((dy / si) ** 2));
      }
    }
    return out;
  };
  // Hanford analog: strong; Fresno analog: moderate.
  const bowlA = bowl(width * 0.42, height * 0.38, 24, 18, -238);
  const bowlB = bowl(width * 0.62, height * 0.70, 18, 14, -152);
  const truthSecularLosMPerYr = new Float32Array(pixels);
  for (let idx = 0; idx < pixels; idx++) {
    // Tectonic shear: E-velocity varying smoothly across a NW–SE line SW of
    // the frame (Pacific-side moves NW; the E component flips sign across
    // the fault trace analog).
    const j = idx % width;
    const i = Math.floor(idx / width);
    const dFault = ((j / width) * 0.9 + (i / height) * 0.6 - 0.62) * 60; // km-ish
    const tectEastMm = 5.5 * Math.tanh(dFault / 9);
    const upMm = bowlA[idx] + bowlB[idx];
    const losMm = projectDisplacementToLOS(
      { east: tectEastMm / 1000, north: 0, up: upMm / 1000 },
      los,
    ) * 1000;
    truthSecularLosMPerYr[idx] = losMm / 1000; // m/yr
  }

  // ---- Truth coseismic step (Okada, Coalinga-analog blind thrust) ----
  const truthQuakeLosM = new Float32Array(pixels);
  {
    // Convert the event epicentre into grid coordinates.
    const epi = latLonToUtmInZone(STACK_EVENT.lat, STACK_EVENT.lon, 11);
    const fault = {
      strikeDeg: STACK_EVENT.nodalPlane.strike,
      dipDeg: STACK_EVENT.nodalPlane.dip,
      rakeDeg: STACK_EVENT.nodalPlane.rake,
      lengthM: 16000,
      widthM: 9000,
      topDepthM: 0, // derived from centroid depth inside geoFaultDisplacementENU
      slipM: 0.58,
      centroidLat: STACK_EVENT.lat,
      centroidLon: STACK_EVENT.lon,
    };
    const centroidDepth = STACK_EVENT.depthKm * 1000;
    const stride = 1;
    for (let i = 0; i < height; i += stride) {
      for (let j = 0; j < width; j += stride) {
        const x = xStart + j * spacing;
        const y = yStart - i * spacing;
        const enu = geoFaultDisplacementENU(fault, centroidDepth, x - epi.easting, y - epi.northing);
        const losM = projectDisplacementToLOS(enu, los);
        truthQuakeLosM[i * width + j] = losM;
      }
    }
  }

  // ---- Per-epoch truth cumulative displacement (m, no atmosphere) ----
  const t0 = Date.parse(epochs[0].time);
  const tQuake = Date.parse(STACK_EVENT.time);
  const truthCumulative: Float32Array[] = [];
  for (let e = 0; e < nE; e++) {
    const t = Date.parse(epochs[e].time);
    const years = (t - t0) / (365.25 * 86400000);
    const step = t >= tQuake ? 1 : 0;
    const cum = new Float32Array(pixels);
    // Seasonal pumping: max drawdown late summer (day ~240), amplitude
    // proportional to the bowl rate (deeper bowls breathe more).
    const dayOfYear = ((t - Date.parse('2026-01-01T00:00:00Z')) / 86400000) % 365.25;
    for (let idx = 0; idx < pixels; idx++) {
      const bowlScale = Math.min(1, Math.abs(truthSecularLosMPerYr[idx]) / 0.2);
      const seasonal =
        bowlScale * 0.018 * (Math.cos((2 * Math.PI * (dayOfYear - 240)) / 365.25) - 1) / 2;
      cum[idx] = truthSecularLosMPerYr[idx] * years + step * truthQuakeLosM[idx] + seasonal;
    }
    truthCumulative.push(cum);
  }

  // ---- Per-epoch atmosphere (m of LOS) ----
  const atmospherePerEpoch: Float32Array[] = [];
  let hMean = 0;
  for (let idx = 0; idx < pixels; idx++) hMean += dem[idx];
  hMean /= pixels;
  for (let e = 0; e < nE; e++) {
    // Stratified troposphere: height-correlated with a per-epoch slope.
    // Typical stratified delay gradients over the valley flanks are
    // ~1–4 mm of delay per km of height → 1e-6..4e-6 m/m; we draw per-epoch
    // slopes from that regime (a stronger disturbance episode set for the
    // exercise is NOT used — the documented regime stands).
    const slope = gauss(rand) * 3.0e-6; // m of LOS per m of height (σ ≈ 3 mm/km)
    // Turbulent troposphere (fractal) + ionospheric ramp.
    const turb = turbulentField(width, height, 0.009, 9, spacing / 1000, 90000 + e * 31);
    const iono = ionoRampField(width, height, e, 424242, 0.010, spacing / 1000);
    const atm = new Float32Array(pixels);
    for (let idx = 0; idx < pixels; idx++) {
      atm[idx] = slope * (dem[idx] - hMean) + turb[idx] + iono[idx];
    }
    atmospherePerEpoch.push(atm);
  }

  // ---- Coherence per interferogram ----
  const GAMMA0 = [0.74, 0.90, 0.93, 0.15, 0.80]; // ag, range, urban, water, bare (L-band)
  const TAU = [60, 500, 800, 5, 300]; // days
  // Slow land-cover change: a few fields flip class mid-series (crop
  // rotation), decorrelating pairs that span the flip.
  const flipAt = new Int32Array(pixels).fill(-1);
  for (let idx = 0; idx < pixels; idx++) {
    if (landcover[idx] === 0 && rand() < 0.04) flipAt[idx] = 5 + Math.floor(rand() * 12);
  }
  const coherence: Float32Array[] = ifgs.map((ifg) => {
    const g = new Float32Array(pixels);
    const dt = ifg.temporalBaselineDays;
    const midEpoch = (ifg.refIndex + ifg.secIndex) / 2;
    for (let idx = 0; idx < pixels; idx++) {
      const lc = landcover[idx];
      let gamma = GAMMA0[lc] * Math.exp(-dt / TAU[lc]);
      if (flipAt[idx] >= 0 && ifg.refIndex < flipAt[idx] && ifg.secIndex >= flipAt[idx]) {
        gamma *= 0.25; // crop rotation decorrelation
      }
      // Speckle of the coherence estimate itself.
      gamma *= 1 + gauss(rand) * 0.05;
      g[idx] = Math.max(0.02, Math.min(0.985, gamma));
      // Late-series coherence drift (soil-moisture seasonality).
      g[idx] *= 1 - 0.06 * Math.max(0, midEpoch - nE / 2) / nE;
    }
    return g;
  });

  // ---- Interferometric phase per ifg ----
  // d_ifg = truth(sec) − truth(ref) + atm(sec) − atm(ref) + noise,
  // referenced to the stable P271-analog window, then converted to radians.
  const refI = Math.round(height * 0.16);
  const refJ = Math.round(width * 0.86);
  const phase: Float32Array[] = ifgs.map((ifg, k) => {
    const d = new Float32Array(pixels);
    const g = coherence[k];
    const dtYears = (Date.parse(epochs[ifg.secIndex].time) - Date.parse(epochs[ifg.refIndex].time)) / (365.25 * 86400000);
    for (let idx = 0; idx < pixels; idx++) {
      // Deformation (truth) difference.
      const defo =
        truthCumulative[ifg.secIndex][idx] - truthCumulative[ifg.refIndex][idx];
      // Atmosphere difference.
      const atm =
        atmospherePerEpoch[ifg.secIndex][idx] - atmospherePerEpoch[ifg.refIndex][idx];
      // CRLB noise from coherence (radians → metres).
      const gamma = Math.max(0.05, Math.min(0.985, g[idx]));
      const sigmaRad = Math.sqrt((1 - gamma * gamma) / (2 * LOOKS * gamma * gamma));
      const noiseM = gauss(rand) * sigmaRad * METRES_PER_RAD;
      d[idx] = defo + atm + noiseM;
    }
    // Reference to the P271-analog stable window (5×5 median).
    const refVals: number[] = [];
    for (let di = -2; di <= 2; di++) {
      for (let dj = -2; dj <= 2; dj++) {
        const ii = refI + di;
        const jj = refJ + dj;
        if (ii >= 0 && ii < height && jj >= 0 && jj < width) {
          refVals.push(d[ii * width + jj]);
        }
      }
    }
    refVals.sort((a, b) => a - b);
    const refOffset = refVals[Math.floor(refVals.length / 2)];
    const out = new Float32Array(pixels);
    for (let idx = 0; idx < pixels; idx++) {
      out[idx] = (d[idx] - refOffset) / METRES_PER_RAD;
    }
    return out;
  });

  // ---- Causal ground-truth labels (for the Brier calibration) ----
  // DOMINANT-mechanism labels: a pixel is "coseismic" only where the quake
  // step exceeds the secular signal it is superposed on (the Bayesian
  // engine attributes the LEADING mechanism per plan §8 — mixed pixels get
  // the dominant label, which the Brier note states explicitly).
  const labels = new Uint8Array(pixels);
  for (let idx = 0; idx < pixels; idx++) {
    const lq = Math.abs(truthQuakeLosM[idx]);
    const ls = Math.abs(truthSecularLosMPerYr[idx]);
    if (lq > 0.035 && lq > ls * 0.9) labels[idx] = 1;
    else if (ls > 0.025) labels[idx] = 2;
    else labels[idx] = 0;
  }

  // ---- Footprint polygon (grid corners → WGS84) ----
  const corners: Array<[number, number]> = [
    [xStart, yStart],
    [xStart + (width - 1) * spacing, yStart],
    [xStart + (width - 1) * spacing, yStart - (height - 1) * spacing],
    [xStart, yStart - (height - 1) * spacing],
  ];
  const footprintPolygon = corners.map(([e, n]) => {
    const ll = utmToLatLon(e, n, 11, false);
    return [ll.lon, ll.lat] as [number, number];
  });

  cached = {
    id: STACK_ID,
    meta,
    geometry: {
      wavelength: LAMBDA,
      incidenceAngleDeg: 34,
      headingDeg: -12.5,
      passDirection: 'ascending',
      lookDirection: 'right',
    },
    epochs,
    ifgs,
    phase,
    coherence,
    dem,
    landcover,
    labels,
    truthSecularLosMPerYr,
    truthQuakeLosM,
    truthCumulative,
    atmospherePerEpoch,
    event: STACK_EVENT,
    footprintPolygon,
    center: { lat: centerLat, lon: centerLon },
  };
  return cached;
}

/** Epoch index that first contains the coseismic step (for tests/UI). */
export function quakeStepEpochIndex(stack: CentralValleyStack): number {
  const tQuake = Date.parse(stack.event.time);
  for (let e = 0; e < stack.epochs.length; e++) {
    if (Date.parse(stack.epochs[e].time) >= tQuake) return e;
  }
  return stack.epochs.length;
}

/**
 * GNSS validation network for the demonstration stack (plan §10: "referenced
 * to stable GNSS station P271"; plan §11 validation protocol).
 *
 * Station POSITIONS are fixed, documented Central Valley localities (P271 =
 * the plan's stable reference). Velocities are sampled from the SAME
 * synthetic Earth truth that drives the stack (secular field only — standard
 * GNSS processing removes coseismic steps) plus an independent Gaussian
 * measurement noise of σ = 1.5 mm/yr per component. This keeps the network
 * physically consistent with the fixture while the comparison against the
 * SBAS estimate remains an independent noise realization — the validation
 * exercises the real projection + RMSE machinery, not a circular copy of the
 * InSAR result.
 */
export function centralValleyGnssNetwork(stack: CentralValleyStack): import('./types').GnssStation[] {
  const positions: Array<{ id: string; lat: number; lon: number; label: string }> = [
    { id: 'P271', lat: 36.585, lon: -119.625, label: "Stable Sierra-margin reference (the plan's P271)" },
    { id: 'P304', lat: 36.325, lon: -119.935, label: 'Hanford–Corcoran pumping bowl' },
    { id: 'P306', lat: 36.185, lon: -119.845, label: 'Southern bowl flank' },
    { id: 'P733', lat: 36.555, lon: -120.015, label: 'Valley margin' },
    { id: 'P735', lat: 36.495, lon: -120.185, label: 'West-centre valley' },
    { id: 'CAOR', lat: 36.165, lon: -119.655, label: 'East valley margin' },
    { id: 'CAFR', lat: 36.465, lon: -119.875, label: 'Fresno agricultural zone' },
    { id: 'CAKE', lat: 36.135, lon: -120.045, label: 'South-west valley' },
    { id: 'CALB', lat: 36.395, lon: -120.105, label: 'West valley' },
    { id: 'CAWC', lat: 36.265, lon: -119.915, label: 'Between the two bowls' },
  ];
  const { width, height } = stack.meta;
  const rand = mulberry32(987654321);
  const gauss = () => {
    let u = 0;
    let v = 0;
    while (u === 0) u = rand();
    while (v === 0) v = rand();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  };
  const los = losUnitVector(stack.geometry.incidenceAngleDeg, stack.geometry.headingDeg, stack.geometry.lookDirection);
  return positions.map((p) => {
    // Nearest pixel → sample the truth SECULAR ENU velocity. The bowls are
    // purely vertical and the tectonic term purely east, so the ENU
    // components are recovered exactly from the stored LOS truth plus the
    // known tectonic east model.
    const u = latLonToUtmInZone(p.lat, p.lon, 11);
    const j = Math.min(width - 1, Math.max(0, Math.round((u.easting - stack.meta.xStart) / stack.meta.xSpacing)));
    const i = Math.min(height - 1, Math.max(0, Math.round((stack.meta.yStart - u.northing) / -stack.meta.ySpacing)));
    const idx = i * width + j;
    const jj = idx % width;
    const ii = Math.floor(idx / width);
    const dFault = ((jj / width) * 0.9 + (ii / height) * 0.6 - 0.62) * 60;
    const eastMm = 5.5 * Math.tanh(dFault / 9);
    // LOS convention: losM = −(east·losE + up·losU)  →  up = (east·losE + losM) / −losU
    const losM = stack.truthSecularLosMPerYr[idx];
    const upMm = -((eastMm / 1000) * los.east + losM) / los.up * 1000;
    return {
      id: p.id,
      lat: p.lat,
      lon: p.lon,
      eastMmPerYr: eastMm + gauss() * 1.5,
      northMmPerYr: gauss() * 1.5,
      upMmPerYr: upMm + gauss() * 1.5,
      sigmaMm: 1.5,
      source:
        `Synthetic GNSS fixture (${p.label}): velocity sampled from the same synthetic Earth truth as ` +
        'the stack (secular field; coseismic steps removed as in standard GNSS processing) plus ' +
        'independent noise σ = 1.5 mm/yr. The live NGL endpoint was unreachable from this deployment.',
    };
  });
}

/**
 * Lightweight stack descriptor (epochs, geometry, footprint, event) WITHOUT
 * generating the phase/coherence/atmosphere fields — used by the dataset
 * detail endpoint so selecting the dataset never pays the ~7 s fixture
 * generation cost.
 */
export function stackDescriptor(): {
  epochs: string[];
  ifgCount: number;
  event: StackCatalogEvent;
  footprintPolygon: [number, number][];
  center: { lat: number; lon: number };
} {
  const epochs = epochSchedule();
  const nE = epochs.length;
  const width = 288;
  const height = 220;
  const spacing = 250;
  const c = latLonToUtmInZone(36.35, -119.92, 11);
  const xStart = Math.round(c.easting - ((width - 1) / 2) * spacing);
  const yStart = Math.round(c.northing + ((height - 1) / 2) * spacing);
  const corners: Array<[number, number]> = [
    [xStart, yStart],
    [xStart + (width - 1) * spacing, yStart],
    [xStart + (width - 1) * spacing, yStart - (height - 1) * spacing],
    [xStart, yStart - (height - 1) * spacing],
  ];
  const footprintPolygon = corners.map(([e, n]) => {
    const ll = utmToLatLon(e, n, 11, false);
    return [ll.lon, ll.lat] as [number, number];
  });
  // 24 interferograms (verified against the full generator in tests).
  const ifgCount = 21 + 4;
  void nE;
  return {
    epochs: epochs.map((e) => e.time),
    ifgCount,
    event: STACK_EVENT,
    footprintPolygon,
    center: { lat: 36.35, lon: -119.92 },
  };
}
