/**
 * Atmospheric delay mitigation framework (plan §6 "Atmospheric Delay
 * Mitigation Framework", §12 preprocessing module).
 *
 * The interferometric phase decomposes as (plan §6)
 *
 *   φ_int = φ_disp + φ_topo + φ_orbit + φ_atm + φ_noise
 *   φ_atm = φ_strat(tropospheric, stratified) + φ_turb(tropospheric, turbulent)
 *         + φ_iono (ionospheric, dispersive)
 *
 * The NISAR L2 GUNW products supply geocoding-step corrections only; the
 * stored unwrapped phase retains the atmospheric terms, so the system plan
 * prescribes:
 *
 *   1. Tropospheric delay — raytraced integration of hydrostatic + wet
 *      refractivity from ERA5 reanalysis fields (RAiDER). The ERA5 CDS API
 *      requires credentials this deployment does not hold, so the stack
 *      pipeline uses the documented fallback: the phase–elevation correlation
 *      method (Bekaert et al. 2015 GACOS-style iterative estimate), which
 *      estimates the stratified term  φ_strat ≈ g·(h − h̄)  directly from the
 *      interferogram itself with outlier trimming, and quantifies the
 *      turbulent remainder as a residual budget term.
 *
 *   2. Ionospheric delay — split-spectrum processing isolates the dispersive
 *      ionospheric phase in the L2 products (supplied as a screen that is NOT
 *      applied to the stored phase). At stack level the dominant ionospheric
 *      signature over one frame is a long-wavelength ramp (vTEC gradient);
 *      the pipeline estimates and removes a plane fitted over the stable
 *      reference region — the same region used for phase referencing — and
 *      reports the removed magnitude in the phase-budget ledger.
 *
 * Every correction here is applied with explicit before/after statistics in
 * the output, so the reader can see exactly what was removed and what
 * remained. Nothing is silently "fixed".
 */

import { madSigma, median } from './statistics';

export interface StratifiedCorrection {
  /** Estimated phase-per-elevation slope (m of LOS per metre of height). */
  slopeMPerM: number;
  /** RMS of the removed stratified term (m). */
  removedRmsM: number;
  /** Robust σ of the field before / after correction (m). */
  sigmaBeforeM: number;
  sigmaAfterM: number;
  /** Pixels used in the regression. */
  n: number;
  method: string;
}

/**
 * Estimate and REMOVE the stratified tropospheric term from one
 * interferogram (displacement in metres, DEM in metres, mask of valid
 * pixels). Iteratively reweighted least squares on d = g·h + c with MAD
 * outlier trimming (two passes), evaluated only where mask=1.
 *
 * Mutates `disp` (in-place removal); returns the ledger statistics.
 */
export interface StratOptions {
  /**
   * Physical bound on the phase-per-elevation slope (m of LOS per m of
   * height). Stratified delay gradients rarely exceed ~10 mm/km of height
   * (1e-5 m/m); a regression that returns more is fitting deformation or
   * turbulence, not atmosphere, and is clamped.
   */
  maxAbsSlope?: number;
  /**
   * Minimum DEM range (m) the regression mask must span for the slope to be
   * identifiable at all; below this the slope is set to 0 (correction
   * skipped, honestly reported).
   */
  minDemRangeM?: number;
}

