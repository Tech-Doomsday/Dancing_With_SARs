/**
 * 2-D nonlinear shallow-water equations (NSWE) — well-balanced finite-volume
 * solver on a regular lat/lon grid (ETOPO1-derived).
 *
 * Equations (conservative form, bed b positive-up, h = η − b):
 *   ∂h/∂t   + ∂(hu)/∂x + ∂(hv)/∂y = 0
 *   ∂(hu)/∂t + ∂(hu² + ½gh²)/∂x + ∂(huv)/∂y = −gh ∂b/∂x − τ_x/ρ
 *   ∂(hv)/∂t + ∂(huv)/∂x + ∂(hv² + ½gh²)/∂y = −gh ∂b/∂y − τ_y/ρ
 * Manning friction τ/ρ = g n² u|u| / h^{1/3} (semi-implicit, per stage).
 *
 * Numerics (every choice documented in docs/tsunami.md and the evidence matrix):
 *   - Hydrostatic reconstruction (Audusse et al. 2004): interface states are
 *     re-centred on max(b_L, b_R) with the double-sided pressure correction,
 *     which makes the scheme exactly well-balanced for the lake at rest
 *     INCLUDING wet/dry fronts. The correction uses CELL-centre depths, so
 *     the equilibrium cancellation survives the MUSCL reconstruction.
 *   - HLL flux with Einfeldt speeds and George (2008) dry-front speeds:
 *     s_L = min(u_L−√(g h_L), u_R−√(g h_R)), s_R = max(u_L+√(g h_L), u_R+√(g h_R));
 *     a dry side emits no waves. Positivity-preserving under the CFL bound.
 *     (HLLC's contact wave is immaterial for tsunami propagation — documented.)
 *   - order=2: MUSCL reconstruction of (η, hu, hv) with the monotonized-central
 *     limiter (bed piecewise-constant) + SSP-RK2 (Heun) time integration.
 *     order=1: piecewise-constant states + one Euler stage.
 *   - Wet/dry: h_dry = 1e-4 m; momentum zeroed below 10·h_dry (drain-time
 *     robustness); dried cells sit at η = b.
 *   - Adaptive Δt each step from the 2-D CFL SUM
 *       Δt ≤ C / max((|u|+√gh)/Δx + (|v|+√gh)/Δy),  C = 0.45 (order 2) / 0.5.
 *   - Variable cell areas A_j = Δx_j·Δy (cos φ convergence): the update is
 *     exactly conservative in the variable-area sense. Coriolis and spherical
 *     metric terms are omitted (documented approximation: for tsunami periods
 *     f ≪ ω, first-arrival error ≪ 1 % over regional domains).
 *   - Open boundaries: Rayleigh sponge layers (quadratic ramp on η−η₀ and
 *     momentum) on every edge; land edges are naturally reflective dry cells.
 *
 * The solver is a stepping object (advance N steps per call) so the runner can
 * chunk work asynchronously without blocking the dev server event loop.
 */

import type { BathymetryGrid, GaugeSeries, SimParams } from './types';

const G = 9.81;
const H_DRY = 1e-4;
/** momentum zeroed below this depth (m) — thin-film velocity limiter */
const H_VEL = 0.01;
const H_VEL2 = H_VEL * H_VEL;
/** MUSCL reconstruction disabled below this depth (flattening threshold) */
const H_MUSCL = 0.1;

export interface GaugeSpec {
  stationId: string;
  lat: number;
  lon: number;
}

export interface SolverFieldSnapshot {
  /** max |η| over the run (m) */
  maxEta: Float32Array;
  /** first time (s) |η| exceeded arrivalThreshold, NaN = never */
  arrival: Float32Array;
  /** max flow depth on land (m) — coarse-DEM caveat applies */
  maxFlowDepth: Float32Array;
}

export interface SolverDiagnostics {
  nSteps: number;
  tSim: number;
  minDt: number;
  maxDt: number;
  massInitial: number;
  massFinal: number;
  massDriftRelative: number;
  maxEtaPeak: number;
  maxVelocity: number;
  /** positivity-violation count (should be 0 under the CFL) */
  nNegClamped: number;
}

export interface SolverResult {
  diagnostics: SolverDiagnostics;
  fields: SolverFieldSnapshot;
  gauges: GaugeSeries[];
}

