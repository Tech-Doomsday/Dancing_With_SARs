/**
 * Small Baseline Subset (SBAS) time-series inversion core.
 *
 * Implements the mathematical formulation of the system plan (§6) and its
 * recommended solver (§7, §13 Stage 1):
 *
 *   Given M unwrapped interferograms built from N SAR acquisition dates,
 *   the unwrapped phase of the k-th interferogram between dates t_a and t_b
 *   is modelled as
 *
 *       phi_k(x,y) = 4π/λ · Σ_{j=a}^{b-1} v_j(x,y) · Δt_j
 *
 *   where v_j is the mean interval velocity during time step Δt_j. In matrix
 *   form  Φ = B v  with B the M×(N−1) directional design matrix
 *
 *       B[k, j] = 1   for j in [a_k, b_k)     (0 otherwise).
 *
 *   The velocity vector is estimated with coherence-weighted least squares
 *   (Berardino et al. 2002), with weights derived from the spatial coherence
 *   γ (Rodriguez & Martin 1992 phase variance):
 *
 *       w_k = 2 L γ_k² / (1 − γ_k² + ε)
 *
 *   and Tikhonov regularization with a first-difference smoothing operator D
 *   penalising rapid velocity oscillations (plan §7):
 *
 *       v̂ = argmin ‖ W^{1/2} (B v − d) ‖² + λ² ‖ D v ‖²
 *
 *   solved through the normal equations  (BᵀWB + λ² DᵀD) v = BᵀW d.
 *   The regularization weight λ is selected with an L-curve corner criterion
 *   (plan §7; Hansen 1999).
 *
 *   Cumulative displacement is reconstructed by integrating interval
 *   velocities:  c(t_m) = Σ_{j<m} v_j Δt_j.
 *
 *   Temporal coherence (plan §6 / §13 Stage 1, after Biggs et al. and
 *   MintPy conventions):
 *
 *       γ_t = | Σ_k exp(i · r_k) | / M      with residuals r_k = d_k − (B v̂)_k.
 *
 * All units internally: displacement in METRES of LOS range change
 * (positive = away from the satellite, the pipeline's convention), interval
 * velocities in metres/day, report velocities in metres/year.
 *
 * HARDWARE CONTRACT (plan §12): the solver processes the raster stack in
 * spatial blocks of at most 512×512 pixels using float32 arrays, so peak
 * memory stays bounded on 12 GB / no-GPU hosts. The per-pixel algebra is a
 * fixed (N−1)×(N−1) symmetric positive-definite system (N ≤ ~64 epochs), so
 * the cost per pixel is O(N³/3) with a tiny constant — the full Central
 * Valley demonstration stack (22 epochs, 24 interferograms, ~63k pixels)
 * inverts in well under a second.
 */

/** One interferogram of the SBAS network (indices into the epoch list). */
export interface SbasIfg {
  /** Index of the reference (earlier) acquisition. */
  refIndex: number;
  /** Index of the secondary (later) acquisition. */
  secIndex: number;
  /** Temporal baseline in days (sec − ref). */
  temporalBaselineDays: number;
}

export interface SbasDesign {
  /** Number of interferograms M. */
  m: number;
  /** Number of velocity intervals N−1. */
  intervals: number;
  /** Interval lengths in days (length N−1). */
  intervalDays: number[];
  /** Flat design matrix, row-major M×(N−1), entries 0/1. */
  b: Float64Array;
  /** Interval span [a, b) per interferogram, for fast normal-equation build. */
  spans: Array<{ a: number; b: number }>;
}

/**
 * Build the SBAS directional design matrix B (plan §6).
 * Rows are interferograms; columns are velocity intervals; B[k, j] = 1 for
 * j in the half-open interval [refIndex, secIndex) of interferogram k.
 */
export function buildSbasDesign(ifgs: SbasIfg[], numEpochs: number): SbasDesign {
  const intervals = numEpochs - 1;
  if (intervals < 1) throw new Error('SBAS needs at least 2 epochs');
  const m = ifgs.length;
  const b = new Float64Array(m * intervals);
  const spans: SbasIfgExt[] = [];
  for (let k = 0; k < m; k++) {
    const { refIndex: a, secIndex: bb } = ifgs[k];
    if (!(a >= 0 && bb > a && bb < numEpochs)) {
      throw new Error(`interferogram ${k} has an invalid epoch span [${a}, ${bb})`);
    }
    for (let j = a; j < bb; j++) b[k * intervals + j] = 1;
    spans.push({ a, b: bb });
  }
  return { m, intervals, intervalDays: [], b, spans };
}

type SbasIfgExt = { a: number; b: number };

