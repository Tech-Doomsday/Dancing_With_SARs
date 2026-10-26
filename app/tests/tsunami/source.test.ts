import { describe, expect, test } from 'bun:test';
import {
  parseFfmGeojson,
  uniformFaultFromScaling,
  computeDeformation,
  kajiuraFilter,
  buildSourceField,
  DEFAULT_SOURCE_OPTIONS,
  type Subfault,
} from '@/lib/tsunami/source';
import { geoFaultDisplacementENUTopEdge } from '@/lib/science/okada';
import { momentFromMw, slipFromMoment, ruptureAreaFromMw } from '@/lib/science/okada';
import type { BathymetryGrid } from '@/lib/tsunami/types';
import { fftInPlace } from '@/lib/tsunami/fft';

/**
 * Source-model verification. SYNTHETIC FIXTURES ONLY (clearly labelled) —
 * they validate the math and the wiring, never the geophysics of any real
 * event. Real finite-fault models are exercised by the live hindcast.
 */

function testGrid(nx: number, ny: number, bedFn: (i: number, j: number) => number, resArcmin = 2): BathymetryGrid {
  const bed = new Float32Array(nx * ny);
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) bed[j * nx + i] = bedFn(i, j);
  }
  return {
    resArcmin,
    lat0: -10,
    lon0: -80,
    nx,
    ny,
    bed,
    provenance: {
      datasetId: 'test',
      provider: 'synthetic fixture',
      productId: 'test',
      productType: 'test',
      sourceUrl: 'none',
      retrievedAt: new Date().toISOString(),
      observationStart: 'n/a',
      observationEnd: 'n/a',
      units: 'm',
      crs: 'EPSG:4326',
      verticalDatum: 'n/a',
      processing: [],
      dataClass: 'modelled',
      qualityNotes: ['SYNTHETIC FIXTURE'],
    },
  };
}

describe('FFM.geojson parser (synthetic fixture shaped like the real product)', () => {
  // Two hand-computable subfaults. Corner order per the verified product:
  // [0]-[1] top edge (shallow), [3]-[2] bottom edge, closed with [4]=[0].
  const km = 1000;
  const faultA = {
    geometry: {
      coordinates: [[
        [-71.0, -35.0, 10 * km],
        [-70.0, -35.0, 10 * km],      // top edge 111 km due east
        [-70.0, -35.2, 30 * km],      // bottom edge south + 20 km deeper
        [-71.0, -35.2, 30 * km],
        [-71.0, -35.0, 10 * km],
      ]],
    },
    properties: { slip: 5, rake: 90, sf_moment: 1e20, rise: 10, trup: 20 },
  };
  const faultB = {
    geometry: {
      coordinates: [[
        [-70.5, -34.0, 15 * km],
        [-70.5, -34.9, 15 * km],      // top edge due south
        [-70.6, -34.9, 25 * km],
        [-70.6, -34.0, 25 * km],
        [-70.5, -34.0, 15 * km],
      ]],
    },
    properties: { slip: 2, rake: 45, sf_moment: 5e19 },
  };
  const parsed = parseFfmGeojson({ features: [faultA, faultB] as never[] });

  test('parses both subfaults', () => {
    expect(parsed).toHaveLength(2);
  });

  test('fault A: strike ≈ 90° (east), dip ≈ atan(20/22.2) ≈ 42°, top depth 10 km, width ≈ 30 km', () => {
    const a = parsed[0];
    expect(a.strikeDeg).toBeGreaterThan(80);
    expect(a.strikeDeg).toBeLessThan(100);
    // horizontal downdip extent: 0.2° lat ≈ 22.2 km; depth contrast 20 km
    const expectedDip = (Math.atan2(20 * km, 0.2 * 111_000) * 180) / Math.PI;
    expect(Math.abs(a.dipDeg - expectedDip)).toBeLessThan(1);
    expect(a.topDepthM / km).toBeCloseTo(10, 5);
    expect(a.widthM / km).toBeCloseTo(Math.hypot(20, 22.2), 0); // ≈ 29.9 km
    expect(a.lengthM / km).toBeCloseTo(111 * Math.cos((35 * Math.PI) / 180), 0); // 1° lon at 35°S ≈ 91 km
    expect(a.slipM).toBe(5);
    expect(a.rakeDeg).toBe(90);
    expect(a.momentNM).toBe(1e20);
  });

  test('fault B: strike ≈ 180° (south), moment carried', () => {
    const b = parsed[1];
    expect(b.strikeDeg).toBeGreaterThan(170);
    expect(b.strikeDeg).toBeLessThan(190);
    expect(b.momentNM).toBe(5e19);
  });

  test('drops features without slip/rake', () => {
    const bad = { geometry: { coordinates: [[[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 0]]] }, properties: {} };
    expect(parseFfmGeojson({ features: [bad] as never[] })).toHaveLength(0);
  });
});