interface Geom {
  nx: number;
  ny: number;
  dx: Float64Array;
  dy: number;
  area: Float64Array;
  yEdge: Float64Array;
}

/**
 * Grid geometry — LOCAL CARTESIAN (tangent-plane) formulation:
 * all rows use the zonal cell width evaluated at the grid's CENTRE latitude.
 * Rationale (documented in docs/tsunami.md): with per-row cos(φ) widths and
 * no spherical metric terms, a conservative variable-area discretization
 * cannot simultaneously be exactly well-balanced — the resting-lake pressure
 * leaves an O(Δcosφ) residual (measured 1e-4 m/s per step) that destroys
 * the lake-at-rest benchmark. The tangent-plane form makes conservation and
 * well-balancing EXACT; its cost is a zonal-distance error of
 * |cos(φ)/cos(φ_c) − 1| ≤ ~5% at the edges of a ±5° regional domain
 * (verified acceptable against the DART arrival-time tolerance of ±15 min).
 */
function buildGeom(grid: BathymetryGrid): Geom {
  const { nx, ny, resArcmin, lat0 } = grid;
  const R = 6371.0088e3;
  const latC = lat0 + ((ny - 1) * resArcmin) / 120;
  const dxC = ((resArcmin / 60) * Math.PI * R * Math.cos((latC * Math.PI) / 180)) / 180;
  const dy = ((resArcmin / 60) * Math.PI * R) / 180;
  const dx = new Float64Array(ny).fill(dxC);
  const area = new Float64Array(ny).fill(dxC * dy);
  const yEdge = new Float64Array(Math.max(0, ny - 1)).fill(dxC);
  return { nx, ny, dx, dy, area, yEdge };
}

/**
 * HLL flux for the shallow-water system. (hL,uL,vL) left state, normal u.
 * out = (mass flux, normal-momentum flux, tangential-momentum flux).
 */
function hllFlux(
  hL: number, uL: number, vL: number,
  hR: number, uR: number, vR: number,
  out: Float64Array,
): void {
  if (hL <= 0 && hR <= 0) {
    out[0] = 0; out[1] = 0; out[2] = 0;
    return;
  }
  const cL = hL > 0 ? Math.sqrt(G * hL) : 0;
  const cR = hR > 0 ? Math.sqrt(G * hR) : 0;
  let sL = Math.min(uL - cL, uR - cR);
  let sR = Math.max(uL + cL, uR + cR);
  // Dry-front speeds (George 2008): the dry side emits nothing faster than
  // the wet side's rarefaction edge.
  if (hL <= 0 && hR > 0) sL = Math.min(sL, uR - 2 * cR);
  if (hR <= 0 && hL > 0) sR = Math.max(sR, uL + 2 * cL);
  if (sL >= 0) {
    out[0] = hL * uL;
    out[1] = hL * uL * uL + 0.5 * G * hL * hL;
    out[2] = hL * uL * vL;
    return;
  }
  if (sR <= 0) {
    out[0] = hR * uR;
    out[1] = hR * uR * uR + 0.5 * G * hR * hR;
    out[2] = hR * uR * vR;
    return;
  }
  const f0L = hL * uL;
  const f1L = hL * uL * uL + 0.5 * G * hL * hL;
  const f2L = hL * uL * vL;
  const f0R = hR * uR;
  const f1R = hR * uR * uR + 0.5 * G * hR * hR;
  const f2R = hR * uR * vR;
  const inv = 1 / (sR - sL);
  out[0] = (sR * f0R - sL * f0L + sL * sR * (hR - hL)) * inv;
  out[1] = (sR * f1R - sL * f1L + sL * sR * (hR * uR - hL * uL)) * inv;
  out[2] = (sR * f2R - sL * f2L + sL * sR * (hR * vR - hL * vL)) * inv;
}

/** Two-argument monotonized-central (MC) limiter slope. */
function mc2(a: number, b: number): number {
  if (a * b <= 0) return 0;
  const s = a + b;
  if (s === 0) return 0;
  const m = 2 * Math.min(Math.abs(a), Math.abs(b));
  return Math.sign(s) * Math.min(Math.abs(s) / 2, m);
}

/**
 * The stepping NSWE solver. Construct once per run; call advance() in chunks.
 */