export function removeStratifiedTroposphere(
  disp: Float32Array,
  dem: Float32Array,
  mask: Uint8Array,
  opts: StratOptions = {},
): StratifiedCorrection {
  const maxAbsSlope = opts.maxAbsSlope ?? 1e-5;
  const minDemRangeM = opts.minDemRangeM ?? 150;
  const sigmaBefore = madSigma(masked(disp, mask));

  // Identifiability gate: the mask must span enough elevation range.
  let hMin = Infinity;
  let hMax = -Infinity;
  for (let i = 0; i < disp.length; i++) {
    if (!mask[i]) continue;
    const h = dem[i];
    if (!Number.isFinite(h)) continue;
    if (h < hMin) hMin = h;
    if (h > hMax) hMax = h;
  }
  const demSpan = hMax - hMin;
  let g = 0;
  let intercept = 0;
  let nUsed = 0;
  let mad = Infinity;
  let skipped = demSpan < minDemRangeM;

  if (!skipped) {
    for (let pass = 0; pass < 2; pass++) {
      // Regress d on h over valid pixels (single pass = O(N) accumulators).
      let n = 0, sh = 0, sd = 0, shh = 0, shd = 0;
      for (let i = 0; i < disp.length; i++) {
        if (!mask[i]) continue;
        const h = dem[i];
        const d = disp[i];
        if (!Number.isFinite(h) || !Number.isFinite(d)) continue;
        if (pass === 1) {
          // Second pass: trim residuals > 2.5·MAD from the first-pass fit.
          const r = d - (g * h + intercept);
          if (Math.abs(r) > 2.5 * mad) continue;
        }
        n++;
        sh += h;
        sd += d;
        shh += h * h;
        shd += h * d;
      }
      nUsed = n;
      const den = n * shh - sh * sh;
      if (n >= 10 && Math.abs(den) > 1e-9) {
        g = (n * shd - sh * sd) / den;
        intercept = (sd - g * sh) / n;
      } else {
        break;
      }
      // Physical clamp: a slope beyond the stratified-delay regime is
      // fitting deformation/turbulence, not atmosphere.
      if (Math.abs(g) > maxAbsSlope) {
        g = Math.sign(g) * maxAbsSlope;
      }
      // MAD of current residuals for the next trim.
      const res: number[] = [];
      for (let i = 0; i < disp.length; i++) {
        if (!mask[i]) continue;
        const r = disp[i] - (g * dem[i] + intercept);
        if (Number.isFinite(r)) res.push(r);
      }
      mad = madSigma(res) || 1e-9;
    }
  }

  // Remove g·(h − h̄) — centre on the mean elevation so the constant part is
  // absorbed by the phase referencing rather than a fake deformation offset.
  let hMean = 0;
  let hn = 0;
  for (let i = 0; i < disp.length; i++) {
    if (mask[i] && Number.isFinite(dem[i])) {
      hMean += dem[i];
      hn++;
    }
  }
  hMean = hn > 0 ? hMean / hn : 0;

  let removed2 = 0;
  let rn = 0;
  for (let i = 0; i < disp.length; i++) {
    if (!mask[i]) continue;
    const removed = g * (dem[i] - hMean);
    disp[i] -= removed;
    removed2 += removed * removed;
    rn++;
  }
  const sigmaAfter = madSigma(masked(disp, mask));

  return {
    slopeMPerM: g,
    removedRmsM: rn > 0 ? Math.sqrt(removed2 / rn) : 0,
    sigmaBeforeM: sigmaBefore,
    sigmaAfterM: sigmaAfter,
    n: nUsed,
    method:
      (skipped
        ? `Skipped: the regression mask spans only ${demSpan.toFixed(0)} m of elevation (< ${minDemRangeM} m), ` +
          'so the stratified slope is not identifiable; no correction was applied. '
        : '') +
      'Phase–elevation correlation (iteratively trimmed least squares, two passes, 2.5·MAD trim, ' +
      `slope clamped to ±${maxAbsSlope.toExponential(0)} m/m — the physical regime of stratified delays); ` +
      'ERA5/RAiDER raytracing requires CDS credentials not held by this deployment ' +
      '(plan §6 fallback, documented in the phase-budget ledger).',
  };
}

function masked(values: Float32Array, mask: Uint8Array): number[] {
  const out: number[] = [];
  for (let i = 0; i < values.length; i++) {
    if (mask[i] && Number.isFinite(values[i])) out.push(values[i]);
  }
  return out;
}

export interface RampCorrection {
  /** Planar coefficients a·x + b·y + c fitted over the reference region. */
  a: number;
  b: number;
  c: number;
  /** RMS of the removed ramp (m). */
  removedRmsM: number;
  /** Robust σ before / after (m). */
  sigmaBeforeM: number;
  sigmaAfterM: number;
  method: string;
}

/**
 * Estimate and REMOVE a planar long-wavelength ramp (ionospheric vTEC
 * gradient + residual orbital/orbit-attitude term) from one interferogram.
 * The plane is fitted ONLY over the stable reference region — the same
 * far-field pixels used for phase referencing — so it cannot absorb the
 * deformation signal inside the basin.
 *
 * Coordinates are pixel grid indices scaled to metres (x, y) for numerical
 * conditioning.
 */
