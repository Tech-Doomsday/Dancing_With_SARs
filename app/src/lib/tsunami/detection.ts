/**
 * Tsunami signal detection on bottom-pressure records.
 *
 * Method (documented; parameters configurable, defaults justified inline):
 *  1. De-tiding: local quadratic regression over a ±3 h window centred on
 *     each sample. The astronomical tide (period ≈ 12.4/24 h) is locally
 *     well-approximated by a quadratic over a 6 h window (residual tide leak
 *     ≪ 1 cm), which also removes instrumental drift. This replaces the
 *     UTide/FES2014 harmonic approach of operational centres because the
 *     Copernicus FES2014 service requires an account (documented deviation).
 *  2. Noise scale: σ from the residual over [origin−6 h, origin−10 min].
 *  3. Trigger: first sample after origin−15 min with |residual| > k·σ
 *     (default k = 5). The 15-min pre-origin allowance exists because DART
 *     pressure sensors record the seismic Rayleigh/E phase minutes before
 *     the water wave; pre-origin triggers are flagged separately and never
 *     reported as the tsunami arrival.
 *  4. Amplitude: max |residual| within [trigger, trigger+3 h].
 *  5. Period: dominant period from mean zero-crossing spacing in the first
 *     hour after the trigger (robust first-order spectral estimate).
 *
 * Honesty contract: a trigger is a DETECTION of an anomalous wave, not proof
 * of a tsunami — causal attribution happens in evidence.ts, separately.
 */

import type { DetectionResult, ObsAvailability, TimeSeries, TsunamiProvenance } from './types';

export interface DetectOptions {
  /** k in the k·σ trigger (default 5) */
  k: number;
  /** de-tiding half-window (hours, default 3) */
  detideHalfWindowH: number;
  /** noise window before origin (default [6h, 10min]) */
  noiseWindowH: [number, number];
  /** amplitude window after trigger (hours, default 3) */
  amplitudeWindowH: number;
  /** minimum pre-event samples required for a σ estimate */
  minNoiseSamples: number;
  /**
   * Seismic-phase mask (minutes after origin, default 8): DART bottom
   * pressure sensors record the earthquake's Rayleigh/E phase minutes
   * before the water wave at near-field stations (verified on Tōhoku 2011:
   * the +3 min trigger at DART 21418 is seismic; the water wave arrives
   * ~+29 min). Triggers inside the mask are excluded from the arrival but
   * still reported as a caveat. Physically motivated, configurable.
   */
  seismicMaskMin: number;
  /**
   * Model-informed mask (minutes after origin; optional, used by the
   * simulation runner): a long water wave cannot outrun the eikonal
   * ray time ∫ds/√(gh), so triggers earlier than ~0.85× that bound are
   * physically impossible water arrivals → flagged as seismic
   * contamination instead of defining the arrival. The masked trigger is
   * kept in the result for transparency.
   */
  maskUntilMin?: number;
}

export const DEFAULT_DETECT_OPTIONS: DetectOptions = {
  k: 5,
  detideHalfWindowH: 3,
  noiseWindowH: [6, 1 / 6],
  amplitudeWindowH: 3,
  minNoiseSamples: 8,
  seismicMaskMin: 8,
};

/**
 * Local quadratic de-tiding, ROBUST two-pass:
 *   pass 1 — quadratic LS fit over the ±halfWindowH window (captures the
 *            locally-smooth tide + drift);
 *   pass 2 — window samples whose pass-1 residual exceeds 2.5× the window's
 *            residual RMS are excluded and the fit is redone. This stops the
 *            tsunami itself from biasing the fit (measured 13% amplitude loss
 *            on the synthetic benchmark without it); the smooth tide survives.
 * At the series edges the window shrinks (minimum 3 points, else dropped).
 */