export class NsweSolver {
  readonly nx: number;
  readonly ny: number;
  private readonly geom: Geom;
  private readonly eta0: Float64Array;
  private eta: Float64Array;
  private hu: Float64Array;
  private hv: Float64Array;
  private readonly bed: Float64Array;
  private t = 0;
  private nSteps = 0;
  private minDt = Infinity;
  private maxDt = 0;
  private mass0 = 0;
  private maxEta: Float32Array;
  private arrival: Float32Array;
  private maxFlowDepth: Float32Array;
  private readonly arrivalThreshold: number;
  private readonly order: 1 | 2;
  private readonly manningN: number;
  private readonly spongeCells: number;
  private readonly spongeMax = 0.25;
  private readonly gauges: Array<{ spec: GaugeSpec; idx: number; tMin: number[]; eta: number[] }>;
  private readonly gaugeDt = 30; // s
  private nextGaugeT = 0;
  private maxVel = 0;
  /** times the negative-depth clamp fired (positivity violations) */
  private nNegClamped = 0;

  // order-2 scratch
  private s1eta!: Float64Array;
  private s1hu!: Float64Array;
  private s1hv!: Float64Array;
  private s2eta!: Float64Array;
  private s2hu!: Float64Array;
  private s2hv!: Float64Array;
  private slopeXE!: Float64Array;
  private slopeXU!: Float64Array;
  private slopeXV!: Float64Array;
  private slopeYE!: Float64Array;
  private slopeYU!: Float64Array;
  private slopeYV!: Float64Array;

  constructor(
    grid: BathymetryGrid,
    etaInit: Float32Array,
    params: SimParams,
    gaugeSpecs: GaugeSpec[],
  ) {
    this.geom = buildGeom(grid);
    this.nx = grid.nx;
    this.ny = grid.ny;
    const n = this.nx * this.ny;
    this.bed = new Float64Array(n);
    for (let k = 0; k < n; k++) this.bed[k] = grid.bed[k];
    this.eta = new Float64Array(n);
    this.hu = new Float64Array(n);
    this.hv = new Float64Array(n);
    this.eta0 = new Float64Array(n);
    for (let k = 0; k < n; k++) {
      const e = Math.max(etaInit[k], this.bed[k]); // surface never below the bed
      this.eta[k] = e;
      this.eta0[k] = e;
    }
    this.maxEta = new Float32Array(n);
    this.arrival = new Float32Array(n).fill(NaN);
    this.maxFlowDepth = new Float32Array(n);
    this.arrivalThreshold = params.arrivalThresholdM;
    this.order = params.order;
    this.manningN = params.manningN;
    // Sponge width scales with resolution (~35 cells) but never more than
    // 15% of either grid dimension — a 2-D min-distance sponge would
    // otherwise blanket small domains entirely and absorb interior mass.
    // params.spongeEnabled = false gives a purely reflective basin (used by
    // the conservation benchmarks, where absorption is the one thing that
    // MUST NOT happen).
    this.spongeCells = params.spongeEnabled === false
      ? 0
      : Math.max(
          2,
          Math.min(
            Math.round(35 / Math.sqrt(params.resArcmin)),
            Math.floor(0.15 * Math.min(grid.nx, grid.ny)),
          ),
        );

    for (let j = 0; j < this.ny; j++) {
      for (let i = 0; i < this.nx; i++) {
        const k = j * this.nx + i;
        const h = this.eta[k] - this.bed[k];
        if (h > 0) this.mass0 += h * this.geom.area[j];
      }
    }

    this.gauges = gaugeSpecs.map((s) => {
      const j = Math.round(((s.lat - grid.lat0) * 60) / grid.resArcmin);
      const i = Math.round(((s.lon - grid.lon0) * 60) / grid.resArcmin);
      const idx = j >= 0 && j < this.ny && i >= 0 && i < this.nx ? j * this.nx + i : -1;
      return { spec: s, idx, tMin: [], eta: [] };
    });

    // Scratch arrays are needed by both orders (order 1 stages out-of-place,
    // order 2 runs SSP-RK2); slopes only by order 2.
    this.s1eta = new Float64Array(n);
    this.s1hu = new Float64Array(n);
    this.s1hv = new Float64Array(n);
    this.s2eta = new Float64Array(n);
    this.s2hu = new Float64Array(n);
    this.s2hv = new Float64Array(n);
    if (this.order === 2) {
      this.slopeXE = new Float64Array(n);
      this.slopeXU = new Float64Array(n);
      this.slopeXV = new Float64Array(n);
      this.slopeYE = new Float64Array(n);
      this.slopeYU = new Float64Array(n);
      this.slopeYV = new Float64Array(n);
    }

    this.recordFields(0);
  }