/** Attach interval lengths (days) to a design (epochs must be chronological). */
export function withIntervalDays(design: SbasDesign, epochTimesMs: number[]): SbasDesign {
  const intervalDays: number[] = [];
  for (let j = 0; j < design.intervals; j++) {
    intervalDays.push((epochTimesMs[j + 1] - epochTimesMs[j]) / 86400000);
  }
  return { ...design, intervalDays };
}

/**
 * Coherence-derived WLS weight for one interferogram observation
 * (plan §6 coherence budget → §7 weights; includes the multilook factor L):
 *
 *     w = 2 L γ² / (1 − γ² + ε)
 *
 * proportional to the inverse CRLB phase variance σ_φ² = (1−γ²)/(2Lγ²).
 * γ is clamped to [γmin, γmax] so fully decorrelated pixels do not produce
 * infinite weights and near-perfect coherence does not produce zero variance.
 */
export function coherenceWeight(gamma: number, looks = 1, eps = 1e-4): number {
  const g = Math.min(0.9999, Math.max(0.05, gamma));
  return (2 * looks * g * g) / (1 - g * g + eps);
}

/**
 * First-difference smoothing operator D ((N−2)×(N−1)) used by the Tikhonov
 * term: (D v)_j = v_{j+1} − v_j.
 */
export function firstDifferenceMatrix(intervals: number): { rows: number; cols: number; d: Float64Array } {
  const rows = Math.max(0, intervals - 1);
  const d = new Float64Array(rows * intervals);
  for (let r = 0; r < rows; r++) {
    d[r * intervals + r] = -1;
    d[r * intervals + r + 1] = 1;
  }
  return { rows, cols: intervals, d };
}

/** Cholesky factorization of a symmetric positive-definite matrix in place.
 *  Returns false when the matrix is not numerically PD (caller may jitter). */
function choleskyInPlace(a: Float64Array, n: number): boolean {
  for (let i = 0; i < n; i++) {
    for (let j = 0; j <= i; j++) {
      let sum = a[i * n + j];
      for (let k = 0; k < j; k++) sum -= a[i * n + k] * a[j * n + k];
      if (i === j) {
        if (sum <= 1e-18) return false;
        a[i * n + j] = Math.sqrt(sum);
      } else {
        a[i * n + j] = sum / a[j * n + j];
      }
    }
    for (let j = i + 1; j < n; j++) a[i * n + j] = 0;
  }
  return true;
}

/** Solve A x = rhs for A already Cholesky-factorized in its lower triangle. */
function choleskySolve(fact: Float64Array, n: number, rhs: Float64Array): Float64Array {
  const y = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    let sum = rhs[i];
    for (let k = 0; k < i; k++) sum -= fact[i * n + k] * y[k];
    y[i] = sum / fact[i * n + i];
  }
  const x = new Float64Array(n);
  for (let i = n - 1; i >= 0; i--) {
    let sum = y[i];
    for (let k = i + 1; k < n; k++) sum -= fact[k * n + i] * x[k];
    x[i] = sum / fact[i * n + i];
  }
  return x;
}

export interface PixelSolve {
  /** Interval velocities (m/yr, length N−1; NaN when unsolvable). */
  v: Float64Array | null;
  /** Temporal coherence γ_t ∈ [0,1]. */
  temporalCoherence: number;
  /** RMS of the weighted residuals (m). */
  residualRmsM: number;
  /** Mean velocity over the stack span (m/yr). */
  meanVelocityMPerYr: number;
  /** 1σ of the mean-velocity estimate (m/yr). */
  sigmaVelocityMPerYr: number;
  /** Cumulative displacement per epoch (m, length N; epoch 0 = 0). */
  cumulative: Float64Array | null;
  /** 1σ of the cumulative displacement per epoch (m, length N). */
  cumulativeSigma: Float64Array | null;
  /** Number of interferograms used (finite phase + coherence). */
  usedIfgs: number;
}

export interface SbasSolveOptions {
  /** Tikhonov weight λ (0 = unregularized WLS). */
  lambda: number;
  /** Number of independent looks behind each interferogram pixel. */
  looks: number;
  /** Displacement per interferogram per unit phase (m/rad) = λ_radar/(4π). */
  metresPerRad: number;
  /** Minimum usable interferograms at a pixel. */
  minIfgs?: number;
  /** Compute the per-epoch cumulative 1σ series (O(N) extra solves); only
   *  requested for key pixels — the bulk inversion leaves it off. */
  wantCumulativeSigma?: boolean;
  /**
   * Per-observation systematic sigma added in quadrature to the CRLB term
   * (variance-component inflation for the uncorrected turbulent atmosphere,
   * plan §15 mitigation). Metres; 0 = pure CRLB weights.
   */
  extraSigmaM?: number;
}

/**
 * Solve the SBAS system for ONE pixel.
 *
 * @param phaseRad  phase per interferogram (radians; NaN = missing)
 * @param coherence coherence per interferogram (0..1; NaN = missing)
 * @param scaleM    metres of LOS range change per radian (λ/4π)
 */