describe('uniform-plane fallback (scaling laws)', () => {
  test('WC94 area for M 8.8 ≈ 3.1e4 km² and M0 = 10^(1.5·Mw+9.1)', () => {
    const areaKm2 = ruptureAreaFromMw(8.8) / 1e6;
    expect(areaKm2).toBeGreaterThan(2e4);
    expect(areaKm2).toBeLessThan(5e4);
    expect(Math.abs(momentFromMw(9.0) / 3.981e22 - 1)).toBeLessThan(0.02); // Mw 9.0 ↔ M0 = 10^22.6
  });

  test('uniformFaultFromScaling: slip = M0/(μA), plane area matches scaling', () => {
    const { fault, notes } = uniformFaultFromScaling({
      magnitude: 7.7,
      depthKm: 12.6,
      lat: 7.5,
      lon: -80.7,
      nodalPlane: { strike: 25, dip: 75, rake: -155 },
    });
    const m0 = momentFromMw(7.7);
    expect(fault.slipM).toBeCloseTo(slipFromMoment(m0, fault.lengthM * fault.widthM), 5);
    expect(Math.abs(fault.lengthM * fault.widthM - ruptureAreaFromMw(7.7)) / ruptureAreaFromMw(7.7)).toBeLessThan(0.02);
    expect(notes.length).toBeGreaterThanOrEqual(3);
    expect(fault.topDepthM).toBeGreaterThanOrEqual(0);
  });
});

describe('Tanioka–Seno slope-correction wiring', () => {
  // Planar bed sloping up to the east: b = s·x, s = 0.1 (a steep trench wall).
  // A pure strike-slip fault (rake 0) on this slope must produce
  // dz_with − dz_without = u_x · s (+ u_y · ~0) at every cell.
  const nx = 90;
  const ny = 60;
  const s = 0.1;
  const grid = testGrid(nx, ny, (i) => -2000 + s * i * 2 * 1852, 2);
  const sf: Subfault = {
    clat: -10 + (30 * 2) / 60,
    clon: -80 + (45 * 2) / 60,
    topDepthM: 5000,
    strikeDeg: 0,
    dipDeg: 90,
    rakeDeg: 0, // pure strike-slip
    slipM: 4,
    lengthM: 60_000,
    widthM: 40_000,
    momentNM: 0,
  };

  test('dz(with) − dz(without) equals u_h·∇b using the same Okada displacement', () => {
    const withCorr = computeDeformation(grid, [sf], { slopeCorrection: true, kajiuraFilter: false });
    const without = computeDeformation(grid, [sf], { slopeCorrection: false, kajiuraFilter: false });
    // check a spread of cells
    let worst = 0;
    let n = 0;
    for (let j = 10; j < ny - 10; j += 7) {
      for (let i = 10; i < nx - 10; i += 7) {
        const k = j * nx + i;
        const diff = withCorr.dz[k] - without.dz[k];
        // independent Okada evaluation at this cell
        const lat = grid.lat0 + (j * grid.resArcmin) / 60;
        const lon = grid.lon0 + (i * grid.resArcmin) / 60;
        const R = 6371.0088e3;
        const meanLat = ((sf.clat + lat) / 2) * (Math.PI / 180);
        const e = ((lon - sf.clon) * Math.PI) / 180 * R * Math.cos(meanLat);
        const nOff = ((lat - sf.clat) * Math.PI) / 180 * R;
        const disp = geoFaultDisplacementENUTopEdge(
          {
            strikeDeg: sf.strikeDeg,
            dipDeg: sf.dipDeg,
            rakeDeg: sf.rakeDeg,
            lengthM: sf.lengthM,
            widthM: sf.widthM,
            topDepthM: sf.topDepthM,
            slipM: sf.slipM,
            centroidLat: sf.clat,
            centroidLon: sf.clon,
          },
          sf.topDepthM,
          e + (sf.widthM / 2) * Math.cos((sf.dipDeg * Math.PI) / 180) * Math.cos(0),
          nOff + (sf.widthM / 2) * Math.cos((sf.dipDeg * Math.PI) / 180) * -Math.sin(0),
        );
        // gradient of the planar bed: b = -2000 + s·(cells east × 2' × 1852)
        // db/dx = s (east), db/dy = 0. The code computes smoothed gradients of
        // the SAME bed, so the expected correction is disp.east·s.
        const expected = disp.east * s;
        const err = Math.abs(diff - expected);
        if (Math.abs(expected) > 0.01) {
          // only check cells with a non-negligible correction
          worst = Math.max(worst, err);
          n++;
        }
      }
    }
    expect(n).toBeGreaterThan(5);
    expect(worst).toBeLessThan(0.005); // mm-level agreement in wiring
  });
});