export function localQuadraticDetide(
  t: Float64Array,
  v: Float32Array,
  halfWindowH: number,
): { t: Float64Array; resid: Float32Array } {
  const n = t.length;
  const outT: number[] = [];
  const outV: number[] = [];
  const hwMs = halfWindowH * 3600_000;

  /** Weighted quadratic fit over [lo, hi] centred on m (x in hours).
   *  Returns coefficients [c0, c1, c2] of p(x) = c0 + c1 x + c2 x², or null. */
  const fitQuad = (m: number, lo: number, hi: number, use: boolean[] | null): [number, number, number] | null => {
    let s0 = 0, s1 = 0, s2 = 0, s3 = 0, s4 = 0, b0 = 0, b1 = 0, b2 = 0;
    for (let q = lo; q <= hi; q++) {
      if (use && !use[q]) continue; // the centre may be an outlier too
      const x = (t[q] - t[m]) / 3600_000;
      const y = v[q];
      s0 += 1; s1 += x; s2 += x * x; s3 += x ** 3; s4 += x ** 4;
      b0 += y; b1 += x * y; b2 += x * x * y;
    }
    if (s0 < 3) return null;
    const A = [s0, s1, s2, s1, s2, s3, s2, s3, s4];
    const b = [b0, b1, b2];
    for (let col = 0; col < 3; col++) {
      let piv = col;
      for (let r = col + 1; r < 3; r++) {
        if (Math.abs(A[r * 3 + col]) > Math.abs(A[piv * 3 + col])) piv = r;
      }
      if (Math.abs(A[piv * 3 + col]) < 1e-12) return null;
      if (piv !== col) {
        for (let c = 0; c < 3; c++) {
          const tmp = A[col * 3 + c];
          A[col * 3 + c] = A[piv * 3 + c];
          A[piv * 3 + c] = tmp;
        }
        const tb = b[col]; b[col] = b[piv]; b[piv] = tb;
      }
      const d = A[col * 3 + col];
      for (let c = 0; c < 3; c++) A[col * 3 + c] /= d;
      b[col] /= d;
      for (let r = 0; r < 3; r++) {
        if (r === col) continue;
        const f = A[r * 3 + col];
        if (f === 0) continue;
        for (let c = 0; c < 3; c++) A[r * 3 + c] -= f * A[col * 3 + c];
        b[r] -= f * b[col];
      }
    }
    return [b[0], b[1], b[2]];
  };

  let lo = 0;
  let hi = 0;
  for (let m = 0; m < n; m++) {
    while (lo < m && t[m] - t[lo] > hwMs) lo++;
    if (hi < m) hi = m;
    while (hi + 1 < n && t[hi + 1] - t[m] <= hwMs) hi++;
    if (hi - lo + 1 < 3) continue;
    if (t[hi] - t[lo] < 0.5 * hwMs) continue;

    const c1 = fitQuad(m, lo, hi, null);
    if (!c1) continue;

    // pass-1 residuals at every window sample (evaluate the polynomial!)
    // Robust scale = MAD (median absolute deviation) — insensitive to the
    // tsunami samples themselves, unlike an RMS that the wave inflates.
    const devs: number[] = [];
    for (let q = lo; q <= hi; q++) {
      const x = (t[q] - t[m]) / 3600_000;
      const p = c1[0] + c1[1] * x + c1[2] * x * x;
      devs.push(Math.abs(v[q] - p));
    }
    const devsSorted = [...devs].sort((a, b) => a - b);
    const mad = devsSorted[Math.floor(devsSorted.length / 2)] ?? 0;
    const robSig = 1.4826 * mad;

    const use: boolean[] = new Array(n).fill(true);
    let nOut = 0;
    let di = 0;
    for (let q = lo; q <= hi; q++, di++) {
      if (devs[di] > 3 * Math.max(robSig, 1e-4)) {
        use[q] = false;
        nOut++;
      }
    }
    let coeffs = c1;
    if (nOut > 0 && devs.length - nOut >= 3) {
      const c2 = fitQuad(m, lo, hi, use);
      if (c2) coeffs = c2;
    }
    outT.push(t[m]);
    outV.push(v[m] - coeffs[0]);
  }
  return { t: Float64Array.from(outT), resid: Float32Array.from(outV) };
}