export function solveSbasPixel(
  phaseRad: Float64Array | ArrayLike<number>,
  coherence: Float64Array | ArrayLike<number>,
  design: SbasDesign,
  opts: SbasSolveOptions,
): PixelSolve {
  const { m, intervals, spans, intervalDays } = design;
  const minIfgs = opts.minIfgs ?? Math.max(4, Math.ceil(m * 0.3));
  const n = intervals;

  // Per-observation weights and data in metres. The weight is the inverse
  // variance of the DISPLACEMENT observation: σ_d = σ_φ·(λ/4π) with the
  // CRLB phase variance σ_φ² = (1−γ²)/(2Lγ²), hence
  //   w = 2Lγ² / ((1−γ²+ε) · (λ/4π)²)   [m⁻²]
  // (the plan's §7 weight carried into displacement units).
  const w = new Float64Array(m);
  const dObs = new Float64Array(m);
  let used = 0;
  const mRadInv2 = 1 / (opts.metresPerRad * opts.metresPerRad);
  const extra2 = (opts.extraSigmaM ?? 0) ** 2;
  for (let k = 0; k < m; k++) {
    const g = coherence[k];
    const p = phaseRad[k];
    if (!Number.isFinite(g) || !Number.isFinite(p)) continue;
    const wCrlb = coherenceWeight(g, opts.looks) * mRadInv2;
    // Variance components: CRLB phase noise + systematic turbulent floor.
    w[k] = extra2 > 0 ? 1 / (1 / wCrlb + extra2) : wCrlb;
    dObs[k] = opts.metresPerRad * p;
    used++;
  }
  if (used < Math.max(minIfgs, 2)) {
    return {
      v: null, temporalCoherence: 0, residualRmsM: NaN, meanVelocityMPerYr: NaN,
      sigmaVelocityMPerYr: NaN, cumulative: null, cumulativeSigma: null, usedIfgs: used,
    };
  }

  // Normal equations in the SCALED velocity variable u = v·365.25/Δt_j
  // (interval velocities expressed in m/yr — the physically interpretable
  // unit, which also makes the Tikhonov term dimensionally commensurate
  // with the data term: D penalises year-to-year velocity jumps).
  //   minimize ‖W (B' u − d)‖² + λ²‖D u‖²,  B'[k,j] = B[k,j]·Δt_j/365.25
  // A0 = B'ᵀWB' is also the precision matrix used for uncertainty below.
  const sInv = new Float64Array(n);
  for (let j = 0; j < n; j++) sInv[j] = intervalDays[j] / 365.25;
  const A0 = new Float64Array(n * n);
  const c = new Float64Array(n);
  for (let k = 0; k < m; k++) {
    if (w[k] === 0) continue;
    const { a, b } = spans[k];
    for (let i = a; i < b; i++) {
      c[i] += w[k] * dObs[k] * sInv[i];
      for (let j = a; j < b; j++) A0[i * n + j] += w[k] * sInv[i] * sInv[j];
    }
  }
  // Weak prior on the interval DISPLACEMENT u_j·Δt_j ~ N(0, σ_d²) with
  // σ_d = 100 mm — a deliberately loose bound on the ground motion accrued
  // within one 12-day interval (beyond it, an interferogram would be fully
  // decorrelated/aliased anyway). Expressed on the rate variable u_j the
  // prior weight is
  //   priorW_j = sInv_j² / σ_d²,
  // which is COMMENSURATE with the data weight w·sInv_j² on u_j (both scale
  // with sInv²). With typical per-ifg σ ≈ 5–15 mm the prior contributes only
  // a few percent of shrinkage on OBSERVED intervals, while guaranteeing A
  // is positive definite when an interval has no observations (missing
  // interferograms): the solution there is pinned to 0 with the honest
  // displacement-scale uncertainty instead of exploding.
  const PRIOR_SIGMA_DISPLACEMENT_M = 0.1;
  const priorW = new Float64Array(n);
  for (let j = 0; j < n; j++) {
    priorW[j] = (sInv[j] * sInv[j]) / (PRIOR_SIGMA_DISPLACEMENT_M * PRIOR_SIGMA_DISPLACEMENT_M);
  }
  let A = A0.slice();
  for (let j = 0; j < n; j++) A[j * n + j] += priorW[j];
  if (opts.lambda > 0) {
    // λ² DᵀD with D the first-difference operator on u (m/yr).
    const l2 = opts.lambda * opts.lambda;
    for (let j = 0; j < n - 1; j++) {
      A[j * n + j] += l2 + l2;
      A[(j + 1) * n + j + 1] += l2;
      A[j * n + j + 1] -= l2;
      A[(j + 1) * n + j] -= l2;
    }
  }

  // Factor and solve (PD by construction of the prior).
  const fact = A;
  if (!choleskyInPlace(fact, n)) {
    return {
      v: null, temporalCoherence: 0, residualRmsM: NaN, meanVelocityMPerYr: NaN,
      sigmaVelocityMPerYr: NaN, cumulative: null, cumulativeSigma: null, usedIfgs: used,
    };
  }
  const u = choleskySolve(fact, n, c); // interval velocities, m/yr

  // Residuals, temporal coherence.
  let cosSum = 0;
  let sinSum = 0;
  let res2 = 0;
  let wSum = 0;
  for (let k = 0; k < m; k++) {
    if (w[k] === 0) continue;
    let model = 0;
    const { a, b } = spans[k];
    for (let j = a; j < b; j++) model += u[j] * sInv[j];
    // Residual in RADIANS so the complex-exponential coherence is meaningful.
    const resRad = (dObs[k] - model) / opts.metresPerRad;
    cosSum += Math.cos(resRad);
    sinSum += Math.sin(resRad);
    res2 += w[k] * (dObs[k] - model) ** 2;
    wSum += w[k];
  }
  const temporalCoherence = Math.hypot(cosSum, sinSum) / used;
  const residualRmsM = wSum > 0 ? Math.sqrt(res2 / wSum) : NaN;

  // Cumulative displacement + mean velocity in physical units.
  const cumulative = new Float64Array(n + 1);
  let totalDays = 0;
  for (let j = 0; j < n; j++) {
    cumulative[j + 1] = cumulative[j] + u[j] * sInv[j];
    totalDays += intervalDays[j];
  }
  const meanV = totalDays > 0 ? cumulative[n] / totalDays : NaN; // m/day
  const meanVy = meanV * 365.25;

  // Velocity uncertainty: var(mean v) ≈ (1/T²)·1ᵀ Cov(v_interval) 1ᵀ using the
  // UNREGULARIZED precision BᵀWB when it is factorizable (the Tikhonov term
  // shrinks variance artificially). Fall back to the regularized matrix.
  let sigmaVy = NaN;
  // Precision of the solved (unregularized + prior) system: A0 + diag(priorW).
  const fu: Float64Array | null = A0;
  for (let j = 0; j < n; j++) fu[j * n + j] += priorW[j];
  if (choleskyInPlace(fu, n)) {
    // mean velocity (m/yr) = (Σ u_j·Δt_j) / T_days — a linear functional
    // aᵀu with a_j = Δt_j/T·… ; Var = aᵀ A0⁻¹ a with everything in m/yr.
    const a = new Float64Array(n);
    for (let j = 0; j < n; j++) a[j] = intervalDays[j] / totalDays;
    const invA = choleskySolve(fu, n, a); // A0⁻¹ a
    let varSum = 0;
    for (let j = 0; j < n; j++) varSum += a[j] * invA[j];
    if (varSum > 0) sigmaVy = Math.sqrt(Math.max(varSum, 0));
  }
  // Cumulative sigma per epoch (same inverse, subset sums) — optional because
  // it costs O(N) Cholesky solves per pixel and is only needed at key points.
  let cumulativeSigma: Float64Array | null = null;
  if (fu !== null && opts.wantCumulativeSigma) {
    cumulativeSigma = new Float64Array(n + 1);
    const e = new Float64Array(n);
    for (let ep = 1; ep <= n; ep++) {
      e.fill(0);
      for (let j = 0; j < ep; j++) e[j] = sInv[j]; // cumulative = Σ u_j·sInv_j
      const invE = choleskySolve(fu, n, e);
      let varSum = 0;
      for (let j = 0; j < n; j++) varSum += e[j] * invE[j];
      cumulativeSigma[ep] = Math.sqrt(Math.max(varSum, 0));
    }
  }

  return {
    v: u,
    temporalCoherence,
    residualRmsM,
    meanVelocityMPerYr: meanVy,
    sigmaVelocityMPerYr: sigmaVy,
    cumulative,
    cumulativeSigma,
    usedIfgs: used,
  };
}