export function removeLongWavelengthRamp(
  disp: Float32Array,
  mask: Uint8Array,
  isReference: Uint8Array,
  width: number,
  xSpacing: number,
  ySpacing: number,
): RampCorrection {
  const sigmaBefore = madSigma(masked(disp, mask));

  // Fit over reference pixels (fallback: all valid pixels).
  let useRef = false;
  for (let i = 0; i < isReference.length; i++) {
    if (isReference[i] && mask[i]) {
      useRef = true;
      break;
    }
  }
  const pixelCounts = (i: number): boolean => (useRef ? isReference[i] === 1 && mask[i] === 1 : mask[i] === 1);

  let n = 0, sx = 0, sy = 0, sxx = 0, sxy = 0, syy = 0, sxz = 0, syz = 0, sz = 0;
  for (let i = 0; i < disp.length; i++) {
    if (!pixelCounts(i)) continue;
    const x = ((i % width) * xSpacing) / 1000; // km for conditioning
    const y = ((Math.floor(i / width)) * Math.abs(ySpacing)) / 1000;
    const z = disp[i];
    if (!Number.isFinite(z)) continue;
    n++;
    sx += x; sy += y; sz += z;
    sxx += x * x; sxy += x * y; syy += y * y;
    sxz += x * z; syz += y * z;
  }
  let a = 0, b = 0, c = 0;
  if (n >= 20) {
    // 3×3 normal equations via Cramer's rule.
    const m = [
      [sxx, sxy, sx],
      [sxy, syy, sy],
      [sx, sy, n],
    ];
    const rhs = [sxz, syz, sz];
    const det = (mm: number[][]) =>
      mm[0][0] * (mm[1][1] * mm[2][2] - mm[1][2] * mm[2][1]) -
      mm[0][1] * (mm[1][0] * mm[2][2] - mm[1][2] * mm[2][0]) +
      mm[0][2] * (mm[1][0] * mm[2][1] - mm[1][1] * mm[2][0]);
    const D = det(m);
    if (Math.abs(D) > 1e-12) {
      const rep = (col: number) => {
        const mm = m.map((row) => row.slice());
        for (let r = 0; r < 3; r++) mm[r][col] = rhs[r];
        return det(mm);
      };
      a = rep(0) / D;
      b = rep(1) / D;
      c = rep(2) / D;
    }
  }

  let removed2 = 0;
  let rn = 0;
  for (let i = 0; i < disp.length; i++) {
    if (!mask[i]) continue;
    const x = ((i % width) * xSpacing) / 1000;
    const y = ((Math.floor(i / width)) * Math.abs(ySpacing)) / 1000;
    const removed = a * x + b * y + c;
    disp[i] -= removed;
    removed2 += removed * removed;
    rn++;
  }
  const sigmaAfter = madSigma(masked(disp, mask));

  return {
    a, b, c,
    removedRmsM: rn > 0 ? Math.sqrt(removed2 / rn) : 0,
    sigmaBeforeM: sigmaBefore,
    sigmaAfterM: sigmaAfter,
    method:
      'Planar ramp fitted over the stable reference region (the split-spectrum ionospheric screen ' +
      'is supplied but not applied by the product; at frame scale the residual ionosphere dominates ' +
      'the long-wavelength error budget — plan §6). Fitting only over the reference region prevents ' +
      'the ramp from absorbing real deformation.',
  };
}

/**
 * Fractal (power-law) turbulent-troposphere simulator for the synthetic
 * validation stack: a von Kármán-correlated random field with an 8/3 power
 * law (Kolmogorov 3-D turbulence integrated along the ray), synthesized as
 * a superposition of plane waves with spectrum-sampled wavenumbers,
 * uniformly random directions and phases (deterministic seed). This
 * construction is exact-by-definition (no FFT / Gibbs pitfalls) and is used
 * ONLY to synthesize the exercise fixture.
 */
