import { describe, expect, test } from 'bun:test';
import { okadaSurfaceDisplacementRaw, okadaSurfaceDisplacement, momentFromMw, ruptureAreaFromMw, slipFromMoment } from '@/lib/science/okada';

/**
 * Benchmark values generated with tools/dc3d_reference.py — a Python port of
 * Okada's official DC3D Fortran code (Okada 1985/1992;
 * https://github.com/satoshi-pes/dc3d.py). Regenerate with:
 *   python3 tests/science/generate_okada_benchmarks.py
 */
const OKADA_BENCHMARKS: Array<{
  geom: [number, number, number, number, number, number, number, number];
  mode: [number, number, number];
  ref: [number, number, number];
}> = [
  { geom: [2.0, 2.0, 2.0, 70.0, -1.5, 1.5, -1.0, 1.0], mode: [1, 0, 0], ref: [-3.26336498e-2, -3.20658929e-2, 5.2895183e-4] },
  { geom: [2.0, 2.0, 2.0, 70.0, -1.5, 1.5, -1.0, 1.0], mode: [0, 1, 0], ref: [-2.05921477e-2, -4.23946473e-2, -3.22456728e-2] },
  { geom: [2.0, 2.0, 2.0, 70.0, -1.5, 1.5, -1.0, 1.0], mode: [0, 0, 1], ref: [-1.66402795e-2, 1.82395965e-2, -2.2021671e-3] },
  { geom: [2.0, 2.0, 2.0, 70.0, -1.0, 1.0, -1.0, 0.5], mode: [1, 0, 0], ref: [-1.32699212e-2, -1.31207063e-2, -7.7402835e-4] },
  { geom: [2.0, 2.0, 2.0, 70.0, -1.0, 1.0, -1.0, 0.5], mode: [0, 1, 0], ref: [-9.02170739e-3, -1.60404635e-2, -1.36822143e-2] },
  { geom: [2.0, 2.0, 2.0, 70.0, -1.0, 1.0, -1.0, 0.5], mode: [0, 0, 1], ref: [-8.6031592e-3, 5.21290372e-3, -2.65369623e-3] },
  { geom: [0.0, 3.0, 5.0, 30.0, -4.0, 4.0, 0.0, 6.0], mode: [1, 0, 0], ref: [1.99292543e-1, 0.0, 0.0] },
  { geom: [0.0, 3.0, 5.0, 30.0, -4.0, 4.0, 0.0, 6.0], mode: [0, 1, 0], ref: [0.0, 6.14805647e-2, 3.24853093e-1] },
  { geom: [0.0, 3.0, 5.0, 30.0, -4.0, 4.0, 0.0, 6.0], mode: [0, 0, 1], ref: [0.0, -1.4407736e-1, 5.90259192e-1] },
  { geom: [-2.5, 1.7, 3.3, 55.0, -2.0, 2.0, -1.0, 2.0], mode: [1, 0, 0], ref: [1.46764779e-2, 7.58623534e-3, -3.78010804e-2] },
  { geom: [-2.5, 1.7, 3.3, 55.0, -2.0, 2.0, -1.0, 2.0], mode: [0, 1, 0], ref: [-4.64434341e-2, 1.76530304e-2, 5.07648421e-2] },
  { geom: [-2.5, 1.7, 3.3, 55.0, -2.0, 2.0, -1.0, 2.0], mode: [0, 0, 1], ref: [2.93760329e-2, 1.81143499e-4, -1.4838566e-2] },
  { geom: [1.1, -0.4, 8.0, 90.0, -5.0, 5.0, 0.0, 4.0], mode: [1, 0, 0], ref: [6.76783101e-3, -5.24851853e-3, 1.07113276e-3] },
  { geom: [1.1, -0.4, 8.0, 90.0, -5.0, 5.0, 0.0, 4.0], mode: [0, 1, 0], ref: [1.82933248e-3, -1.99881195e-3, 2.63130862e-2] },
  { geom: [1.1, -0.4, 8.0, 90.0, -5.0, 5.0, 0.0, 4.0], mode: [0, 0, 1], ref: [-1.02084725e-2, 1.67973547e-3, -5.80851401e-2] },
  { geom: [0.001, 0.002, 12.0, 10.0, -10.0, 10.0, 0.0, 12.0], mode: [1, 0, 0], ref: [6.96112294e-2, -6.6184816e-6, 1.30139394e-5] },
  { geom: [0.001, 0.002, 12.0, 10.0, -10.0, 10.0, 0.0, 12.0], mode: [0, 1, 0], ref: [-2.6437287e-6, 6.47601684e-2, -7.0785141e-2] },
  { geom: [0.001, 0.002, 12.0, 10.0, -10.0, 10.0, 0.0, 12.0], mode: [0, 0, 1], ref: [1.41841656e-5, -1.63080211e-1, 3.72532435e-1] },
  { geom: [-4.0, -3.0, 6.0, 45.0, -3.0, 3.0, -2.0, 1.5], mode: [1, 0, 0], ref: [2.99996609e-2, 2.4546762e-2, -3.70027609e-2] },
  { geom: [-4.0, -3.0, 6.0, 45.0, -3.0, 3.0, -2.0, 1.5], mode: [0, 1, 0], ref: [-2.45652335e-2, -1.43496669e-2, 3.99895878e-2] },
  { geom: [-4.0, -3.0, 6.0, 45.0, -3.0, 3.0, -2.0, 1.5], mode: [0, 0, 1], ref: [-3.55614335e-2, -4.02331535e-2, 7.57477618e-2] },
  { geom: [7.0, 5.0, 4.0, 70.0, -2.0, 2.0, 0.0, 3.0], mode: [1, 0, 0], ref: [-1.42886816e-2, -1.62278996e-2, 6.97015734e-3] },
  { geom: [7.0, 5.0, 4.0, 70.0, -2.0, 2.0, 0.0, 3.0], mode: [0, 1, 0], ref: [-5.49936515e-3, -9.19523226e-3, -5.15193116e-3] },
  { geom: [7.0, 5.0, 4.0, 70.0, -2.0, 2.0, 0.0, 3.0], mode: [0, 0, 1], ref: [-5.10608733e-3, 1.10631995e-2, 5.80348979e-3] },
];