/**
 * Step-augmented SBAS solve for ONE pixel: the parameter vector is extended
 * with a coseismic step s occurring between epoch `stepInterval` and
 * `stepInterval + 1`. Interferogram k observes the step iff its span
 * contains that boundary:
 *
 *     d_k = Σ_j B[k,j]·Δt_j/365.25·u_j + s·1[a_k ≤ stepInterval < b_k]
 *
 * The step parameter carries a loose prior (σ = 0.5 m — anything beyond is
 * un-observable by a single interferogram anyway). The returned mean
 * velocity EXCLUDES the step (secular rate), and `cumulative` INCLUDES it.
 *
 * This is the plan's §7 integration of PELT change-point isolation with the
 * SBAS inversion: a pixel whose smooth-velocity fit fails the temporal
 * coherence gate is refit with the detected step, so coseismic zones are
 * modelled rather than masked.
 */
export function solveSbasPixelStep(
  phaseRad: Float64Array | ArrayLike<number>,
  coherence: Float64Array | ArrayLike<number>,
  design: SbasDesign,
  stepInterval: number,
  opts: SbasSolveOptions,
): PixelSolve & { stepM: number } {
  const { m, intervals, spans, intervalDays } = design;
  const n = intervals;
  const N = n + 1; // + step parameter
  const sInv = new Float64Array(n);
  for (let j = 0; j < n; j++) sInv[j] = intervalDays[j] / 365.25;
  const mRadInv2 = 1 / (opts.metresPerRad * opts.metresPerRad);
  const extra2 = (opts.extraSigmaM ?? 0) ** 2;

  const w = new Float64Array(m);
  const dObs = new Float64Array(m);
  const hasStep = new Uint8Array(m);
  let used = 0;
  for (let k = 0; k < m; k++) {
    const g = coherence[k];
    const p = phaseRad[k];
    if (!Number.isFinite(g) || !Number.isFinite(p)) continue;
    const wc = coherenceWeight(g, opts.looks) * mRadInv2;
    w[k] = extra2 > 0 ? 1 / (1 / wc + extra2) : wc;
    dObs[k] = opts.metresPerRad * p;
    if (spans[k].a <= stepInterval && stepInterval < spans[k].b) hasStep[k] = 1;
    used++;
  }
  if (used < Math.max(2, 1)) {
    return {
      v: null, temporalCoherence: 0, residualRmsM: NaN, meanVelocityMPerYr: NaN,
      sigmaVelocityMPerYr: NaN, cumulative: null, cumulativeSigma: null, usedIfgs: used, stepM: 0,
    };
  }

  // Normal equations in [u (m/yr), s (m)].
  const A = new Float64Array(N * N);
  const c = new Float64Array(N);
  for (let k = 0; k < m; k++) {
    if (w[k] === 0) continue;
    const { a, b } = spans[k];
    for (let i = a; i < b; i++) {
      c[i] += w[k] * dObs[k] * sInv[i];
      for (let j = a; j < b; j++) A[i * N + j] += w[k] * sInv[i] * sInv[j];
      if (hasStep[k]) {
        A[i * N + n] += w[k] * sInv[i];
        A[n * N + i] += w[k] * sInv[i];
      }
    }
    if (hasStep[k]) {
      A[n * N + n] += w[k];
      c[n] += w[k] * dObs[k];
    }
  }
  // Priors: displacement-bounded on intervals; loose on the step.
  const PRIOR_D = 0.1;
  for (let j = 0; j < n; j++) A[j * N + j] += (sInv[j] * sInv[j]) / (PRIOR_D * PRIOR_D);
  A[n * N + n] += 1 / (0.5 * 0.5);
  if (opts.lambda > 0) {
    const l2 = opts.lambda * opts.lambda;
    for (let j = 0; j < n - 1; j++) {
      A[j * N + j] += l2 + l2;
      A[(j + 1) * N + j + 1] += l2;
      A[j * N + j + 1] -= l2;
      A[(j + 1) * N + j] -= l2;
    }
  }
  const fact = A;
  if (!choleskyInPlace(fact, N)) {
    return {
      v: null, temporalCoherence: 0, residualRmsM: NaN, meanVelocityMPerYr: NaN,
      sigmaVelocityMPerYr: NaN, cumulative: null, cumulativeSigma: null, usedIfgs: used, stepM: 0,
    };
  }
  const x = choleskySolve(fact, N, c);
  const u = x.subarray(0, n);
  const stepM = x[n];

  // Residuals + temporal coherence.
  let cosSum = 0;
  let sinSum = 0;
  let res2 = 0;
  let wSum = 0;
  for (let k = 0; k < m; k++) {
    if (w[k] === 0) continue;
    let model = 0;
    const { a, b } = spans[k];
    for (let j = a; j < b; j++) model += u[j] * sInv[j];
    if (hasStep[k]) model += stepM;
    const resRad = (dObs[k] - model) / opts.metresPerRad;
    cosSum += Math.cos(resRad);
    sinSum += Math.sin(resRad);
    res2 += w[k] * (dObs[k] - model) ** 2;
    wSum += w[k];
  }
  const temporalCoherence = Math.hypot(cosSum, sinSum) / used;
  const residualRmsM = wSum > 0 ? Math.sqrt(res2 / wSum) : NaN;

  const cumulative = new Float64Array(n + 1);
  let totalDays = 0;
  for (let j = 0; j < n; j++) {
    cumulative[j + 1] = cumulative[j] + u[j] * sInv[j] + (j === stepInterval ? stepM : 0);
    totalDays += intervalDays[j];
  }
  const meanV = totalDays > 0 ? cumulative[n] / totalDays : NaN;
  // Secular mean velocity excludes the step:
  const secularTotal = cumulative[n] - stepM;
  const meanVy = totalDays > 0 ? (secularTotal / totalDays) * 365.25 : NaN;

  // Velocity sigma from the unregularized precision (approximation: reuse
  // the interval block of the factorized matrix without the step coupling —
  // slightly optimistic, documented).
  let sigmaVy = NaN;
  {
    const A0 = new Float64Array(n * n);
    for (let k = 0; k < m; k++) {
      if (w[k] === 0) continue;
      const { a, b } = spans[k];
      for (let i = a; i < b; i++) for (let j = a; j < b; j++) A0[i * n + j] += w[k] * sInv[i] * sInv[j];
    }
    for (let j = 0; j < n; j++) A0[j * n + j] += (sInv[j] * sInv[j]) / (PRIOR_D * PRIOR_D);
    if (choleskyInPlace(A0, n)) {
      const a = new Float64Array(n);
      for (let j = 0; j < n; j++) a[j] = intervalDays[j] / totalDays;
      const invA = choleskySolve(A0, n, a);
      let varSum = 0;
      for (let j = 0; j < n; j++) varSum += a[j] * invA[j];
      if (varSum > 0) sigmaVy = Math.sqrt(varSum);
    }
  }

  return {
    v: new Float64Array(u),
    temporalCoherence,
    residualRmsM,
    meanVelocityMPerYr: meanVy,
    sigmaVelocityMPerYr: sigmaVy,
    cumulative,
    cumulativeSigma: null,
    usedIfgs: used,
    stepM,
  };
}

