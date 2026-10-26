/**
 * Synthetic validation fixtures.
 *
 * These fixtures are SYNTHETIC — clearly labelled as such everywhere they
 * appear (dataset.provenance.dataClass = 'synthetic', UI badges). They exist
 * to validate the processing pipeline end-to-end against analytic ground
 * truth (Okada 1985 elastic dislocation + a phase-noise model consistent
 * with the coherence-based uncertainty formula). They are NOT presented as
 * observations of the Earth.
 *
 * Generation chain (forward physics, then observation simulation):
 *   1. Surface displacement from an Okada85 fault (ground truth).
 *   2. Projection to LOS with an explicit radar geometry.
 *   3. Conversion to interferometric phase (inverse of the NISAR convention).
 *   4. Addition of Gaussian phase noise whose sigma equals the
 *      coherence-based estimate (Rodriguez & Martin 1992), with a
 *      spatially varying coherence map (patchy decorrelation).
 *   5. Addition of a smooth residual "atmospheric" ramp.
 *   6. Wrapping to (-pi, pi] and re-unwrapping by the pipeline would be
 *      trivial for such small signals; instead the phase is left unwrapped
 *      with noise, matching the GUNW product level.
 *
 * Deterministic: seeded PRNG (mulberry32) — identical output every run.
 */

import type { Raster, RadarGeometry, ScienceDataset } from './types';
import { displacementToPhase } from './phase';
import { losUnitVector, projectDisplacementToLOS } from './los-projection';
import { geoFaultDisplacementENU, type GeoFault } from './okada';
import { phaseSigmaRad } from './uncertainty';
import { utmZone, latLonToUtm, utmZoneToEpsg } from './utm';

