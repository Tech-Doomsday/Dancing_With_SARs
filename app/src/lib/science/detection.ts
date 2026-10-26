/**
 * Change detection and summary statistics over a referenced displacement
 * field with per-pixel uncertainty.
 *
 * Detection rule (documented, transparent): a pixel counts as "changed" when
 *   |d| > max(k * sigma_total, physicalFloor)
 * where sigma_total combines the coherence-based phase noise propagated to
 * displacement and the reference-region scatter. The physical floor
 * (default 10 mm, or the ionospheric systematic when larger) guards against
 * declaring sub-noise variations significant when systematic residuals
 * dominate the true error budget.
 *
 * STATISTICAL STATUS (remediation 2026-10-21): this rule is a per-pixel
 * threshold, NOT a calibrated multiple-testing procedure over a spatially
 * correlated raster. The outputs therefore separate:
 *   - valid-pixel masking (coverage) from detected-pixel classification,
 *   - per-pixel exceedance from CLUSTER-level statistics (connected regions
 *     of detected pixels, their sizes and counts),
 * and expose an exploratory NULL-SCENE screen (simulated correlated noise,
 * deterministic seeds) that measures how often the rule fires when no
 * change exists. No formal false-alarm rate is claimed: the calibration
 * needed for that (scene-dependent covariance of atmosphere + ionosphere +
 * decorrelation) is not available to this system, and the screen's numbers
 * are labelled exploratory wherever they appear.
 */

import type { GridMeta } from './types';
import { median } from './statistics';
import { mulberry32 } from './fixtures';

export interface DetectionInputs {
  meta: GridMeta;
  /** Referenced displacement (m); NaN = masked */
  displacement: Float32Array;
  /** Per-pixel one-sigma (m); NaN = masked */
  sigma: Float32Array;
  /** Quality mask: 1 = valid, 0 = masked */
  mask: Uint8Array;
  /** Significance multiplier k */
  k: number;
  /** Physical detection floor (m) */
  floorM: number;
}

export interface ClusterStats {
  /** Number of connected detected regions with >= minPixels pixels. */
  count: number;
  /** Size of the largest such region (km²). */
  largestKm2: number;
  /** Sizes of all such regions, descending (km²). */
  sizesKm2: number[];
  /** Minimum cluster size used (pixels). */
  minPixels: number;
}

export interface DetectionOutputs {
  thresholds: Float32Array;
  detected: Uint8Array;
  clusters: ClusterStats;
  stats: {
    validPixelCount: number;
    /** Pixels failing the quality mask (everything not valid). */
    maskedPixelCount: number;
    /** Valid pixels classified as detected. */
    detectedPixelCount: number;
    /** Median detection threshold over valid pixels (m). */
    thresholdMedianM: number;
    affectedAreaKm2: number;
    towardAreaKm2: number;
    awayAreaKm2: number;
    maxAwayM: number;
    maxTowardM: number;
    medianSigmaM: number;
    medianSigmaAnomalyM: number;
    anomalyCentroid?: { x: number; y: number };
    anomalyOrientationDeg?: number;
    anomalyElongation?: number;
    displacementIntegralMKm2: number;
    coherenceMedian: number;
    coherenceMean: number;
    validFraction: number;
    pixelAreaKm2: number;
    analysedAreaKm2: number;
  };
}

/**
 * Connected-component labelling (4-neighbourhood) of the detected pixels.
 * Regions smaller than minPixels are ignored — a documented minimum-cluster
 * criterion, not a statistical guarantee.
 */