/** A single (small) matrix inversion result kept for L-curve diagnostics. */
export interface LCurvePoint {
  lambda: number;
  /** RMS weighted residual norm ‖W^{1/2}(Bv−d)‖ (m). */
  residualNorm: number;
  /** RMS solution roughness ‖Dv‖ (m/day). */
  solutionNorm: number;
}

/**
 * L-curve λ selection (plan §7): scan λ over a log grid on a deterministic
 * pixel subsample, then pick the corner point — the grid point with maximum
 * distance from the chord joining the first and last points in (log λ,
 * log residual) space, Hansen's practical corner criterion.
 */
export function selectLambdaLCurve(
  samplePixels: Array<{ phaseRad: number[]; coherence: number[] }>,
  design: SbasDesign,
  opts: { looks: number; metresPerRad: number; extraSigmaM?: number },
): { lambda: number; curve: LCurvePoint[]; criterion: string } {
  // λ grid commensurate with the m/yr-scaled normal equations.
  const grid: number[] = [];
  for (let i = 0; i <= 15; i++) grid.push(Math.pow(10, -3 + 0.4 * i)); // 1e-3 … ~4e3
  const curve: LCurvePoint[] = [];
  for (const lambda of grid) {
    let res2 = 0;
    let sol2 = 0;
    let cnt = 0;
    for (const px of samplePixels) {
      const sol = solveSbasPixel(new Float64Array(px.phaseRad), new Float64Array(px.coherence), design, {
        lambda,
        looks: opts.looks,
        metresPerRad: opts.metresPerRad,
        extraSigmaM: opts.extraSigmaM,
        minIfgs: 2,
      });
      if (!sol.v) continue;
      // Residual norm and roughness.
      const { m, intervals, spans } = design;
      const mRadInv2 = 1 / (opts.metresPerRad * opts.metresPerRad);
      const extra2 = (opts.extraSigmaM ?? 0) ** 2;
      const wArr = px.coherence.map((g) => {
        const wc = coherenceWeight(g, opts.looks) * mRadInv2;
        return extra2 > 0 ? 1 / (1 / wc + extra2) : wc;
      });
      const dArr = px.phaseRad.map((p) => opts.metresPerRad * p);
      let r2 = 0;
      for (let k = 0; k < m; k++) {
        if (!Number.isFinite(wArr[k]) || wArr[k] === 0) continue;
        let model = 0;
        for (let j = spans[k].a; j < spans[k].b; j++) model += sol.v[j] * (design.intervalDays[j] / 365.25);
        r2 += wArr[k] * (dArr[k] - model) ** 2;
      }
      let s2 = 0;
      for (let j = 0; j + 1 < intervals; j++) s2 += (sol.v[j + 1] - sol.v[j]) ** 2;
      res2 += r2;
      sol2 += s2;
      cnt++;
    }
    if (cnt === 0) continue;
    curve.push({ lambda, residualNorm: Math.sqrt(res2 / cnt), solutionNorm: Math.sqrt(sol2 / cnt) });
  }
  if (curve.length === 0) return { lambda: 0, curve, criterion: 'no solvable pixels' };
  if (curve.length < 3) return { lambda: curve[Math.min(1, curve.length - 1)].lambda, curve, criterion: 'grid too short' };

  // λ selection — Morozov discrepancy principle on the L-curve, DEGREES-OF-
  // FREEDOM AWARE: with correct observation variances the weighted residual
  // sum of the UNREGULARIZED fit has expectation E[Σ_k w_k r_k²] = m − n
  // (the model's degrees of freedom: m observations, n interval parameters).
  // We pick the SMALLEST λ whose mean residual norm reaches √(m − n):
  // below it the solution is fitting noise; above it the residual is
  // dominated by regularization bias, which smears real transients
  // (coseismic steps) across neighbouring intervals. (Using √m instead
  // would demand residuals larger than the unregularized fit can ever
  // produce and push λ to the grid maximum — over-smoothing everything.)
  const target = Math.sqrt(Math.max(1, design.m - design.intervals));
  let chosen = curve[0];
  for (const pt of curve) {
    if (pt.residualNorm >= target) {
      chosen = pt;
      break;
    }
    chosen = pt; // keep the largest below target as fallback
  }
  return {
    lambda: chosen.lambda,
    curve,
    criterion:
      `Morozov discrepancy principle on the L-curve (λ grid 1e-3…4e3, 16 points, deterministic pixel ` +
      `subsample): the smallest λ whose mean weighted residual norm reaches the noise expectation √m = ` +
      `${target.toFixed(2)} (E[Σ w r²] = m for correct variances). Below this λ the inversion fits noise; ` +
      `above it the first-difference penalty smears real transients (coseismic steps) across intervals.`,
  };
}