/** Deterministic PRNG (mulberry32). */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Box-Muller Gaussian from a uniform generator. */
function gauss(rng: () => number): number {
  let u = 0;
  let v = 0;
  while (u === 0) u = rng();
  while (v === 0) v = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

const SYNTHETIC_GEOMETRY: RadarGeometry = {
  wavelength: 0.2360571,
  incidenceAngleDeg: 34,
  headingDeg: -12.5,
  passDirection: 'ascending',
  lookDirection: 'right',
};

function makeGrid(
  centerLat: number,
  centerLon: number,
  widthKm: number,
  heightKm: number,
  postingM: number,
): { meta: Raster['meta']; centreE: number; centreN: number } {
  const utm = latLonToUtm(centerLat, centerLon);
  const epsg = utmZoneToEpsg(utmZone(centerLon), centerLat < 0 ? 'S' : 'N');
  const width = Math.round((widthKm * 1000) / postingM);
  const height = Math.round((heightKm * 1000) / postingM);
  // Centre the grid on the UTM position.
  const xStart = utm.easting - (width - 1) * (postingM / 2);
  const yStart = utm.northing + (height - 1) * (postingM / 2);
  return {
    meta: { width, height, xStart, xSpacing: postingM, yStart, ySpacing: -postingM, epsg },
    centreE: utm.easting,
    centreN: utm.northing,
  };
}

function footprintFromMeta(meta: Raster['meta'], centreLat: number, centreLon: number): [number, number][] {
  // Equirectangular back-projection around the dataset centre — accurate to
  // well under a pixel at these extents and used for display only.
  const xs = [meta.xStart, meta.xStart + (meta.width - 1) * meta.xSpacing];
  const ys = [meta.yStart, meta.yStart + (meta.height - 1) * meta.ySpacing];
  const utm = latLonToUtm(centreLat, centreLon);
  const latRad = (centreLat * Math.PI) / 180;
  const mPerDegLat = 111132.92;
  const mPerDegLon = 111320 * Math.cos(latRad) - 559.82 * Math.cos(3 * latRad);
  const pts: [number, number][] = [];
  for (const y of [ys[0], ys[1]]) {
    for (const x of [xs[0], xs[1]]) {
      pts.push([
        centreLon + (x - utm.easting) / mPerDegLon,
        centreLat + (y - utm.northing) / mPerDegLat,
      ]);
    }
  }
  // Order: NW, NE, SE, SW
  return [pts[2], pts[3], pts[1], pts[0]];
}

export interface FixtureSpec {
  seed: number;
  centerLat: number;
  centerLon: number;
  widthKm: number;
  heightKm: number;
  postingM: number;
  fault: GeoFault;
  centroidDepthM: number;
  /** Coherence level in coherent areas */
  coherenceBase: number;
  /** Fraction of scene affected by decorrelation patches */
  decorrelatedFraction: number;
  /** Amplitude of the residual atmospheric ramp (m of LOS) */
  atmosphereRampM: number;
  title: string;
  slug: string;
  blurb: string;
}

/**
 * Build a synthetic earthquake fixture. Returns a quantitative dataset with
 * truth, phase and coherence rasters.
 */
export function makeSyntheticDataset(spec: FixtureSpec): ScienceDataset {
  const rng = mulberry32(spec.seed);
  const { meta, centreE, centreN } = makeGrid(
    spec.centerLat, spec.centerLon, spec.widthKm, spec.heightKm, spec.postingM,
  );
  const n = meta.width * meta.height;
  const truth = new Float32Array(n);
  const los = losUnitVector(
    SYNTHETIC_GEOMETRY.incidenceAngleDeg,
    SYNTHETIC_GEOMETRY.headingDeg,
    SYNTHETIC_GEOMETRY.lookDirection,
  );

  // Decorrelation patches: centres and radii (in grid index units).
  const patches: Array<{ ci: number; cj: number; r: number }> = [];
  const patchCount = Math.max(1, Math.round(spec.decorrelatedFraction * 12));
  for (let p = 0; p < patchCount; p++) {
    patches.push({
      ci: rng() * meta.height,
      cj: rng() * meta.width,
      r: 4 + rng() * Math.max(8, Math.min(meta.width, meta.height) * 0.08),
    });
  }

  const coherence = new Float32Array(n);
  const phase = new Float32Array(n);
  const lambda = SYNTHETIC_GEOMETRY.wavelength;
  const looks = 119; // same multilooking as the NISAR sample product

  // Smooth atmospheric ramp: bilinear gradient across the scene.
  const rampA = (rng() - 0.5) * 2;
  const rampB = (rng() - 0.5) * 2;

  for (let idx = 0; idx < n; idx++) {
    const i = Math.floor(idx / meta.width);
    const j = idx % meta.width;
    const x = meta.xStart + j * meta.xSpacing - centreE;
    const y = meta.yStart + i * meta.ySpacing - centreN;

    // 1. Ground truth displacement -> LOS.
    const d = geoFaultDisplacementENU(spec.fault, spec.centroidDepthM, x, y);
    const losM = d.singular ? 0 : projectDisplacementToLOS(d, los);
    truth[idx] = losM;

    // 2. Coherence: base with decorrelated patches + mild spatial noise.
    let coh = spec.coherenceBase + (rng() - 0.5) * 0.06;
    for (const p of patches) {
      const dist = Math.hypot(i - p.ci, j - p.cj);
      if (dist < p.r) coh = Math.min(coh, 0.12 + 0.25 * (dist / p.r));
    }
    coh = Math.max(0.02, Math.min(0.985, coh));
    coherence[idx] = coh;

    // 3. Phase = signal + coherence-consistent noise + atmospheric ramp.
    const atm =
      spec.atmosphereRampM *
      (rampA * (j / meta.width - 0.5) + rampB * (i / meta.height - 0.5)) *
      2;
    const sigmaPhi = phaseSigmaRad(coh, looks);
    const noisyLos = losM + atm + (lambda / (4 * Math.PI)) * gauss(rng) * sigmaPhi;
    phase[idx] = displacementToPhase(noisyLos, lambda);
  }

  // Connected components: single component (valid), 0 = invalid.
  const components = new Uint16Array(n); // all 1 in valid area
  for (let idx = 0; idx < n; idx++) if (coherence[idx] >= 0.5) components[idx] = 1;

  return {
    id: `synthetic-${spec.slug}`,
    slug: spec.slug,
    title: spec.title,
    blurb: spec.blurb,
    mode: 'quantitative',
    isSynthetic: true,
    provenance: {
      datasetId: `synthetic-${spec.slug}`,
      provider: 'MOTUS synthetic fixture (in-process)',
      productId: `SYNTH-OKADA-${spec.slug}-${spec.seed}`,
      productType: 'Synthetic unwrapped interferogram (Okada 1985 forward model)',
      platform: 'Analytic elastic half-space — no spacecraft',
      referenceTime: '2026-01-01T00:00:00Z',
      secondaryTime: '2026-01-13T00:00:00Z',
      temporalBaselineDays: 12,
      dataClass: 'synthetic',
      sourceUrl: 'https://github.com/okada-benchmarks (see docs/science.md)',
      accessNotes:
        'Synthetic validation fixture generated deterministically in-process (seeded). ' +
        'Everywhere labelled synthetic; must never be presented as an Earth observation.',
      qualityNotes: [
        `seed=${spec.seed}`,
        `coherence base ${spec.coherenceBase}`,
        `atmospheric ramp amplitude ±${(spec.atmosphereRampM * 1000).toFixed(1)} mm`,
      ],
    },
    geometry: SYNTHETIC_GEOMETRY,
    phase: { meta, data: phase },
    coherence: { meta, data: coherence },
    components,
    numberOfLooks: looks,
    footprintPolygon: footprintFromMeta(meta, spec.centerLat, spec.centerLon),
    center: { lat: spec.centerLat, lon: spec.centerLon },
    truth: { meta, data: truth },
  };
}

/** Classic Parkfield-like right-lateral strike-slip fixture. */
export function syntheticStrikeSlipFixture(): ScienceDataset {
  const fault: GeoFault = {
    strikeDeg: 137,
    dipDeg: 85,
    rakeDeg: -180, // dextral
    lengthM: 16000,
    widthM: 10000,
    topDepthM: 1000,
    slipM: 0.85,
    centroidLat: 35.9,
    centroidLon: -120.5,
  };
  return makeSyntheticDataset({
    seed: 20261009,
    centerLat: 35.9,
    centerLon: -120.5,
    widthKm: 64,
    heightKm: 64,
    postingM: 250,
    fault,
    centroidDepthM: 6000,
    coherenceBase: 0.8,
    decorrelatedFraction: 0.25,
    atmosphereRampM: 0.004,
    title: 'Synthetic validation — dextral strike-slip earthquake',
    slug: 'strikeslip',
    blurb:
      'Analytic Okada (1985) Mw≈6.5 dextral strike-slip rupture observed with noise and decorrelation. ' +
      'Used to validate the measurement pipeline against known ground truth.',
  });
}

/** Reverse-slip (thrust) fixture. */
export function syntheticThrustFixture(): ScienceDataset {
  const fault: GeoFault = {
    strikeDeg: 250,
    dipDeg: 35,
    rakeDeg: 95,
    lengthM: 22000,
    widthM: 14000,
    topDepthM: 2000,
    slipM: 1.1,
    centroidLat: 35.15,
    centroidLon: -119.1,
  };
  return makeSyntheticDataset({
    seed: 88001,
    centerLat: 35.15,
    centerLon: -119.1,
    widthKm: 72,
    heightKm: 72,
    postingM: 280,
    fault,
    centroidDepthM: 9000,
    coherenceBase: 0.75,
    decorrelatedFraction: 0.35,
    atmosphereRampM: 0.006,
    title: 'Synthetic validation — reverse (thrust) earthquake',
    slug: 'thrust',
    blurb:
      'Analytic Okada (1985) Mw≈6.8 thrust rupture with stronger decorrelation. ' +
      'Tests sign conventions and detection on a lobate vertical/horizontal pattern.',
  });
}

/** Null fixture: no deformation, noise only — false-alarm control. */
export function syntheticNullFixture(): ScienceDataset {
  const fault: GeoFault = {
    strikeDeg: 0,
    dipDeg: 90,
    rakeDeg: 0,
    lengthM: 1,
    widthM: 1,
    topDepthM: 30000,
    slipM: 0,
    centroidLat: 35.9,
    centroidLon: -120.5,
  };
  return makeSyntheticDataset({
    seed: 424242,
    centerLat: 35.9,
    centerLon: -120.5,
    widthKm: 64,
    heightKm: 64,
    postingM: 250,
    fault,
    centroidDepthM: 30000,
    coherenceBase: 0.85,
    decorrelatedFraction: 0.15,
    atmosphereRampM: 0.003,
    title: 'Synthetic validation — null (no deformation)',
    slug: 'null',
    blurb:
      'Zero ground truth displacement with realistic noise. The pipeline should report ' +
      'no significant change: this fixture controls the false-alarm behaviour.',
  });
}