export function detectedClusterStats(
  detected: Uint8Array,
  meta: GridMeta,
  minPixels: number,
): ClusterStats {
  const w = meta.width;
  const h = meta.height;
  const visited = new Uint8Array(w * h);
  const sizes: number[] = [];
  const queue = new Int32Array(w * h);
  for (let start = 0; start < w * h; start++) {
    if (!detected[start] || visited[start]) continue;
    let head = 0;
    let tail = 0;
    queue[tail++] = start;
    visited[start] = 1;
    let size = 0;
    while (head < tail) {
      const idx = queue[head++];
      size++;
      const i = Math.floor(idx / w);
      const j = idx % w;
      if (j > 0 && detected[idx - 1] && !visited[idx - 1]) { visited[idx - 1] = 1; queue[tail++] = idx - 1; }
      if (j < w - 1 && detected[idx + 1] && !visited[idx + 1]) { visited[idx + 1] = 1; queue[tail++] = idx + 1; }
      if (i > 0 && detected[idx - w] && !visited[idx - w]) { visited[idx - w] = 1; queue[tail++] = idx - w; }
      if (i < h - 1 && detected[idx + w] && !visited[idx + w]) { visited[idx + w] = 1; queue[tail++] = idx + w; }
    }
    if (size >= minPixels) sizes.push(size);
  }
  sizes.sort((a, b) => b - a);
  const pixelAreaKm2 = Math.abs(meta.xSpacing * meta.ySpacing) / 1e6;
  return {
    count: sizes.length,
    largestKm2: sizes.length ? sizes[0] * pixelAreaKm2 : 0,
    sizesKm2: sizes.map((s) => s * pixelAreaKm2),
    minPixels,
  };
}

export function runDetection(
  inputs: DetectionInputs,
  coherence: Float32Array,
): DetectionOutputs {
  const { meta, displacement, sigma, mask, k, floorM } = inputs;
  const n = meta.width * meta.height;
  const thresholds = new Float32Array(n);
  const detected = new Uint8Array(n);

  const pixelAreaM2 = Math.abs(meta.xSpacing * meta.ySpacing);
  const pixelAreaKm2 = pixelAreaM2 / 1e6;

  let validCount = 0;
  let detectedCount = 0;
  let towardCount = 0;
  let awayCount = 0;
  let maxAway = 0;
  let maxToward = 0;
  let integralMKm2 = 0;
  const sigmas: number[] = [];
  const anomalySigmas: number[] = [];
  const cohs: number[] = [];
  const thrList: number[] = [];
  let cohSum = 0;
  let cohN = 0;

  // Anomaly moments
  let sw = 0;
  let cx = 0;
  let cy = 0;
  let cxx = 0;
  let cyy = 0;
  let cxy = 0;

  for (let idx = 0; idx < n; idx++) {
    if (!mask[idx]) {
      thresholds[idx] = NaN;
      continue;
    }
    validCount++;
    const c = coherence[idx];
    if (!Number.isNaN(c)) {
      cohs.push(c);
      cohSum += c;
      cohN++;
    }
    const s = sigma[idx];
    if (!Number.isNaN(s)) sigmas.push(s);
    const d = displacement[idx];
    const thr = Math.max(k * (Number.isNaN(s) ? 0 : s), floorM);
    thresholds[idx] = thr;
    thrList.push(thr);
    if (Math.abs(d) > thr) {
      detected[idx] = d > 0 ? 1 : 2; // 1 = away (positive), 2 = toward (negative)
      detectedCount++;
      anomalySigmas.push(s);
      integralMKm2 += d * pixelAreaKm2; // m * km^2
      if (d > 0) {
        awayCount++;
        if (d > maxAway) maxAway = d;
      } else {
        towardCount++;
        if (d < maxToward) maxToward = d;
      }
      const i = Math.floor(idx / meta.width);
      const j = idx % meta.width;
      const x = meta.xStart + j * meta.xSpacing;
      const y = meta.yStart + i * meta.ySpacing;
      const w = Math.abs(d);
      sw += w;
      cx += w * x;
      cy += w * y;
      cxx += w * x * x;
      cyy += w * y * y;
      cxy += w * x * y;
    }
  }

  // Minimum credible cluster: 4 pixels by default (documented convention).
  const clusters = detectedClusterStats(detected, meta, 4);

  const centroid =
    sw > 0 ? { x: cx / sw, y: cy / sw } : undefined;

  let orientation: number | undefined;
  let elongation: number | undefined;
  if (centroid && sw > 0) {
    const varX = cxx / sw - centroid.x * centroid.x;
    const varY = cyy / sw - centroid.y * centroid.y;
    const covXY = cxy / sw - centroid.x * centroid.y;
    // Principal axis of the covariance matrix.
    const trace = varX + varY;
    const diff = varX - varY;
    const disc = Math.sqrt(diff * diff + 4 * covXY * covXY);
    const lambdaMax = (trace + disc) / 2;
    const lambdaMin = (trace - disc) / 2;
    if (lambdaMax > 0 && lambdaMin >= 0) {
      elongation = lambdaMin > 0 ? Math.sqrt(lambdaMax / lambdaMin) : Infinity;
      // Angle of the major axis (eigenvector) from east.
      const theta = 0.5 * Math.atan2(2 * covXY, diff);
      orientation = (theta * 180) / Math.PI;
    }
  }

  const coherenceMedian = median(cohs);
  const stats: DetectionOutputs['stats'] = {
    validPixelCount: validCount,
    maskedPixelCount: n - validCount,
    detectedPixelCount: detectedCount,
    thresholdMedianM: median(thrList),
    affectedAreaKm2: detectedCount * pixelAreaKm2,
    towardAreaKm2: towardCount * pixelAreaKm2,
    awayAreaKm2: awayCount * pixelAreaKm2,
    maxAwayM: maxAway,
    maxTowardM: maxToward,
    medianSigmaM: median(sigmas),
    medianSigmaAnomalyM: median(anomalySigmas),
    anomalyCentroid: centroid,
    anomalyOrientationDeg: orientation,
    anomalyElongation: elongation,
    displacementIntegralMKm2: integralMKm2,
    coherenceMedian,
    coherenceMean: cohN ? cohSum / cohN : NaN,
    validFraction: validCount / n,
    pixelAreaKm2,
    analysedAreaKm2: validCount * pixelAreaKm2,
  };
  return { thresholds, detected, clusters, stats };
}

