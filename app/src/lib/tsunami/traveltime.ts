/**
 * Travel-time field from the eikonal equation ‖∇T‖ = 1/c(x,y) with
 * c = √(g·h) on the still-water depth — solved by Gauss–Seidel fast sweeping
 * (alternating sweep orders; converges to the viscosity solution in a finite
 * number of passes for convex Hamiltonians — Zhao 2005).
 *
 * Seeding: T = 0 on wet cells where the source deformation |Δh| exceeds
 * seedFraction·max|Δh| (default 10%) — the deformed sea floor region.
 *
 * Used for an independent first-arrival estimate that does not depend on the
 * nonlinear solver, and for cross-checking model arrival times.
 */

import type { BathymetryGrid } from './types';

const G = 9.81;

export interface TravelTimeOptions {
  /** fraction of max |Δh| above which cells seed T = 0 */
  seedFraction?: number;
  /** sweep passes (4 per round) */
  rounds?: number;
}

/**
 * Returns T (seconds, NaN on land / unreachable) on the grid.
 */
export function travelTimeField(
  grid: BathymetryGrid,
  dz: Float32Array,
  opts: TravelTimeOptions = {},
): Float32Array {
  const { nx, ny, resArcmin, lat0 } = grid;
  const seedFraction = opts.seedFraction ?? 0.1;
  const rounds = opts.rounds ?? 6;

  let maxAbs = 0;
  for (let k = 0; k < dz.length; k++) {
    const a = Math.abs(dz[k]);
    if (a > maxAbs) maxAbs = a;
  }
  const seedLevel = seedFraction * maxAbs;

  const T = new Float32Array(nx * ny).fill(Infinity);
  for (let k = 0; k < T.length; k++) {
    if (grid.bed[k] < 0 && Math.abs(dz[k]) >= seedLevel) T[k] = 0;
  }

  const cellDy = ((resArcmin / 60) * Math.PI * 6371.0088e3) / 180;

  /**
   * Local Godunov update of the eikonal discretization (Zhao 2005):
   *   (T − Tx)²/dx² + (T − Ty)²/dy² = 1/c²
   * with Tx/Ty the upwind (smallest) neighbour times. Falls back to the
   * one-sided form when the quadratic has no admissible root. A naive
   * neighbour+distance update would converge to the L1 (Manhattan) metric
   * and overestimate diagonal travel times by up to √2.
   */
  const updateCell = (j: number, i: number): void => {
    const k = j * nx + i;
    const hk = -grid.bed[k];
    if (hk <= 0) return; // land
    const meanLat = lat0 + (j * resArcmin) / 60;
    const dx = ((resArcmin / 60) * Math.PI * 6371.0088e3 * Math.cos((meanLat * Math.PI) / 180)) / 180;
    const dy = cellDy;

    // upwind neighbour times
    let Tx = Infinity;
    if (i > 0 && T[k - 1] < Tx) Tx = T[k - 1];
    if (i < nx - 1 && T[k + 1] < Tx) Tx = T[k + 1];
    let Ty = Infinity;
    if (j > 0 && T[k - nx] < Ty) Ty = T[k - nx];
    if (j < ny - 1 && T[k + nx] < Ty) Ty = T[k + nx];

    // wave speed: min of this cell's and the contributing neighbour's depth
    const cOf = (kn: number): number => {
      const hn = -grid.bed[kn];
      const hmin = Math.min(hk, hn);
      return hmin > 0 ? Math.sqrt(G * hmin) : 0;
    };
    let cx = 0;
    if (Number.isFinite(Tx)) {
      const kn = i > 0 && T[k - 1] <= (i < nx - 1 ? T[k + 1] : Infinity) ? k - 1 : k + 1;
      cx = cOf(kn);
    }
    let cy = 0;
    if (Number.isFinite(Ty)) {
      const kn = j > 0 && T[k - nx] <= (j < ny - 1 ? T[k + nx] : Infinity) ? k - nx : k + nx;
      cy = cOf(kn);
    }

    const oneX = cx > 0 ? Tx + dx / cx : Infinity;
    const oneY = cy > 0 ? Ty + dy / cy : Infinity;
    let cand = Math.min(oneX, oneY);

    if (Number.isFinite(Tx) && Number.isFinite(Ty) && cx > 0 && cy > 0) {
      // solve dy²(T−Tx)² + dx²(T−Ty)² = (dx·dy/c)² with c = harmonic-ish min
      const c = Math.min(cx, cy);
      const A = dy * dy + dx * dx;
      const B = -2 * (dy * dy * Tx + dx * dx * Ty);
      const C = dy * dy * Tx * Tx + dx * dx * Ty * Ty - (dx * dx * dy * dy) / (c * c);
      const disc = B * B - 4 * A * C;
      if (disc >= 0) {
        // the eikonal root is the PLUS root (≥ max(Tx, Ty)); the minus root
        // is the non-upwind branch (verified on the flat-plane benchmark)
        const r = (-B + Math.sqrt(disc)) / (2 * A);
        if (r >= Math.max(Tx, Ty) - 1e-9) cand = Math.min(cand, r);
      }
    }
    if (cand < T[k]) T[k] = cand;
  };

  for (let round = 0; round < rounds; round++) {
    const before = Float32Array.from(T);
    // four sweep orders
    const sweeps: Array<[number, number, number, number]> = [
      [0, ny, 1, 1],   // top-down, left-right
      [0, ny, 1, -1],  // top-down, right-left
      [ny - 1, -1, -1, 1],
      [ny - 1, -1, -1, -1],
    ];
    for (const [j0, j1, js, is] of sweeps) {
      for (let j = j0; js > 0 ? j < j1 : j > j1; j += js) {
        for (let i = is > 0 ? 0 : nx - 1; is > 0 ? i < nx : i >= 0; i += is) {
          updateCell(j, i);
        }
      }
    }
    if (round >= 1) {
      let maxDelta = 0;
      for (let k = 0; k < T.length; k++) {
        const d = Math.abs(T[k] - before[k]);
        if (d > maxDelta) maxDelta = d;
      }
      if (maxDelta < 1e-3) break;
    }
  }

  for (let k = 0; k < T.length; k++) {
    if (!Number.isFinite(T[k])) T[k] = NaN;
  }
  return T;
}

/** Travel time (s) at the nearest wet cell to (lat, lon); NaN if unreachable. */
export function travelTimeAt(
  grid: BathymetryGrid,
  T: Float32Array,
  lat: number,
  lon: number,
): number {
  const j = Math.round(((lat - grid.lat0) * 60) / grid.resArcmin);
  const i = Math.round(((lon - grid.lon0) * 60) / grid.resArcmin);
  if (j < 0 || j >= grid.ny || i < 0 || i >= grid.nx) return NaN;
  const t = T[j * grid.nx + i];
  return Number.isFinite(t) ? t : NaN;
}