/** Mean zero-crossing period (minutes) in a residual segment. */
function dominantPeriodMin(t: Float64Array, r: Float32Array, from: number, to: number): number | null {
  let lastCross = NaN;
  const gaps: number[] = [];
  for (let m = 0; m < t.length; m++) {
    if (t[m] < from || t[m] > to) continue;
    if (m === 0) {
      lastCross = t[m];
      continue;
    }
    if (t[m - 1] < from) continue;
    if ((r[m - 1] <= 0 && r[m] > 0) || (r[m - 1] >= 0 && r[m] < 0)) {
      if (Number.isFinite(lastCross) && t[m] > lastCross) {
        gaps.push(t[m] - lastCross);
      }
      lastCross = t[m];
    }
  }
  if (gaps.length < 2) return null;
  gaps.sort((a, b) => a - b);
  const median = gaps[Math.floor(gaps.length / 2)];
  return (2 * median) / 60_000; // half-period × 2, ms → min
}

/**
 * Full detection for one station given its record and the event origin.
 * Returns an explicit availability state when the data cannot support a
 * decision — never a silent "no tsunami".
 */
export function detectTsunamiSignal(
  stationId: string,
  series: TimeSeries,
  originMs: number,
  provenance: TsunamiProvenance,
  optsIn: Partial<DetectOptions> = {},
): DetectionResult {
  const opts = { ...DEFAULT_DETECT_OPTIONS, ...optsIn };
  const caveats: string[] = [];

  // Analysis window: origin−8h .. origin+8h (detide needs ±3h context).
  const winStart = originMs - 8 * 3600_000;
  const winEnd = originMs + 8 * 3600_000;
  const idx: number[] = [];
  for (let m = 0; m < series.t.length; m++) {
    if (series.t[m] >= winStart && series.t[m] <= winEnd) idx.push(m);
  }

  const insufficient = (reason: string): DetectionResult => ({
    stationId,
    provenance,
    t: new Float64Array(0),
    resid: new Float32Array(0),
    sigmaNoise: NaN,
    triggerMs: null,
    arrivalMin: null,
    preOriginExcursion: false,
    maxAmplitudeM: null,
    dominantPeriodMin: null,
    nSamples: idx.length,
    caveats: [reason],
    availability: { state: 'missing', reason },
  });

  if (idx.length < opts.minNoiseSamples + 10) {
    return insufficient(
      `only ${idx.length} samples cover ±8 h of the origin — the record does not cover the event window`,
    );
  }

  // Pre-event coverage check: need noise window samples.
  let noiseCount = 0;
  for (const m of idx) {
    const dt = originMs - series.t[m];
    if (dt >= opts.noiseWindowH[1] * 3600_000 && dt <= opts.noiseWindowH[0] * 3600_000) noiseCount++;
  }
  if (noiseCount < opts.minNoiseSamples) {
    return insufficient(
      `only ${noiseCount} pre-event samples in the noise window — no baseline to define the noise scale`,
    );
  }

  const t = Float64Array.from(idx.map((m) => series.t[m]));
  const v = Float32Array.from(idx.map((m) => series.v[m]));

  // gap detection (cadence jumps)
  let cadence = Infinity;
  for (let m = 1; m < t.length; m++) {
    const d = t[m] - t[m - 1];
    if (d > 0 && d < cadence) cadence = d;
  }
  let maxGap = 0;
  for (let m = 1; m < t.length; m++) {
    const d = t[m] - t[m - 1];
    if (d > maxGap) maxGap = d;
  }
  if (maxGap > 4 * cadence) {
    caveats.push(`data gap of ${Math.round(maxGap / 60_000)} min inside the window (nominal cadence ${Math.round(cadence / 60_000)} min)`);
  }

  const { t: dt_, resid } = localQuadraticDetide(t, v, opts.detideHalfWindowH);

  // noise sigma over [origin−6h, origin−10min], ROBUST (MAD-based): a
  // seismic-phase excursion inside the window would inflate an RMS estimate
  // and push the trigger threshold above the excursion itself.
  const noiseIdx: number[] = [];
  for (let m = 0; m < dt_.length; m++) {
    const d = originMs - dt_[m];
    if (d >= opts.noiseWindowH[1] * 3600_000 && d <= opts.noiseWindowH[0] * 3600_000) {
      noiseIdx.push(m);
    }
  }
  if (noiseIdx.length < opts.minNoiseSamples) {
    return insufficient(`de-tided series has only ${noiseIdx.length} noise-window samples`);
  }
  const absDevs = noiseIdx.map((m) => Math.abs(resid[m])).sort((a, b) => a - b);
  const mad = absDevs[Math.floor(absDevs.length / 2)] ?? 0;
  const sigma = 1.4826 * mad;
  const threshold = opts.k * sigma;

  // trigger search from origin−15min, excluding the seismic-phase mask:
  // excursions in [origin, maskEnd] are recorded as a caveat but do not
  // define the (hydrodynamic) arrival. maskEnd = max(fixed 8 min,
  // model-informed bound) when the caller supplies one.
  const searchStart = originMs - 15 * 60_000;
  const maskMin = Math.max(opts.seismicMaskMin, opts.maskUntilMin ?? 0);
  const maskEnd = originMs + maskMin * 60_000;
  let triggerIdx = -1;
  let seismicPhase = false;
  let maskedTriggerMin: number | null = null;
  for (let m = 0; m < dt_.length; m++) {
    if (dt_[m] < searchStart) continue;
    if (Math.abs(resid[m]) > threshold) {
      if (dt_[m] >= originMs && dt_[m] < maskEnd) {
        if (maskedTriggerMin === null) maskedTriggerMin = (dt_[m] - originMs) / 60_000;
        seismicPhase = true;
        continue; // keep searching for the water wave
      }
      triggerIdx = m;
      break;
    }
  }
  if (seismicPhase) {
    caveats.push(
      maskedTriggerMin !== null && (opts.maskUntilMin ?? 0) > opts.seismicMaskMin
        ? `excitation at +${maskedTriggerMin.toFixed(0)} min excluded: a water wave cannot arrive before ~85% of the eikonal ray time (${(opts.maskUntilMin ?? 0).toFixed(0)} min) — seismic phase on the pressure record`
        : `excitation within ${opts.seismicMaskMin} min of the origin excluded from the arrival (seismic phase on the pressure record, not the water wave)`,
    );
  }
  const preOriginExcursion =
    triggerIdx >= 0 ? dt_[triggerIdx] < originMs : false;

  let maxAmp: number | null = null;
  let period: number | null = null;
  if (triggerIdx >= 0) {
    const tEnd = dt_[triggerIdx] + opts.amplitudeWindowH * 3600_000;
    for (let m = triggerIdx; m < dt_.length && dt_[m] <= tEnd; m++) {
      const a = Math.abs(resid[m]);
      if (a > (maxAmp ?? 0)) maxAmp = a;
    }
    period = dominantPeriodMin(dt_, resid, dt_[triggerIdx], dt_[triggerIdx] + 3600_000);
    if (preOriginExcursion) {
      caveats.push('trigger precedes the origin — likely the seismic phase on the pressure sensor, not the water wave');
    }
  }

  const availability: ObsAvailability = { state: 'available' };
  return {
    stationId,
    provenance: {
      ...provenance,
      processing: [
        ...provenance.processing,
        `local quadratic de-tiding (±${opts.detideHalfWindowH} h window)`,
        `noise σ = ${(sigma * 100).toFixed(2)} cm (robust MAD estimate) from ${noiseIdx.length} pre-event samples`,
        `trigger rule |residual| > ${opts.k}·σ = ${(threshold * 100).toFixed(2)} cm from origin−15 min`,
      ],
    },
    t: dt_,
    resid,
    sigmaNoise: sigma,
    triggerMs: triggerIdx >= 0 ? dt_[triggerIdx] : null,
    arrivalMin: triggerIdx >= 0 ? (dt_[triggerIdx] - originMs) / 60_000 : null,
    preOriginExcursion,
    maxAmplitudeM: maxAmp,
    dominantPeriodMin: period,
    nSamples: dt_.length,
    caveats,
    availability,
  };
}