// ---------------------------------------------------------------------------
// Exploratory null-scene screen (simulated correlated noise).

export interface NullSceneResult {
  /** Number of simulated null scenes. */
  scenes: number;
  /** Mean fraction of valid pixels falsely detected across scenes. */
  detectedFractionMean: number;
  /** Worst-case fraction across scenes. */
  detectedFractionMax: number;
  /** Mean falsely "affected" area (km²). */
  falseAreaKm2Mean: number;
  /** Number of scenes with at least one >= minPixels cluster. */
  scenesWithClusters: number;
  /** Correlation length used (pixels). */
  correlationLengthPx: number;
  /** Deterministic seed base. */
  seed: number;
  note: string;
}

/**
 * Simulate null scenes (no change) with SPATIALLY CORRELATED Gaussian noise
 * and run the same detection rule. Correlation is introduced by box-filtering
 * white noise over `correlationLengthPx` and renormalising to unit variance,
 * then scaling by the per-pixel sigma of the real scene — so the simulation
 * inherits the scene's own noise map while adding the long-range correlation
 * that independent-noise tests miss.
 *
 * Deterministic (seeded PRNG). This is an EXPLORATORY screen: it does not
 * model atmosphere/ionosphere physics and its numbers must not be presented
 * as a calibrated false-alarm rate.
 */
