/**
 * Robust statistics used across the pipeline.
 *
 * Robust (median/MAD-based) estimators are preferred because interferometric
 * displacement fields routinely contain outliers from unwrapping errors,
 * atmospheric residuals, and incoherent pixels that survive masking.
 * Median and MAD have a 50% breakdown point (Huber, 1981, "Robust
 * Statistics"; Hampel et al., 1986).
 */

/** Median of a numeric array (NaNs ignored). Returns NaN for empty input. */
export function median(values: ArrayLike<number>): number {
  const arr: number[] = [];
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (!Number.isNaN(v)) arr.push(v);
  }
  if (arr.length === 0) return NaN;
  arr.sort((a, b) => a - b);
  const mid = arr.length >> 1;
  return arr.length % 2 ? arr[mid] : (arr[mid - 1] + arr[mid]) / 2;
}

/**
 * Median absolute deviation (MAD) of a numeric array (NaNs ignored).
 * The consistent sigma estimate is 1.4826 * MAD (normal distribution).
 */
export function madSigma(values: ArrayLike<number>): number {
  const med = median(values);
  if (Number.isNaN(med)) return NaN;
  const devs: number[] = [];
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (!Number.isNaN(v)) devs.push(Math.abs(v - med));
  }
  return 1.4826 * median(devs);
}

/** Arithmetic mean (NaNs ignored). */
export function mean(values: ArrayLike<number>): number {
  let s = 0;
  let n = 0;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (!Number.isNaN(v)) {
      s += v;
      n++;
    }
  }
  return n === 0 ? NaN : s / n;
}

/** Standard deviation (NaNs ignored, sample ddof=0). */
export function std(values: ArrayLike<number>): number {
  const m = mean(values);
  if (Number.isNaN(m)) return NaN;
  let s = 0;
  let n = 0;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (!Number.isNaN(v)) {
      s += (v - m) * (v - m);
      n++;
    }
  }
  return n < 2 ? NaN : Math.sqrt(s / (n - 1));
}

/** Quantile of a numeric array using linear interpolation (NaNs ignored). */
export function quantile(values: ArrayLike<number>, q: number): number {
  const arr: number[] = [];
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (!Number.isNaN(v)) arr.push(v);
  }
  if (arr.length === 0) return NaN;
  arr.sort((a, b) => a - b);
  const pos = (arr.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  if (lo === hi) return arr[lo];
  return arr[lo] + (arr[hi] - arr[lo]) * (pos - lo);
}

/**
 * Weighted planar fit d ≈ a*i + b*j + c over the reference region
 * (least squares on the grid index coordinates i, j). Used only for the
 * optional residual-ramp removal, default OFF, because a plane can absorb
 * real long-wavelength deformation signals.
 */
export function planarFit(
  points: Array<{ i: number; j: number; v: number }>,
): { a: number; b: number; c: number } | null {
  const n = points.length;
  if (n < 6) return null;
  // Normal equations for [a,b,c] with basis (i, j, 1)
  let Sii = 0, Sjj = 0, Sij = 0, Si = 0, Sj = 0;
  let Svi = 0, Svj = 0, Sv = 0;
  for (const p of points) {
    Sii += p.i * p.i;
    Sjj += p.j * p.j;
    Sij += p.i * p.j;
    Si += p.i;
    Sj += p.j;
    Svi += p.v * p.i;
    Svj += p.v * p.j;
    Sv += p.v;
  }
  // Solve the 3x3 system by Cramer's rule.
  const m = [
    [Sii, Sij, Si],
    [Sij, Sjj, Sj],
    [Si, Sj, n],
  ];
  const rhs = [Svi, Svj, Sv];
  const det = (m: number[][]) =>
    m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) -
    m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) +
    m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
  const D = det(m);
  if (Math.abs(D) < 1e-12) return null;
  const replaceCol = (col: number) => {
    const mm = m.map((row) => row.slice());
    for (let r = 0; r < 3; r++) mm[r][col] = rhs[r];
    return det(mm);
  };
  return { a: replaceCol(0) / D, b: replaceCol(1) / D, c: replaceCol(2) / D };
}

/** Pearson correlation of two equal-length arrays (NaN pairs ignored). */
export function pearson(x: ArrayLike<number>, y: ArrayLike<number>): number {
  let sx = 0, sy = 0, n = 0;
  for (let i = 0; i < x.length; i++) {
    if (!Number.isNaN(x[i]) && !Number.isNaN(y[i])) {
      sx += x[i];
      sy += y[i];
      n++;
    }
  }
  if (n < 3) return NaN;
  const mx = sx / n;
  const my = sy / n;
  let num = 0, dx = 0, dy = 0;
  for (let i = 0; i < x.length; i++) {
    if (!Number.isNaN(x[i]) && !Number.isNaN(y[i])) {
      const a = x[i] - mx;
      const b = y[i] - my;
      num += a * b;
      dx += a * a;
      dy += b * b;
    }
  }
  const den = Math.sqrt(dx * dy);
  return den === 0 ? NaN : num / den;
}