  get simTime(): number {
    return this.t;
  }
  get steps(): number {
    return this.nSteps;
  }
  get progressInfo(): { t: number; steps: number; maxVel: number } {
    return { t: this.t, steps: this.nSteps, maxVel: this.maxVel };
  }

  private spongeFactor(j: number, i: number): number {
    const d = Math.min(Math.min(j, this.ny - 1 - j), Math.min(i, this.nx - 1 - i));
    if (d >= this.spongeCells) return 0;
    const f = 1 - d / this.spongeCells;
    return this.spongeMax * f * f;
  }

  /**
   * Stable time step from the 2-D CFL sum (recomputed every step).
   * Cells bordering a DRY neighbour use the dry-front characteristic speed
   * |u| + 2√(gh) (the exact dam-break-into-dry-bed front speed, George 2008)
   * — using only |u| + √(gh) there violates positivity at run-up fronts and
   * silently destroys mass through the negative-depth clamp.
   */
  private computeDt(): number {
    const cfl = this.order === 2 ? 0.45 : 0.5;
    let maxRate = 1e-9;
    for (let j = 0; j < this.ny; j++) {
      const dxj = this.geom.dx[j];
      for (let i = 0; i < this.nx; i++) {
        const k = j * this.nx + i;
        const h = this.eta[k] - this.bed[k];
        if (h <= H_DRY) continue;
        const c = Math.sqrt(G * h);
        const u = this.hu[k] / h;
        const v = this.hv[k] / h;
        // dry-front speed where a NEIGHBOUR is dry (domain walls are
        // reflective, not dry — their mirrored-ghost speeds stay ≤ |u|+c)
        const dryL = i > 0 && this.eta[k - 1] - this.bed[k - 1] <= H_DRY;
        const dryR = i < this.nx - 1 && this.eta[k + 1] - this.bed[k + 1] <= H_DRY;
        const dryD = j > 0 && this.eta[k - this.nx] - this.bed[k - this.nx] <= H_DRY;
        const dryU = j < this.ny - 1 && this.eta[k + this.nx] - this.bed[k + this.nx] <= H_DRY;
        const cx = dryL || dryR ? Math.abs(u) + 2 * c : Math.abs(u) + c;
        const cy = dryD || dryU ? Math.abs(v) + 2 * c : Math.abs(v) + c;
        const rate = cx / dxj + cy / this.geom.dy;
        if (rate > maxRate) maxRate = rate;
      }
    }
    return cfl / maxRate;
  }

  /** MUSCL slopes of a field (full grid, both directions). */
  private computeSlopes(f: Float64Array, sX: Float64Array, sY: Float64Array): void {
    const { nx, ny } = this;
    // x slopes (interior; edges 0)
    for (let j = 0; j < ny; j++) {
      const base = j * nx;
      for (let i = 1; i < nx - 1; i++) {
        sX[base + i] = mc2(f[base + i] - f[base + i - 1], f[base + i + 1] - f[base + i]);
      }
      sX[base] = 0;
      sX[base + nx - 1] = 0;
    }
    // y slopes
    for (let j = 1; j < ny - 1; j++) {
      for (let i = 0; i < nx; i++) {
        const k = j * nx + i;
        sY[k] = mc2(f[k] - f[k - nx], f[k + nx] - f[k]);
      }
    }
    for (let i = 0; i < nx; i++) {
      sY[i] = 0;
      sY[(ny - 1) * nx + i] = 0;
    }
  }

