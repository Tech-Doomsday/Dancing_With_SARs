import { describe, expect, test } from 'bun:test';
import {
  phaseToDisplacement,
  displacementToPhase,
  wrapPhase,
  fringeDisplacement,
  unwrapErrorDisplacement,
  phaseGridToDisplacement,
} from '@/lib/science/phase';
import { losUnitVector, projectDisplacementToLOS, compareWithNisarProductVector } from '@/lib/science/los-projection';
import { phaseSigmaRad, displacementSigmaM, combinedSigmaM, detectionThresholdM } from '@/lib/science/uncertainty';
import { median, madSigma, mean, std, quantile, planarFit, pearson } from '@/lib/science/statistics';

const LAMBDA = 0.2360571; // NISAR L band

describe('phase <-> displacement conversion (NISAR SDS convention)', () => {
  test('one fringe = half a wavelength of range change', () => {
    expect(fringeDisplacement(LAMBDA)).toBeCloseTo(LAMBDA / 2, 12);
  });

  test('phaseToDisplacement: +2pi phase = +lambda/2 range increase (away)', () => {
    expect(phaseToDisplacement(2 * Math.PI, LAMBDA)).toBeCloseTo(LAMBDA / 2, 12);
  });

  test('round trip phase -> displacement -> phase', () => {
    for (const p of [0.1, -1.7, 12.34, -100.5]) {
      expect(displacementToPhase(phaseToDisplacement(p, LAMBDA), LAMBDA)).toBeCloseTo(p, 10);
    }
  });

  test('wrapPhase maps into (-pi, pi]', () => {
    expect(wrapPhase(0)).toBe(0);
    expect(wrapPhase(Math.PI)).toBe(Math.PI);
    expect(wrapPhase(-Math.PI)).toBe(Math.PI);
    expect(wrapPhase(3 * Math.PI)).toBeCloseTo(Math.PI, 12);
    // -7pi + 0.5 == pi + 0.5 (mod 2pi) which wraps to -(pi - 0.5).
    expect(wrapPhase(-7 * Math.PI + 0.5)).toBeCloseTo(-(Math.PI - 0.5), 12);
    expect(wrapPhase(2 * Math.PI - 1)).toBeCloseTo(-1, 12);
  });

  test('k-cycle unwrapping error equals k * lambda/2 metres', () => {
    expect(unwrapErrorDisplacement(1, LAMBDA)).toBeCloseTo(0.118, 3);
    expect(unwrapErrorDisplacement(-2, LAMBDA)).toBeCloseTo(-LAMBDA, 12);
  });

  test('grid conversion propagates NaN and scales correctly', () => {
    const grid = new Float32Array([0, Math.PI, NaN, -2]);
    const out = phaseGridToDisplacement(grid, LAMBDA);
    // Float32 storage limits precision to ~1e-7 relative.
    expect(out[1]).toBeCloseTo(LAMBDA / 4, 6);
    expect(Number.isNaN(out[2])).toBe(true);
    expect(out[3]).toBeCloseTo(phaseToDisplacement(-2, LAMBDA), 6);
  });
});

describe('LOS unit-vector geometry', () => {
  test('vector is a unit vector', () => {
    for (const inc of [20, 34, 45]) {
      for (const hd of [-12.5, -167.5, 90]) {
        const v = losUnitVector(inc, hd);
        expect(Math.hypot(v.east, v.north, v.up)).toBeCloseTo(1, 12);
      }
    }
  });

  test('reconstruction matches the NISAR sample product vector (satellite->ground stored)', () => {
    // Sample GUNW granule radarGrid means: losUnitVectorX = -0.4005,
    // losUnitVectorY = -0.0837, incidence 24.17 deg, ascending right-looking,
    // heading -11.80 deg (derived from the same vectors).
    const diff = compareWithNisarProductVector(24.17, -11.8, -0.4005, -0.0837);
    expect(diff).toBeLessThan(0.02);
  });

  test('projection sign: motion toward the satellite decreases range (negative d)', () => {
    const los = losUnitVector(34, -12.5); // ascending, looks ENE
    const d = { east: los.east, north: los.north, up: los.up }; // motion toward satellite
    expect(projectDisplacementToLOS(d, los)).toBeLessThan(0);
    expect(projectDisplacementToLOS({ east: -los.east, north: -los.north, up: -los.up }, los)).toBeGreaterThan(0);
  });

  test('pure vertical motion projects with cos(incidence)', () => {
    const inc = 34;
    const los = losUnitVector(inc, -12.5);
    // Uplift (up = +1) moves the ground toward the satellite -> range decreases -> d < 0.
    const d = projectDisplacementToLOS({ east: 0, north: 0, up: 1 }, los);
    expect(d).toBeCloseTo(-Math.cos((inc * Math.PI) / 180), 12);
  });
});