describe('Okada85 surface displacement (DC3D port)', () => {
  test('matches the DC3D reference implementation to <=1e-8 relative', () => {
    for (const b of OKADA_BENCHMARKS) {
      const [x, y, depth, dip, al1, al2, aw1, aw2] = b.geom;
      const r = okadaSurfaceDisplacementRaw(x, y, depth, dip, al1, al2, aw1, aw2, b.mode[0], b.mode[1], b.mode[2]);
      expect(r.singular).toBe(false);
      for (let k = 0; k < 3; k++) {
        const got = [r.ux, r.uy, r.uz][k];
        const ref = b.ref[k];
        const rel = Math.abs(got - ref) / Math.max(Math.abs(ref), 1e-30);
        expect(rel).toBeLessThan(1e-8);
      }
    }
  });

  test('friendly wrapper places the fault BELOW the top-edge depth (buried field matches 2D analytic screw solution)', () => {
    // Long vertical strike-slip, top depth 1, width 10, unit slip.
    // 2D anti-plane solution: u(y) = (1/pi)[atan(y/d1) - atan(y/d2)] (Segall 2010 style).
    const cases: Array<[number, number]> = [
      [0.3, 0.0841],
      [1.0, 0.2211],
      [3.3, 0.3136],
      [5.0, 0.3014],
    ];
    for (const [y, expect2d] of cases) {
      const r = okadaSurfaceDisplacement(0, y, { depth: 1, dipDeg: 90, length: 100, width: 10, u1: 1, u2: 0, u3: 0 });
      expect(Math.abs(r.ux)).toBeGreaterThan(expect2d * 0.8);
      expect(Math.abs(r.ux)).toBeLessThan(expect2d * 1.2);
      expect(Math.abs(r.uy)).toBeLessThan(1e-9);
      expect(Math.abs(r.uz)).toBeLessThan(1e-9);
    }
  });

  test('dip-slip U2=+1 is normal-sense: the hanging wall drops across the trace', () => {
    const mk = (y: number) =>
      okadaSurfaceDisplacement(0, y, { depth: 50, dipDeg: 80, length: 100000, width: 10000, u1: 0, u2: 1, u3: 0 });
    const hang = mk(100);
    const foot = mk(-100);
    expect(hang.uz - foot.uz).toBeLessThan(-0.5); // hanging wall drops
  });

  test('strike-slip U1=+1 is sinistral: hanging wall moves -x across the trace', () => {
    const mk = (y: number) =>
      okadaSurfaceDisplacement(0, y, { depth: 50, dipDeg: 80, length: 100000, width: 10000, u1: 1, u2: 0, u3: 0 });
    const jump = mk(100).ux - mk(-100).ux;
    expect(jump).toBeLessThan(-0.3);
  });

  test('tensile U3=+1 is opening: walls separate across the trace', () => {
    const mk = (y: number) =>
      okadaSurfaceDisplacement(0, y, { depth: 50, dipDeg: 80, length: 100000, width: 10000, u1: 0, u2: 0, u3: 1 });
    const jump = mk(100).uy - mk(-100).uy;
    expect(jump).toBeGreaterThan(0.2);
  });

  test('displacement decays to ~0 far from the fault', () => {
    const r = okadaSurfaceDisplacement(0, 200000, { depth: 2000, dipDeg: 60, length: 20000, width: 8000, u1: 0, u2: 1, u3: 0 });
    expect(Math.hypot(r.ux, r.uy, r.uz)).toBeLessThan(0.01);
  });

  test('moment and area scaling behave sensibly', () => {
    // Mw 7 -> M0 ~ 3.55e19 N m (Hanks & Kanamori 1979)
    const m0 = momentFromMw(7);
    expect(m0).toBeGreaterThan(3e19);
    expect(m0).toBeLessThan(4e19);
    // Mw 7 area ~ 10^(0.91*7-3.49) km^2 ~ 750
    const a = ruptureAreaFromMw(7) / 1e6;
    expect(a).toBeGreaterThan(400);
    expect(a).toBeLessThan(1300);
    // Uniform slip ~ M0/(mu A) ~ 1.5 m for Mw 7
    const slip = slipFromMoment(m0, a * 1e6);
    expect(slip).toBeGreaterThan(0.5);
    expect(slip).toBeLessThan(5);
  });
});