  /**
   * One explicit Euler stage: interface fluxes with hydrostatic
   * reconstruction (+ MUSCL when order 2), side corrections, friction,
   * wet/dry and sponge. Writes the updated state into (etaOut,huOut,hvOut)
   * given inputs (etaIn,huIn,hvIn). In-place (out === in) is allowed for
   * order 1 because each cell update only reads neighbours through the
   * pre-computed interface loop order... NOT safe: the x sweep reads etaIn of
   * the same cells it writes. Order 1 therefore also uses scratch arrays.
   */
  private eulerStage(
    dt: number,
    etaIn: Float64Array, huIn: Float64Array, hvIn: Float64Array,
    etaOut: Float64Array, huOut: Float64Array, hvOut: Float64Array,
  ): void {
    const { nx, ny, geom } = this;
    const second = this.order === 2;
    if (second) {
      this.computeSlopes(etaIn, this.slopeXE, this.slopeYE);
      this.computeSlopes(huIn, this.slopeXU, this.slopeYU);
      this.computeSlopes(hvIn, this.slopeXV, this.slopeYV);
    }

    etaOut.set(etaIn);
    huOut.set(huIn);
    hvOut.set(hvIn);

    const flux = new Float64Array(3);

    // ---------------- x sweep ----------------
    for (let j = 0; j < ny; j++) {
      const aj = geom.area[j];
      const cx = (dt * geom.dy) / aj;

      // Left/right DOMAIN EDGES act as reflective walls: a mirrored ghost
      // state (u → −u) supplies the wall reaction. Without this, the resting
      // lake's hydrostatic pressure accelerates the edge cells — the
      // well-balanced property requires the wall flux at every closed edge.
      {
        const k = j * nx;
        const b = this.bed[k];
        const e = etaIn[k];
        let hs = e - b;
        if (hs < 0) hs = 0;
        const u = hs > H_DRY && e - b > H_DRY ? huIn[k] / (e - b) : 0;
        const v = hs > H_DRY && e - b > H_DRY ? hvIn[k] / (e - b) : 0;
        hllFlux(hs, -u, v, hs, u, v, flux); // ghost mirrors the normal velocity
        const h0 = e - b > 0 ? e - b : 0;
        const fw = flux[1] + 0.5 * G * (h0 * h0 - hs * hs);
        huOut[k] += cx * fw;
        hvOut[k] += cx * flux[2];
      }
      {
        const k = j * nx + (nx - 1);
        const b = this.bed[k];
        const e = etaIn[k];
        let hs = e - b;
        if (hs < 0) hs = 0;
        const u = hs > H_DRY && e - b > H_DRY ? huIn[k] / (e - b) : 0;
        const v = hs > H_DRY && e - b > H_DRY ? hvIn[k] / (e - b) : 0;
        hllFlux(hs, u, v, hs, -u, v, flux);
        const h0 = e - b > 0 ? e - b : 0;
        const fw = flux[1] + 0.5 * G * (h0 * h0 - hs * hs);
        huOut[k] -= cx * fw;
        hvOut[k] -= cx * flux[2];
      }

      for (let i = 0; i <= nx - 2; i++) {
        const kL = j * nx + i;
        const kR = kL + 1;
        const bL = this.bed[kL];
        const bR = this.bed[kR];
        const bMax = bL > bR ? bL : bR;

        let eL = etaIn[kL];
        let eR = etaIn[kR];
        let pL = huIn[kL];
        let pR = huIn[kR];
        let qL = hvIn[kL];
        let qR = hvIn[kR];
        {
          // flattening: revert to piecewise-constant near dry states —
          // second-order reconstruction is not positivity-safe at wet/dry
          // fronts (standard practice, e.g. GeoClaw's limiter strategy)
          const hLcell = eL - bL;
          const hRcell = eR - bR;
          const safe =
            second && hLcell > H_MUSCL && hRcell > H_MUSCL &&
            eL - bMax > H_MUSCL && eR - bMax > H_MUSCL;
          if (safe) {
            eL += 0.5 * this.slopeXE[kL];
            pL += 0.5 * this.slopeXU[kL];
            qL += 0.5 * this.slopeXV[kL];
            eR -= 0.5 * this.slopeXE[kR];
            pR -= 0.5 * this.slopeXU[kR];
            qR -= 0.5 * this.slopeXV[kR];
          }
        }

        let hsL = eL - bMax;
        let hsR = eR - bMax;
        if (hsL < 0) hsL = 0;
        if (hsR < 0) hsR = 0;
        let uL = 0, vL = 0, uR = 0, vR = 0;
        const dL = eL - bL;
        const dR = eR - bR;
        if (dL > H_DRY && hsL > 0) {
          uL = (pL * dL) / (dL * dL + H_VEL2);
          vL = (qL * dL) / (dL * dL + H_VEL2);
        }
        if (dR > H_DRY && hsR > 0) {
          uR = (pR * dR) / (dR * dR + H_VEL2);
          vR = (qR * dR) / (dR * dR + H_VEL2);
        }
        hllFlux(hsL, uL, vL, hsR, uR, vR, flux);

        // Audusse side corrections use the CELL-SIDE depth at this interface:
        // the RECONSTRUCTED depth in the cell's own bed frame (equals the
        // cell-centre depth when the reconstruction is flattened/order 1).
        // Using the cell-centre depth with MUSCL would double-count the
        // reconstruction and cancel the physical pressure gradient (verified:
        // wave momentum came out ~1000x too small).
        const hL0 = eL - bL > 0 ? eL - bL : 0;
        const hR0 = eR - bR > 0 ? eR - bR : 0;
        const fxL0 = flux[0];
        const fxL1 = flux[1] + 0.5 * G * (hL0 * hL0 - hsL * hsL);
        const fxL2 = flux[2];
        const fxR0 = flux[0];
        const fxR1 = flux[1] + 0.5 * G * (hR0 * hR0 - hsR * hsR);
        const fxR2 = flux[2];

        const cx = (dt * geom.dy) / aj;
        etaOut[kL] -= cx * fxL0;
        huOut[kL] -= cx * fxL1;
        hvOut[kL] -= cx * fxL2;
        etaOut[kR] += cx * fxR0;
        huOut[kR] += cx * fxR1;
        hvOut[kR] += cx * fxR2;
      }
    }

    // ---------------- y sweep ----------------

    // South/north DOMAIN EDGES: reflective wall fluxes (v → −v), mirroring
    // the x-edge treatment. Edge length = the boundary row's own cell width.
    for (let i = 0; i < nx; i++) {
      {
        // south wall below row 0
        const k = i;
        const b = this.bed[k];
        const e = etaIn[k];
        let hs = e - b;
        if (hs < 0) hs = 0;
        const wet = hs > H_DRY && e - b > H_DRY;
        const u = wet ? huIn[k] / (e - b) : 0;
        const v = wet ? hvIn[k] / (e - b) : 0;
        hllFlux(hs, -v, u, hs, v, u, flux);
        const h0 = e - b > 0 ? e - b : 0;
        const fw = flux[1] + 0.5 * G * (h0 * h0 - hs * hs);
        const cyS = (dt * geom.dx[0]) / geom.area[0];
        hvOut[k] += cyS * fw;
        huOut[k] += cyS * flux[2];
      }
      {
        // north wall above row ny-1
        const k = (ny - 1) * nx + i;
        const b = this.bed[k];
        const e = etaIn[k];
        let hs = e - b;
        if (hs < 0) hs = 0;
        const wet = hs > H_DRY && e - b > H_DRY;
        const u = wet ? huIn[k] / (e - b) : 0;
        const v = wet ? hvIn[k] / (e - b) : 0;
        hllFlux(hs, v, u, hs, -v, u, flux);
        const h0 = e - b > 0 ? e - b : 0;
        const fw = flux[1] + 0.5 * G * (h0 * h0 - hs * hs);
        const cyN = (dt * geom.dx[ny - 1]) / geom.area[ny - 1];
        hvOut[k] -= cyN * fw;
        huOut[k] -= cyN * flux[2];
      }
    }

    for (let j = 0; j <= ny - 2; j++) {
      const aD = geom.area[j];
      const aU = geom.area[j + 1];
      for (let i = 0; i < nx; i++) {
        const kD = j * nx + i;
        const kU = kD + nx;
        const bD = this.bed[kD];
        const bU = this.bed[kU];
        const bMax = bD > bU ? bD : bU;

        let eD = etaIn[kD];
        let eU = etaIn[kU];
        let pD = huIn[kD];
        let pU = huIn[kU];
        let qD = hvIn[kD];
        let qU = hvIn[kU];
        {
          const hDcell = eD - bD;
          const hUcell = eU - bU;
          const safe =
            second && hDcell > H_MUSCL && hUcell > H_MUSCL &&
            eD - bMax > H_MUSCL && eU - bMax > H_MUSCL;
          if (safe) {
            eD += 0.5 * this.slopeYE[kD];
            pD += 0.5 * this.slopeYU[kD];
            qD += 0.5 * this.slopeYV[kD];
            eU -= 0.5 * this.slopeYE[kU];
            pU -= 0.5 * this.slopeYU[kU];
            qU -= 0.5 * this.slopeYV[kU];
          }
        }

        let hsD = eD - bMax;
        let hsU = eU - bMax;
        if (hsD < 0) hsD = 0;
        if (hsU < 0) hsU = 0;
        let vD = 0, uD = 0, vU = 0, uU = 0;
        const dD = eD - bD;
        const dU = eU - bU;
        if (dD > H_DRY && hsD > 0) {
          vD = (qD * dD) / (dD * dD + H_VEL2);
          uD = (pD * dD) / (dD * dD + H_VEL2);
        }
        if (dU > H_DRY && hsU > 0) {
          vU = (qU * dU) / (dU * dU + H_VEL2);
          uU = (pU * dU) / (dU * dU + H_VEL2);
        }
        // normal direction is y: pass v as the normal velocity.
        hllFlux(hsD, vD, uD, hsU, vU, uU, flux);

        // reconstructed cell-side depths (see the x-sweep note)
        const hD0 = eD - bD > 0 ? eD - bD : 0;
        const hU0 = eU - bU > 0 ? eU - bU : 0;
        const fyD0 = flux[0];
        const fyD1 = flux[2]; // tangential (x) momentum
        const fyD2 = flux[1] + 0.5 * G * (hD0 * hD0 - hsD * hsD);
        const fyU0 = flux[0];
        const fyU1 = flux[2];
        const fyU2 = flux[1] + 0.5 * G * (hU0 * hU0 - hsU * hsU);

        // Constant tangent-plane widths: yEdge == dx == area/dy everywhere,
        // so this is both pairwise conservative and exactly well-balanced.
        etaOut[kD] -= (dt * geom.yEdge[j]) / aD * fyD0;
        huOut[kD] -= (dt * geom.yEdge[j]) / aD * fyD1;
        hvOut[kD] -= (dt * geom.yEdge[j]) / aD * fyD2;
        etaOut[kU] += (dt * geom.yEdge[j]) / aU * fyU0;
        huOut[kU] += (dt * geom.yEdge[j]) / aU * fyU1;
        hvOut[kU] += (dt * geom.yEdge[j]) / aU * fyU2;
      }
    }

    // ------------- friction, wet/dry, sponge -------------
    for (let j = 0; j < ny; j++) {
      for (let i = 0; i < nx; i++) {
        const k = j * nx + i;
        const b = this.bed[k];
        let h = etaOut[k] - b;
        if (h < 0) {
          h = 0;
          etaOut[k] = b;
          this.nNegClamped++;
        }
        if (h <= H_VEL) {
          // momentum zeroed below H_VEL = 1 cm (mass preserved): thin films
          // at wetting fronts otherwise develop unphysical velocity spikes
          // that crash the CFL (verified on the zero-friction sloshing case)
          etaOut[k] = h > 0 ? b + h : b;
          huOut[k] = 0;
          hvOut[k] = 0;
          continue;
        }
        if (this.manningN > 0) {
          const cF = (dt * G * this.manningN * this.manningN) / Math.pow(h, 4 / 3);
          const fric = (uStar: number): number => {
            const a = Math.abs(uStar);
            const u = (-1 + Math.sqrt(1 + 4 * cF * a)) / (2 * cF);
            return uStar < 0 ? -u : u;
          };
          huOut[k] = h * fric(huOut[k] / h);
          hvOut[k] = h * fric(hvOut[k] / h);
        }
        const sf = this.spongeFactor(j, i);
        if (sf > 0) {
          const keep = 1 - sf;
          huOut[k] *= keep;
          hvOut[k] *= keep;
          etaOut[k] = this.eta0[k] + (etaOut[k] - this.eta0[k]) * keep;
          if (etaOut[k] < b) etaOut[k] = b;
        }
      }
    }
  }