describe('coherence-based uncertainty', () => {
  test('sigma_phi decreases with coherence and with look count', () => {
    expect(phaseSigmaRad(0.9, 119)).toBeLessThan(phaseSigmaRad(0.5, 119));
    expect(phaseSigmaRad(0.9, 200)).toBeLessThan(phaseSigmaRad(0.9, 119));
  });

  test('sigma_phi -> 0 as gamma -> 1; NaN at gamma -> 0', () => {
    expect(phaseSigmaRad(1, 119)).toBe(0);
    expect(Number.isNaN(phaseSigmaRad(0, 119))).toBe(true);
  });

  test('known value: gamma=0.5, L=119 -> sigma_phi ~ 0.1124 rad', () => {
    // sqrt((1-0.25)/(2*119*0.25)) = sqrt(0.75/59.5)
    expect(phaseSigmaRad(0.5, 119)).toBeCloseTo(Math.sqrt(0.75 / 59.5), 12);
  });

  test('displacement sigma = lambda * sigma_phi / (4 pi)', () => {
    const sp = phaseSigmaRad(0.7, 119);
    expect(displacementSigmaM(0.7, 119, LAMBDA)).toBeCloseTo((LAMBDA * sp) / (4 * Math.PI), 12);
  });

  test('combined sigma adds in quadrature', () => {
    const s = combinedSigmaM(0.8, 119, LAMBDA, 0.005, 1.0);
    const pix = displacementSigmaM(0.8, 119, LAMBDA);
    const iono = (LAMBDA * 1.0) / (4 * Math.PI);
    expect(s).toBeCloseTo(Math.sqrt(pix * pix + 0.005 * 0.005 + iono * iono), 12);
  });

  test('detection threshold is max(k*sigma, floor)', () => {
    expect(detectionThresholdM(0.02, 3, 0.01)).toBe(0.06);
    expect(detectionThresholdM(0.002, 3, 0.01)).toBe(0.01);
  });
});

describe('robust statistics', () => {
  test('median ignores NaN and handles even/odd lengths', () => {
    expect(median([3, 1, 2])).toBe(2);
    expect(median([4, 1, 3, 2])).toBe(2.5);
    expect(median([NaN, 1, 3])).toBe(2);
    expect(Number.isNaN(median([NaN]))).toBe(true);
  });

  test('MAD sigma of a standard normal sample is ~1', () => {
    // Deterministic pseudo-normal sample via Box-Muller with fixed values.
    const vals = [0.125, -0.537, 1.023, -1.214, 0.692, -0.204, 0.855, -0.661, 1.402, -0.981,
      0.318, 0.077, -1.107, 0.541, -0.322, 1.18, -0.748, 0.229, 0.936, -0.415];
    const s = madSigma(vals);
    expect(s).toBeGreaterThan(0.5);
    expect(s).toBeLessThan(1.6);
  });

  test('mean and std of a known set', () => {
    expect(mean([1, 2, 3, 4])).toBe(2.5);
    expect(std([2, 4, 4, 4, 5, 5, 7, 9])).toBeCloseTo(2.138, 2);
  });

  test('quantile interpolation', () => {
    expect(quantile([1, 2, 3, 4], 0.5)).toBe(2.5);
    expect(quantile([1, 2, 3, 4], 0)).toBe(1);
    expect(quantile([1, 2, 3, 4], 1)).toBe(4);
    expect(quantile([1, 2, 3, 4], 0.25)).toBe(1.75);
  });

  test('planar fit recovers an exact plane', () => {
    const pts: Array<{ i: number; j: number; v: number }> = [];
    for (let i = 0; i < 10; i++) {
      for (let j = 0; j < 10; j++) {
        pts.push({ i, j, v: 0.5 * i - 0.25 * j + 3 });
      }
    }
    const fit = planarFit(pts)!;
    expect(fit.a).toBeCloseTo(0.5, 9);
    expect(fit.b).toBeCloseTo(-0.25, 9);
    expect(fit.c).toBeCloseTo(3, 9);
  });

  test('Pearson correlation of perfectly correlated and uncorrelated series', () => {
    expect(pearson([1, 2, 3, 4], [2, 4, 6, 8])).toBeCloseTo(1, 12);
    expect(pearson([1, 2, 3, 4], [4, 3, 2, 1])).toBeCloseTo(-1, 12);
    const x = [1, 2, 3, 4, 5];
    const y = [1, -1, 1, -1, 1];
    expect(Math.abs(pearson(x, y))).toBeLessThan(0.9);
  });
});