describe('Kajiura (1963) water-column transfer', () => {
  const nx = 256;
  const ny = 8;
  const depth = 4000;
  const grid = testGrid(nx, ny, () => -depth, 2);

  test('a pure Fourier mode is attenuated by 1/cosh(k·d)', () => {
    const kx = (2 * Math.PI * 2) / (nx * 2 * 1852); // mode 2 over the padded row
    const expected = 1 / Math.cosh(kx * depth);
    const dz = new Float32Array(nx * ny);
    for (let j = 0; j < ny; j++) {
      for (let i = 0; i < nx; i++) {
        dz[j * nx + i] = Math.cos((2 * Math.PI * 2 * i) / nx);
      }
    }
    const out = kajiuraFilter(grid, dz);
    // measure amplitude at a row: the mode is uniform along y so the column
    // pass sees a constant → unchanged there.
    let amp = 0;
    for (let i = 0; i < nx; i++) {
      amp = Math.max(amp, Math.abs(out[4 * nx + i] - out[4 * nx + 0]));
    }
    const ratio = amp / 2; // input peak-to-peak 2 → amplitude 1
    expect(Math.abs(ratio - expected)).toBeLessThan(0.02 * expected + 1e-3);
  });

  test('long waves pass unchanged (k·d ≪ 1 → ratio ≈ 1 within 2%)', () => {
    const grid2 = testGrid(128, 8, () => -4000, 4); // 4' cells → very long waves
    const dz = new Float32Array(128 * 8);
    for (let j = 0; j < 8; j++) {
      for (let i = 0; i < 128; i++) {
        dz[j * 128 + i] = Math.cos((2 * Math.PI * i) / 128); // mode 1
      }
    }
    const kx = (2 * Math.PI * 1) / (256 * 4 * 1852);
    const expected = 1 / Math.cosh(kx * 4000);
    expect(expected).toBeGreaterThan(0.98);
    const out = kajiuraFilter(grid2, dz);
    let amp = 0;
    for (let i = 0; i < 128; i++) amp = Math.max(amp, Math.abs(out[4 * 128 + i] - out[4 * 128 + 0]));
    expect(amp / 2).toBeGreaterThan(0.96);
  });
});

describe('FFT correctness (used by the Kajiura filter)', () => {
  test('round-trip recovers the input; Parseval holds', () => {
    const n = 128;
    const re = new Float64Array(n);
    const im = new Float64Array(n);
    let energyTime = 0;
    for (let i = 0; i < n; i++) {
      re[i] = Math.sin(0.05 * i) + 0.3 * Math.cos(0.21 * i);
      energyTime += re[i] * re[i];
    }
    const re0 = Float64Array.from(re);
    fftInPlace(re, im, false);
    let energyFreq = 0;
    for (let i = 0; i < n; i++) energyFreq += re[i] * re[i] + im[i] * im[i];
    expect(energyFreq / n).toBeCloseTo(energyTime, 6);
    fftInPlace(re, im, true);
    for (let i = 0; i < n; i++) expect(re[i]).toBeCloseTo(re0[i], 10);
  });

  test('rejects non-power-of-two lengths', () => {
    expect(() => fftInPlace(new Float64Array(100), new Float64Array(100))).toThrow();
  });
});

describe('buildSourceField integration (synthetic)', () => {
  test('subsided surface below the bed starts dry (η clamped to b)', () => {
    const grid = testGrid(40, 30, (i, j) => (j > 20 ? 50 : -1000), 2);
    const sf: Subfault = {
      clat: -10 + (10 * 2) / 60,
      clon: -80 + (20 * 2) / 60,
      topDepthM: 2000,
      strikeDeg: 0,
      dipDeg: 45,
      rakeDeg: -90, // normal: subsidence over the hanging wall
      slipM: 10,
      lengthM: 30_000,
      widthM: 20_000,
      momentNM: 0,
    };
    const field = buildSourceField(grid, [sf], 'manual', DEFAULT_SOURCE_OPTIONS, 'synthetic test', null);
    let clamped = 0;
    for (let k = 0; k < field.eta0.length; k++) {
      if (field.eta0[k] < grid.bed[k]) clamped++;
    }
    expect(clamped).toBe(0); // invariant: η ≥ b everywhere after construction
    expect(field.notes.length).toBeGreaterThan(0);
  });
});