  /** Advance up to nSteps steps (or until tEnd). Returns steps taken. */
  advance(nSteps: number, tEnd: number): number {
    let count = 0;
    while (count < nSteps && this.t < tEnd) {
      let dt = this.computeDt();
      if (!Number.isFinite(dt) || dt <= 0) break;
      if (this.t + dt > tEnd) dt = tEnd - this.t;
      if (dt < this.minDt) this.minDt = dt;
      if (dt > this.maxDt) this.maxDt = dt;

      if (this.order === 1) {
        this.eulerStage(dt, this.eta, this.hu, this.hv, this.s1eta, this.s1hu, this.s1hv);
        this.eta.set(this.s1eta);
        this.hu.set(this.s1hu);
        this.hv.set(this.s1hv);
      } else {
        // SSP-RK2 (Heun):  U¹ = E(Uⁿ);  Uⁿ⁺¹ = ½Uⁿ + ½E(U¹).
        // (A previous version formed E(½(Uⁿ+E(Uⁿ))) — an off-by-half-stage
        // combination that advanced every wave ~1.5x too fast; caught by
        // the √(gh) wave-speed benchmark.)
        this.eulerStage(dt, this.eta, this.hu, this.hv, this.s1eta, this.s1hu, this.s1hv);
        this.eulerStage(dt, this.s1eta, this.s1hu, this.s1hv, this.s2eta, this.s2hu, this.s2hv);
        for (let k = 0; k < this.eta.length; k++) {
          this.eta[k] = 0.5 * this.eta[k] + 0.5 * this.s2eta[k];
          this.hu[k] = 0.5 * this.hu[k] + 0.5 * this.s2hu[k];
          this.hv[k] = 0.5 * this.hv[k] + 0.5 * this.s2hv[k];
        }
      }

      this.t += dt;
      this.nSteps++;
      count++;
      this.recordFields(dt);
      if (this.t >= this.nextGaugeT) {
        for (const g of this.gauges) {
          g.tMin.push(this.t / 60);
          g.eta.push(g.idx >= 0 ? this.eta[g.idx] : NaN);
        }
        this.nextGaugeT = this.t + this.gaugeDt;
      }
    }
    return count;
  }