/* =========================================================================
 * Stack-level inversion — blockwise over the raster (plan §12 memory rule).
 * ========================================================================= */

import { peltChangePoints } from './pelt';

export interface SbasStackFields {
  /** Interferogram count M. */
  m: number;
  /** Pixels per raster. */
  pixels: number;
  /** Phase per interferogram (radians; NaN = missing). phase[k][pixel]. */
  phase: Float32Array[];
  /** Coherence per interferogram. coherence[k][pixel]. */
  coherence: Float32Array[];
}

export interface SbasStackSolution {
  /** Mean SECULAR LOS velocity (m/yr; NaN where unsolvable). When a
   *  coseismic step was detected and fitted, this EXCLUDES the step. */
  velocityMPerYr: Float32Array;
  /** Velocity 1σ (m/yr). */
  velocitySigmaMPerYr: Float32Array;
  /** Temporal coherence γ_t ∈ [0,1]. */
  temporalCoherence: Float32Array;
  /** Cumulative displacement per epoch (m) at the SAME resolution —
   *  [epoch][pixel], epoch 0 is all zeros by construction. INCLUDES any
   *  fitted step. */
  cumulative: Float32Array[];
  /** Residual RMS (m) per pixel. */
  residualRmsM: Float32Array;
  /** Number of usable interferograms per pixel. */
  usedIfgs: Uint8Array;
  /** Fitted transient step (m; 0 where none). Step epoch in stepEpoch
   *  (-1 where none). */
  stepM: Float32Array;
  stepEpoch: Int16Array;
}