export function runNullSceneScreen(
  inputs: DetectionInputs,
  opts: { scenes?: number; correlationLengthPx?: number; seed?: number } = {},
): NullSceneResult {
  const scenes = opts.scenes ?? 8;
  const corrLen = Math.max(1, opts.correlationLengthPx ?? 8);
  const seed = opts.seed ?? 20261021;
  const { meta, sigma, mask, k, floorM } = inputs;
  const w = meta.width;
  const h = meta.height;
  const n = w * h;

  let validCount = 0;
  for (let idx = 0; idx < n; idx++) if (mask[idx]) validCount++;
  if (validCount === 0) {
    return {
      scenes: 0,
      detectedFractionMean: NaN,
      detectedFractionMax: NaN,
      falseAreaKm2Mean: NaN,
      scenesWithClusters: 0,
      correlationLengthPx: corrLen,
      seed,
      note: 'No valid pixels; null screen not run.',
    };
  }

  const r = Math.max(1, Math.floor(corrLen / 2));
  const kernelN = (2 * r + 1) * (2 * r + 1);
  // Variance of a box average of kernelN independent unit-variance samples.
  const kernelVar = 1 / kernelN;

  const detectedFraction: number[] = [];
  const falseArea: number[] = [];
  let scenesWithClusters = 0;

  for (let s = 0; s < scenes; s++) {
    const rng = mulberry32(seed + s * 7919);
    // White noise, Box-Muller.
    const white = new Float32Array(n);
    for (let idx = 0; idx < n; idx += 2) {
      let u1 = rng();
      let u2 = rng();
      while (u1 <= 1e-12) u1 = rng();
      const mag = Math.sqrt(-2 * Math.log(u1));
      white[idx] = mag * Math.cos(2 * Math.PI * u2);
      if (idx + 1 < n) white[idx + 1] = mag * Math.sin(2 * Math.PI * u2);
    }
    // Box filter -> correlated noise, renormalised to unit variance.
    const corr = new Float32Array(n);
    for (let i = 0; i < h; i++) {
      for (let j = 0; j < w; j++) {
        let sum = 0;
        let cnt = 0;
        for (let di = -r; di <= r; di++) {
          const ii = i + di;
          if (ii < 0 || ii >= h) continue;
          for (let dj = -r; dj <= r; dj++) {
            const jj = j + dj;
            if (jj < 0 || jj >= w) continue;
            sum += white[ii * w + jj];
            cnt++;
          }
        }
        const mean = sum / Math.max(cnt, 1);
        // Edge pixels average fewer samples -> variance still ~kernelN scaling.
        corr[i * w + j] = mean / Math.sqrt(kernelVar);
      }
    }

    // Apply the detection rule to sigma-scaled correlated noise.
    const det = new Uint8Array(n);
    let detCount = 0;
    for (let idx = 0; idx < n; idx++) {
      if (!mask[idx]) continue;
      const sg = sigma[idx];
      if (Number.isNaN(sg)) continue;
      const d = corr[idx] * sg;
      const thr = Math.max(k * sg, floorM);
      if (Math.abs(d) > thr) {
        det[idx] = d > 0 ? 1 : 2;
        detCount++;
      }
    }
    detectedFraction.push(detCount / validCount);
    const pixelAreaKm2 = Math.abs(meta.xSpacing * meta.ySpacing) / 1e6;
    falseArea.push(detCount * pixelAreaKm2);
    const cl = detectedClusterStats(det, meta, 4);
    if (cl.count > 0) scenesWithClusters++;
  }

  const mean = (a: number[]) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : NaN);
  return {
    scenes,
    detectedFractionMean: mean(detectedFraction),
    detectedFractionMax: detectedFraction.length ? Math.max(...detectedFraction) : NaN,
    falseAreaKm2Mean: mean(falseArea),
    scenesWithClusters,
    correlationLengthPx: corrLen,
    seed,
    note:
      'Exploratory null-scene screen: simulated spatially correlated Gaussian noise ' +
      `(correlation length ${corrLen} px) scaled by the scene's own sigma map, run through the ` +
      'same detection rule. It measures rule behaviour under a noise model, NOT a calibrated ' +
      'false-alarm rate (atmospheric/ionospheric covariances are not modelled).',
  };
}