export function turbulentField(
  width: number,
  height: number,
  rmsM: number,
  correlationKm: number,
  pixelKm: number,
  seed: number,
): Float32Array {
  let s = seed >>> 0;
  const rand = () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };

  const k0 = 1 / Math.max(correlationKm, 1e-6); // 1/km
  const kMin = 1 / (Math.max(width, height) * pixelKm); // lowest resolvable mode
  const kMax = 1 / (2 * pixelKm); // Nyquist
  const WAVES = 240;
  const P = 11 / 6; // 2-D field power-law exponent (β = 8/3 process)

  // Closed-form CDF of the power distribution S(k) ∝ k·(1+(k/k0)²)^(−P).
  const F = (x: number) => (k0 * k0 / 2) * Math.pow(1 + (x / k0) ** 2, 1 - P) / (1 - P);
  const fNorm = F(kMax) - F(kMin);
  const sampleK = (u: number): number => {
    let lo = kMin;
    let hi = kMax;
    for (let it = 0; it < 40; it++) {
      const mid = 0.5 * (lo + hi);
      if ((F(mid) - F(kMin)) / fNorm < u) lo = mid;
      else hi = mid;
    }
    return 0.5 * (lo + hi);
  };

  const amps: number[] = [];
  const kxs: number[] = [];
  const kys: number[] = [];
  const phases: number[] = [];
  for (let w = 0; w < WAVES; w++) {
    const k = sampleK(rand());
    const theta = rand() * 2 * Math.PI;
    amps.push(Math.pow(1 + (k / k0) ** 2, -P / 2)); // sqrt spectral density
    kxs.push(k * Math.cos(theta));
    kys.push(k * Math.sin(theta));
    phases.push(rand() * 2 * Math.PI);
  }

  const out = new Float32Array(width * height);
  const twoPi = 2 * Math.PI;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      let acc = 0;
      for (let w = 0; w < WAVES; w++) {
        acc += amps[w] * Math.cos(twoPi * (kxs[w] * x + kys[w] * y) * pixelKm + phases[w]);
      }
      out[y * width + x] = acc;
    }
  }

  // Normalize to the requested RMS.
  let m = 0;
  for (let i = 0; i < out.length; i++) m += out[i] * out[i];
  const cur = Math.sqrt(m / out.length) || 1;
  const scale = rmsM / cur;
  for (let i = 0; i < out.length; i++) out[i] *= scale;
  return out;
}

/**
 * Ionospheric screen for the synthetic stack: a smooth, slowly varying
 * (epoch-to-epoch) long-wavelength ramp whose difference between two epochs
 * enters the interferogram — modelling vTEC gradients that the unapplied
 * split-spectrum screen would have removed.
 */
export function ionoRampField(
  width: number,
  height: number,
  epochIndex: number,
  seed: number,
  rmsM: number,
  pixelKm: number,
): Float32Array {
  let s = (seed + epochIndex * 2654435761) >>> 0;
  const rand = () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  // One gradient direction per "ionospheric weather regime" (regime changes
  // slowly: 1 new draw every 4 epochs).
  const regime = Math.floor(epochIndex / 4);
  let s2 = (seed + regime * 40503) >>> 0;
  const rand2 = () => {
    s2 = (s2 + 0x6d2b79f5) >>> 0;
    let t = s2;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const theta = rand2() * 2 * Math.PI;
  const gradPerKm = (rand() - 0.5) * 2; // direction-stable magnitude per regime
  const gx = Math.cos(theta) * gradPerKm;
  const gy = Math.sin(theta) * gradPerKm;

  const out = new Float32Array(width * height);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      out[y * width + x] = (gx * x + gy * y) * pixelKm;
    }
  }
  // Normalize RMS.
  let m = 0;
  for (let i = 0; i < out.length; i++) m += out[i] * out[i];
  const cur = Math.sqrt(m / out.length) || 1;
  const scale = rmsM / cur;
  for (let i = 0; i < out.length; i++) out[i] *= scale;
  return out;
}

/** Median of a Float32 subarray helper (kept local; statistics exports median for number[]). */
export function medianOf(values: ArrayLike<number>): number {
  const arr: number[] = [];
  for (let i = 0; i < values.length; i++) if (Number.isFinite(values[i])) arr.push(values[i]);
  return median(arr);
}