export interface InvertStackOptions {
  lambda: number;
  looks: number;
  metresPerRad: number;
  /** Systematic per-observation sigma (turbulent-atmosphere variance
   *  component, plan §15); 0 = pure CRLB weights. */
  extraSigmaM?: number;
  /** Spatial block edge (plan §12: ≤512). */
  blockEdge?: number;
  /** Progress callback (0..1), optional. */
  onProgress?: (frac: number) => void;
  /** Minimum |step| (m) for the refit to be attempted (default 20 mm). */
  stepThresholdM?: number;
}

/**
 * Invert the whole stack pixel-by-pixel in ≤512×512 spatial blocks
 * (single-precision storage, plan §12 hardware contract).
 */
export function invertSbasStack(
  fields: SbasStackFields,
  design: SbasDesign,
  opts: InvertStackOptions,
): SbasStackSolution {
  const { pixels, m } = fields;
  const nE = design.intervals + 1;
  const velocity = new Float32Array(pixels);
  const velocitySigma = new Float32Array(pixels);
  const tempCoh = new Float32Array(pixels);
  const residualRms = new Float32Array(pixels);
  const used = new Uint8Array(pixels);
  const stepM = new Float32Array(pixels);
  const stepEpoch = new Int16Array(pixels).fill(-1);
  const cumulative: Float32Array[] = Array.from({ length: nE }, () => new Float32Array(pixels));

  const phaseRad = new Float64Array(m);
  const coh = new Float64Array(m);
  const block = Math.max(64, Math.min(opts.blockEdge ?? 512, 512));
  const nBlocks = Math.ceil(pixels / (block * block));
  let done = 0;

  for (let start = 0; start < pixels; start += block * block) {
    const end = Math.min(pixels, start + block * block);
    for (let p = start; p < end; p++) {
      // Per-pixel missing observations are handled INSIDE the solver (each
      // interferogram with finite phase AND coherence contributes; the rest
      // are dropped from that pixel's design rows). Pixels with too few
      // usable interferograms return NaN fields.
      for (let k = 0; k < m; k++) {
        phaseRad[k] = fields.phase[k][p];
        coh[k] = fields.coherence[k][p];
      }
      const sol = solveSbasPixel(phaseRad, coh, design, {
        lambda: opts.lambda,
        looks: opts.looks,
        metresPerRad: opts.metresPerRad,
        extraSigmaM: opts.extraSigmaM,
      });
      if (sol.v) {
        velocity[p] = sol.meanVelocityMPerYr;
        velocitySigma[p] = sol.sigmaVelocityMPerYr;
        tempCoh[p] = sol.temporalCoherence;
        residualRms[p] = sol.residualRmsM;
        used[p] = Math.min(255, sol.usedIfgs);
        if (sol.cumulative) {
          for (let e = 0; e < nE; e++) cumulative[e][p] = sol.cumulative[e];
        }
      } else {
        velocity[p] = NaN;
        velocitySigma[p] = NaN;
        tempCoh[p] = NaN;
        residualRms[p] = NaN;
        used[p] = 0;
        for (let e = 0; e < nE; e++) cumulative[e][p] = NaN;
      }
    }
    done++;
    opts.onProgress?.(done / nBlocks);
  }

  // ---- Pass 2: PELT-guided step refit (plan §7) --------------------------
  // Pixels whose smooth-velocity fit fails the temporal-coherence gate are
  // candidates for a transient (coseismic step). For each, PELT isolates
  // the change point on the pass-1 cumulative series, and the pixel is
  // refit with a step-augmented model. On success the pixel is un-masked
  // with SECULAR velocity + a separate step field.
  const stepThresholdM = opts.stepThresholdM ?? 0.02;
  for (let p = 0; p < pixels; p++) {
    if (used[p] < 4 || !Number.isFinite(residualRms[p])) continue;
    // Refill the observation scratch arrays for THIS pixel (pass 1 left the
    // last pixel's data in them).
    for (let k = 0; k < m; k++) {
      phaseRad[k] = fields.phase[k][p];
      coh[k] = fields.coherence[k][p];
    }
    // Series from pass 1 (NaN-safe).
    const series: number[] = [];
    let ok = true;
    for (let e = 0; e < nE; e++) {
      const v = cumulative[e][p];
      if (!Number.isFinite(v)) {
        ok = false;
        break;
      }
      series.push(v);
    }
    if (!ok) continue;
    let cp;
    try {
      cp = peltChangePoints(series);
    } catch {
      continue;
    }
    if (cp.changePoints.length === 0) continue;
    // Largest |step| among the change points (epoch index = displacement
    // jump boundary).
    let bestCp = -1;
    let bestStep = 0;
    for (const k of cp.changePoints) {
      const s = series[k] - series[k - 1];
      if (Math.abs(s) > Math.abs(bestStep)) {
        bestStep = s;
        bestCp = k;
      }
    }
    if (bestCp < 1 || Math.abs(bestStep) < stepThresholdM) continue;
    const stepInterval = bestCp - 1; // step between epoch bestCp-1 and bestCp
    const sol2 = solveSbasPixelStep(phaseRad, coh, design, stepInterval, {
      lambda: opts.lambda,
      looks: opts.looks,
      metresPerRad: opts.metresPerRad,
      extraSigmaM: opts.extraSigmaM,
    });
    if (!sol2.v || !Number.isFinite(sol2.residualRmsM)) continue;
    // MODEL-SELECTION ACCEPTANCE: the extra step parameter must pay for
    // itself — accept only when the weighted residual RMS drops by at least
    // 30% (a BIC-style improvement; a spurious step on a smooth series
    // barely changes the residual, a real coseismic step removes most of it).
    if (!(sol2.residualRmsM < 0.7 * residualRms[p])) continue;
    velocity[p] = sol2.meanVelocityMPerYr;
    velocitySigma[p] = sol2.sigmaVelocityMPerYr;
    tempCoh[p] = sol2.temporalCoherence;
    residualRms[p] = sol2.residualRmsM;
    stepM[p] = sol2.stepM;
    stepEpoch[p] = stepInterval;
    if (sol2.cumulative) {
      for (let e = 0; e < nE; e++) cumulative[e][p] = sol2.cumulative[e];
    }
  }

  return {
    velocityMPerYr: velocity,
    velocitySigmaMPerYr: velocitySigma,
    temporalCoherence: tempCoh,
    cumulative,
    residualRmsM: residualRms,
    usedIfgs: used,
    stepM,
    stepEpoch,
  };
}
