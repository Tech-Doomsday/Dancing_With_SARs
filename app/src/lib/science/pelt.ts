/**
 * Change-point detection in displacement time series using the Pruned Exact
 * Linear Time (PELT) framework (plan §7; Killick, Fearnhead & Eckley 2012).
 *
 * The plan isolates TRANSIENT change points — "sudden coseismic ruptures or
 * landslide accelerations" — within displacement time series. We therefore
 * run the mean-shift recursion on the FIRST-DIFFERENCE (epoch-to-epoch
 * velocity) series:
 *
 *   • a secular velocity (linear displacement trend) becomes a CONSTANT —
 *     no spurious change points from the trend itself;
 *   • a coseismic step becomes a single large spike, isolated as its own
 *     segment;
 *   • a gradual acceleration becomes a sustained mean shift.
 *
 * The penalized objective is
 *
 *     Σ_{segments} [ C(Δy_{τ+1:t}) + β ],   C = (n/2)·log(2πσ²) + SS/(2σ²)
 *
 * with the KNOWN-VARIANCE Gaussian cost (σ² estimated robustly from the
 * second differences of the series: σ̂ = 1.4826·MAD(Δ²y)/√6, which is
 * step- and trend-robust) and the BIC penalty β = log(n) for the one extra
 * mean parameter per segment — the plan's "penalty parameter preventing
 * over-segmentation". An unknown-variance cost is deliberately avoided: it
 * is unbounded below for constant segments and pathologically rewards
 * over-segmentation.
 *
 * IMPLEMENTATION NOTE (scientific honesty): the PELT *pruning* step
 * (Killick et al. 2012, eq. (5)) is provably valid only for segment costs
 * satisfying their condition (1). The known-variance Gaussian cost does not
 * satisfy it in general, so pruning could discard candidates that later
 * become optimal; this implementation therefore runs the equivalent EXACT
 * optimal-partitioning recursion (Jackson et al. 2005), which returns the
 * same global optimum PELT targets, in O(n²) — trivial for satellite time
 * series (≤ ~64 epochs). The `pruned` field is retained for interface
 * compatibility and always reports 0.
 */

export interface PeltResult {
  /**
   * Change points as EPOCH INDICES i (1 ≤ i < n): a transient occurs
   * between epoch i−1 and epoch i (the displacement jump is y[i] − y[i−1]).
   */
  changePoints: number[];
  /** Segment start indices in the DIFFERENCE series (0 and changePoints). */
  segmentStarts: number[];
  /** Mean epoch-to-epoch velocity of each difference-segment. */
  segmentMeans: number[];
  /** Penalty actually used. */
  penalty: number;
  /** Number of candidates pruned (always 0 — see module note). */
  pruned: number;
}

/** Known-variance Gaussian segment cost of d[lo..hi) (hi exclusive). */
function segmentCost(d: number[], lo: number, hi: number, sigma2: number): number {
  const n = hi - lo;
  if (n <= 0) return 0;
  let s = 0;
  for (let i = lo; i < hi; i++) s += d[i];
  const mean = s / n;
  let ss = 0;
  for (let i = lo; i < hi; i++) ss += (d[i] - mean) ** 2;
  return (n / 2) * Math.log(2 * Math.PI * sigma2) + ss / (2 * sigma2);
}

export interface PeltOptions {
  /**
   * Penalty β, in negative-log-likelihood units. Default: BIC for the one
   * extra mean parameter per additional segment: β = log(n) — the plan's
   * "penalty parameter preventing over-segmentation" (§7).
   */
  penalty?: number;
  /** Known noise variance σ² of the DIFFERENCE series; default = robust
   *  second-difference estimate (see module doc). */
  sigma2?: number;
}

/**
 * Detect transient change points in a displacement series.
 * NaNs are not permitted (interpolate or drop them first).
 */