  /** Update the max-|η| / arrival / flow-depth fields at the current time. */
  private recordFields(lastDt: number): void {
    void lastDt;
    for (let k = 0; k < this.maxEta.length; k++) {
      const b = this.bed[k];
      const h = this.eta[k] - b;
      if (h > H_DRY) {
        const a = Math.abs(this.eta[k]);
        if (a > this.maxEta[k]) this.maxEta[k] = a;
        if (Number.isNaN(this.arrival[k]) && a > this.arrivalThreshold) {
          this.arrival[k] = this.t;
        }
        if (b > 0 && h > this.maxFlowDepth[k]) this.maxFlowDepth[k] = h;
        const u = this.hu[k] / h;
        const v = this.hv[k] / h;
        const spd = Math.hypot(u, v);
        if (spd > this.maxVel) this.maxVel = spd;
      }
    }
  }

  /** Finish: build the result. */
  finish(): SolverResult {
    let mass = 0;
    for (let j = 0; j < this.ny; j++) {
      for (let i = 0; i < this.nx; i++) {
        const k = j * this.nx + i;
        const h = this.eta[k] - this.bed[k];
        if (h > 0) mass += h * this.geom.area[j];
      }
    }
    let maxEtaPeak = 0;
    for (let k = 0; k < this.maxEta.length; k++) {
      if (this.maxEta[k] > maxEtaPeak) maxEtaPeak = this.maxEta[k];
    }
    return {
      diagnostics: {
        nSteps: this.nSteps,
        tSim: this.t,
        minDt: this.minDt === Infinity ? 0 : this.minDt,
        maxDt: this.maxDt,
        massInitial: this.mass0,
        massFinal: mass,
        massDriftRelative: this.mass0 > 0 ? Math.abs(mass - this.mass0) / this.mass0 : 0,
        maxEtaPeak,
        maxVelocity: this.maxVel,
        nNegClamped: this.nNegClamped,
      },
      fields: {
        maxEta: this.maxEta,
        arrival: this.arrival,
        maxFlowDepth: this.maxFlowDepth,
      },
      gauges: this.gauges.map((g) => ({
        stationId: g.spec.stationId,
        lat: g.spec.lat,
        lon: g.spec.lon,
        tMin: Float32Array.from(g.tMin),
        eta: Float32Array.from(g.eta),
      })),
    };
  }
}