export function peltChangePoints(series: number[], opts: PeltOptions = {}): PeltResult {
  const n = series.length;
  if (n < 4) {
    return { changePoints: [], segmentStarts: [0], segmentMeans: [], penalty: opts.penalty ?? 0, pruned: 0 };
  }
  for (let i = 0; i < n; i++) {
    if (!Number.isFinite(series[i])) {
      throw new Error(`peltChangePoints: series[${i}] is not finite (interpolate or drop NaNs first)`);
    }
  }

  // Difference series (epoch-to-epoch velocities).
  const d: number[] = [];
  for (let i = 1; i < n; i++) d.push(series[i] - series[i - 1]);
  const m = d.length;

  // Robust noise variance of the difference series from second differences:
  // for white y-noise σ², var(Δd) = 6σ² → σ̂ = 1.4826·MAD(Δd)/√6.
  // A mild 1.2× guard against over-segmentation is applied (documented
  // tuning; sensitivity is covered by unit tests).
  const dd: number[] = [];
  for (let i = 1; i < m; i++) dd.push(d[i] - d[i - 1]);
  dd.sort((a, b) => Math.abs(a) - Math.abs(b));
  const mad = Math.abs(dd[Math.floor(dd.length / 2)]);
  const sigma = 1.4826 * (mad / Math.sqrt(6)) * 1.2;
  const sigma2 = opts.sigma2 ?? Math.max(sigma * sigma, 1e-18);
  const penalty = opts.penalty ?? Math.log(m);

  // Exact optimal-partitioning recursion (the PELT objective):
  //   F(t) = min_{0 ≤ τ < t} [ F(τ) + C(d_{τ+1:t}) + β ],  F(0) = −β.
  const F = new Float64Array(m + 1);
  const cp = new Int32Array(m + 1).fill(-1);
  F[0] = -penalty;
  for (let t = 1; t <= m; t++) {
    let best = Infinity;
    let bestTau = 0;
    for (let tau = 0; tau < t; tau++) {
      const cost = F[tau] + segmentCost(d, tau, t, sigma2) + penalty;
      if (cost < best) {
        best = cost;
        bestTau = tau;
      }
    }
    F[t] = best;
    cp[t] = bestTau === 0 ? -1 : bestTau;
  }

  // Backtrack boundaries in the DIFFERENCE series.
  const rawCps: number[] = [];
  let t = m;
  while (t > 0) {
    const tau = cp[t];
    if (tau > 0) rawCps.push(tau);
    t = tau > 0 ? tau : 0;
  }
  rawCps.reverse();

  // Map to displacement-epoch indices: a boundary at diff-index k means the
  // velocity changes between epoch k and k+1 → transient reported at epoch
  // index k+1 (the jump y[k+1] − y[k]).
  const changePoints = rawCps.map((k) => k + 1);
  const starts = [0, ...rawCps];
  const means = starts.map((s, i) => meanOf(d, s, i + 1 < starts.length ? starts[i + 1] : m));
  return { changePoints, segmentStarts: starts, segmentMeans: means, penalty, pruned: 0 };
}

function meanOf(y: number[], lo: number, hi: number): number {
  if (hi <= lo) return NaN;
  let s = 0;
  for (let i = lo; i < hi; i++) s += y[i];
  return s / (hi - lo);
}

/**
 * Temporal-linearity feature for the Bayesian engine (plan §8 Stage 3):
 * the fraction of the series' variance explained by the best linear trend
 * (secular velocity). A steadily subsiding aquifer → ≈ 1; a coseismic step
 * buried in a flat series → ≈ 0.
 */
export function linearTrendR2(series: number[], times: number[]): number {
  const n = Math.min(series.length, times.length);
  if (n < 3) return NaN;
  let sx = 0, sy = 0, sxx = 0, sxy = 0;
  for (let i = 0; i < n; i++) {
    sx += times[i];
    sy += series[i];
    sxx += times[i] * times[i];
    sxy += times[i] * series[i];
  }
  const den = n * sxx - sx * sx;
  if (den === 0) return NaN;
  const slope = (n * sxy - sx * sy) / den;
  const intercept = (sy - slope * sx) / n;
  const my = sy / n;
  let ssTot = 0;
  let ssRes = 0;
  for (let i = 0; i < n; i++) {
    const pred = slope * times[i] + intercept;
    ssTot += (series[i] - my) ** 2;
    ssRes += (series[i] - pred) ** 2;
  }
  if (ssTot <= 0) return ssRes <= 0 ? 1 : 0;
  return Math.max(0, Math.min(1, 1 - ssRes / ssTot));
}
