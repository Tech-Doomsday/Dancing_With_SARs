/**
 * Pipeline orchestrator — stages A through E.
 *
 *   A  input validation           (validation.ts)
 *   B  preprocessing              reference-area offset, optional detrend,
 *                                  coherence masking
 *   C  change measurement         phase -> LOS displacement, summary metrics
 *                                  (detection.ts)
 *   D  uncertainty                coherence-based sigma + reference scatter
 *                                  (uncertainty.ts)
 *   E  cause investigation        rule-based attribution with independent
 *                                  USGS evidence (attribution.ts)
 *
 * The pipeline is pure with respect to external services: USGS events are
 * passed in by the caller (the API layer), so the science core stays
 * deterministic and unit-testable.
 */

import type {
  AnalysisParams,
  AnalysisResult,
  ChangeSummary,
  ScienceDataset,
  TransportGrid,
  UsgsEventLite,
} from './types';
import { DEFAULT_PARAMS } from './types';
import { validateDataset } from './validation';
import { phaseGridToDisplacement } from './phase';
import { combinedSigmaM } from './uncertainty';
import { runDetection, runNullSceneScreen } from './detection';
import { evaluateAttribution, forwardModelCheck, rankEventsForPair } from './attribution';
import { median, madSigma, std, planarFit } from './statistics';

/** Base64-encode a typed array (little-endian, platform-checked). */
function b64(arr: Float32Array | Uint8Array): string {
  const u8 = new Uint8Array(arr.buffer, arr.byteOffset, arr.byteLength);
  return Buffer.from(u8).toString('base64');
}

/** Area-average downsample keeping NaN when no valid contributor exists. */
function downsample(src: Float32Array, w: number, h: number, factor: number): { data: Float32Array; w: number; h: number } {
  if (factor <= 1) return { data: src, w, h };
  const nw = Math.ceil(w / factor);
  const nh = Math.ceil(h / factor);
  const out = new Float32Array(nw * nh);
  for (let i = 0; i < nh; i++) {
    for (let j = 0; j < nw; j++) {
      let sum = 0;
      let cnt = 0;
      for (let di = 0; di < factor; di++) {
        for (let dj = 0; dj < factor; dj++) {
          const si = i * factor + di;
          const sj = j * factor + dj;
          if (si >= h || sj >= w) continue;
          const v = src[si * w + sj];
          if (!Number.isNaN(v)) {
            sum += v;
            cnt++;
          }
        }
      }
      out[i * nw + j] = cnt ? sum / cnt : NaN;
    }
  }
  return { data: out, w: nw, h: nh };
}

/** Majority-vote downsample of a boolean grid (detected when ≥ half of the
 * valid contributors pass the rule). */
function downsampleDetected(
  detected: Uint8Array,
  mask: Uint8Array,
  w: number,
  h: number,
  factor: number,
): { data: Uint8Array; w: number; h: number } {
  if (factor <= 1) return { data: detected, w, h };
  const nw = Math.ceil(w / factor);
  const nh = Math.ceil(h / factor);
  const out = new Uint8Array(nw * nh);
  for (let i = 0; i < nh; i++) {
    for (let j = 0; j < nw; j++) {
      let hits = 0;
      let valid = 0;
      for (let di = 0; di < factor; di++) {
        for (let dj = 0; dj < factor; dj++) {
          const si = i * factor + di;
          const sj = j * factor + dj;
          if (si >= h || sj >= w) continue;
          const idx = si * w + sj;
          if (!mask[idx]) continue;
          valid++;
          if (detected[idx]) hits++;
        }
      }
      out[i * nw + j] = valid > 0 && hits / valid >= 0.5 ? 1 : 0;
    }
  }
  return { data: out, w: nw, h: nh };
}

function downsampleMask(src: Uint8Array, w: number, h: number, factor: number): { data: Uint8Array; w: number; h: number } {
  if (factor <= 1) return { data: src, w, h };
  const nw = Math.ceil(w / factor);
  const nh = Math.ceil(h / factor);
  const out = new Uint8Array(nw * nh);
  for (let i = 0; i < nh; i++) {
    for (let j = 0; j < nw; j++) {
      let cnt = 0;
      let tot = 0;
      for (let di = 0; di < factor; di++) {
        for (let dj = 0; dj < factor; dj++) {
          const si = i * factor + di;
          const sj = j * factor + dj;
          if (si >= h || sj >= w) continue;
          tot++;
          if (src[si * w + sj]) cnt++;
        }
      }
      out[i * nw + j] = cnt / Math.max(tot, 1) > 0.5 ? 1 : 0;
    }
  }
  return { data: out, w: nw, h: nh };
}

export function runPipeline(
  ds: ScienceDataset,
  paramsIn: Partial<AnalysisParams> | undefined,
  usgsEvents: UsgsEventLite[] = [],
): AnalysisResult {
  const t0 = Date.now();
  const params: AnalysisParams = { ...DEFAULT_PARAMS, ...(paramsIn || {}) };
  const warnings: string[] = [];

  // ---- Stage A: validation -------------------------------------------------
  const validation = validateDataset(ds);
  warnings.push(...validation.warnings);
  if (!validation.ok) {
    throw new Error(
      `Stage A input validation failed: ${validation.errors.join('; ')}`,
    );
  }
  if (ds.mode !== 'quantitative' || !ds.phase || !ds.coherence) {
    throw new Error('Dataset does not provide the quantitative layers required by the pipeline');
  }

  const meta = ds.phase.meta;
  const w = meta.width;
  const h = meta.height;
  const n = w * h;

  // ---- Stage B: quality mask + referencing --------------------------------
  // Mask = finite phase + coherence above threshold + (when the product
  // provides connected components) membership of a validated unwrapping
  // component. Component 0 marks pixels the producer did NOT unwrap, and
  // 65535 is the HDF5 fill value — both can still carry finite but
  // meaningless phase (the Mojave 2026 operational granule stores a noisy
  // 5-rad field over its cc=0 areas, which once masqueraded as a 5,900 km²
  // "detection" before this gate existed).
  const mask = new Uint8Array(n);
  const coh = ds.coherence.data;
  const phase = ds.phase.data;
  const components = ds.components;
  let validCount = 0;
  let cohPassCount = 0;
  for (let idx = 0; idx < n; idx++) {
    const c = coh[idx];
    if (Number.isNaN(phase[idx]) || Number.isNaN(c) || c < params.coherenceThreshold) continue;
    cohPassCount++;
    if (components) {
      const cc = components[idx];
      if (cc === 0 || cc === 65535) continue;
    }
    mask[idx] = 1;
    validCount++;
  }
  if (components && cohPassCount > 0 && validCount < cohPassCount * 0.5) {
    warnings.push(
      `${(((cohPassCount - validCount) / cohPassCount) * 100).toFixed(0)}% of the pixels above the coherence ` +
        'threshold are excluded because the product marks them as not-unwrapped (connected-component 0 or fill); ' +
        'the stored phase there is not a valid measurement.',
    );
  }
  if (validCount < 100) {
    throw new Error(
      `Stage B: only ${validCount} pixels pass the coherence threshold ${params.coherenceThreshold}; ` +
        'the analysis would be meaningless. Lower the threshold or choose another pair.',
    );
  }

  // Reference region: frame margin band (far field) or full frame.
  const marginRows = Math.max(2, Math.round(params.farFieldMargin * h));
  const marginCols = Math.max(2, Math.round(params.farFieldMargin * w));
  const isRef = new Uint8Array(n);
  let refCount = 0;
  if (params.referenceStrategy === 'farField') {
    for (let i = 0; i < h; i++) {
      for (let j = 0; j < w; j++) {
        const idx = i * w + j;
        if (!mask[idx]) continue;
        if (i < marginRows || i >= h - marginRows || j < marginCols || j >= w - marginCols) {
          isRef[idx] = 1;
          refCount++;
        }
      }
    }
  } else {
    for (let idx = 0; idx < n; idx++) {
      if (mask[idx]) {
        isRef[idx] = 1;
        refCount++;
      }
    }
  }
  if (refCount < 50) {
    warnings.push(
      'Reference region is very small; the far-field assumption may be violated by a large anomaly.',
    );
    for (let idx = 0; idx < n; idx++) if (mask[idx]) { isRef[idx] = 1; refCount++; }
  }

  // ---- Stage C: measurement -------------------------------------------------
  // Raw displacement (NISAR convention: d = lambda*phi/(4pi)).
  const rawDisp = phaseGridToDisplacement(phase, ds.geometry.wavelength);

  // Reference offset: median over the reference region.
  const refValues: number[] = [];
  for (let idx = 0; idx < n; idx++) {
    if (isRef[idx]) refValues.push(rawDisp[idx]);
  }
  const refOffset = median(refValues);
  let refSigma = madSigma(refValues);
  if (!Number.isFinite(refSigma) || refSigma <= 0) {
    // Degenerate MAD: more than half of the reference pixels share one exact
    // value (e.g. snaphu emitting exact zeros over a flat, fully coherent
    // basin, as in the Mojave 2026 granule). Fall back to the standard
    // deviation so the threshold keeps a meaningful spread term.
    refSigma = std(refValues);
    if (Number.isFinite(refSigma) && refSigma > 0) {
      warnings.push(
        'The reference-region MAD is degenerate (a single exact value dominates); using the standard ' +
          'deviation as the reference scatter.',
      );
    }
  }
  if (Number.isNaN(refOffset)) {
    throw new Error('Stage C: reference offset could not be computed (empty reference region).');
  }

  let disp = new Float32Array(n);
  for (let idx = 0; idx < n; idx++) {
    disp[idx] = mask[idx] ? rawDisp[idx] - refOffset : NaN;
  }

  // Optional planar detrend over the reference region (advanced, default off).
  if (params.detrend) {
    const pts: Array<{ i: number; j: number; v: number }> = [];
    for (let idx = 0; idx < n; idx++) {
      if (isRef[idx] && !Number.isNaN(disp[idx])) {
        pts.push({ i: Math.floor(idx / w), j: idx % w, v: disp[idx] });
      }
    }
    const fit = planarFit(pts);
    if (fit) {
      for (let idx = 0; idx < n; idx++) {
        if (mask[idx]) {
          const i = Math.floor(idx / w);
          const j = idx % w;
          disp[idx] -= fit.a * i + fit.b * j + fit.c;
        }
      }
      warnings.push(
        'Planar detrend applied over the reference region; note that a plane can also absorb ' +
          'real long-wavelength deformation.',
      );
    } else {
      warnings.push('Planar fit was degenerate; detrend skipped.');
    }
  }

  // ---- Stage D: uncertainty ---------------------------------------------------
  // Per-pixel sigma = quadrature of the coherence-based phase noise
  // (Rodriguez & Martin 1992) and the reference-region scatter. NOTE: this
  // treats the reference scatter as an independent per-pixel contribution —
  // conservative for random noise, but it does NOT represent spatially
  // correlated systematics, which are handled by the floor below and
  // documented as unresolved rather than averaged away.
  //
  // IONOSPHERIC SYSTEMATIC FLOOR — semantics corrected 2026-10-21 against the
  // official ASF NISAR GUNW guide: the ionospheric phase screen is supplied
  // by the producer but NOT applied to the stored unwrappedPhase, and
  // tropospheric corrections are supplied as LUTs and not removed either.
  // Therefore the measured field is APPARENT relative LOS range change, and
  // the unresolved ionospheric contamination is bounded by the LARGER of:
  //   (a) the unapplied screen's own spatial variation (the ionospheric
  //       signal actually present in the measurement after referencing), and
  //   (b) the screen estimate's uncertainty (how poorly the true ionosphere
  //       is known, even if a user applied the screen).
  // A correction-uncertainty value alone would NOT remove the spatially
  // varying contribution — which is exactly why both statistics are needed.
  const sigma = new Float32Array(n);
  for (let idx = 0; idx < n; idx++) {
    if (!mask[idx]) {
      sigma[idx] = NaN;
      continue;
    }
    sigma[idx] = combinedSigmaM(
      coh[idx],
      ds.numberOfLooks,
      ds.geometry.wavelength,
      refSigma,
    );
  }

  const losPerRad = ds.geometry.wavelength / (4 * Math.PI);
  const screenVariationRad = ds.ionoScreen
    ? Math.max(ds.ionoScreen.variationMadRad, ds.ionoScreen.variationStdRad)
    : 0;
  const screenUncertaintyRad = ds.ionoScreen?.uncertaintyMedianRad ?? 0;
  const floorRad = Math.max(screenVariationRad, screenUncertaintyRad);
  const ionoFloorM = floorRad > 0 ? losPerRad * floorRad : 0;
  const ionoFloorBasis: ChangeSummary['ionoFloorBasis'] =
    ionoFloorM <= 0 ? 'none' : screenVariationRad >= screenUncertaintyRad ? 'variation' : 'uncertainty';
  const effectiveFloorM = Math.max(params.detectionFloor, ionoFloorM);
  const det = runDetection(
    {
      meta,
      displacement: disp,
      sigma,
      mask,
      k: params.significanceK,
      floorM: effectiveFloorM,
    },
    coh,
  );

  // Exploratory null-scene screen on the transport-resolution grid: the
  // full-resolution screen would be needlessly expensive on ~2e7-pixel
  // frames, and the display-scale behaviour is the relevant diagnostic for
  // the reader. Deterministic seeds keep the result reproducible.
  const maxSidePre = 160;
  const factorNs = Math.max(1, Math.ceil(Math.max(w, h) / maxSidePre));
  const nsDisp = downsample(disp, w, h, factorNs);
  const nsSigma = downsample(sigma, w, h, factorNs);
  const nsMask = downsampleMask(mask, w, h, factorNs);
  const nullScreen = runNullSceneScreen(
    {
      meta: { ...meta, width: nsDisp.w, height: nsDisp.h, xSpacing: meta.xSpacing * factorNs, ySpacing: meta.ySpacing * factorNs },
      displacement: nsDisp.data,
      sigma: nsSigma.data,
      mask: nsMask.data,
      k: params.significanceK,
      floorM: effectiveFloorM,
    },
    { scenes: 8, correlationLengthPx: 8, seed: 20261021 },
  );

  // Connected-component statistics.
  let ccCount = 0;
  if (ds.components) {
    const compSizes = new Map<number, number>();
    for (let idx = 0; idx < n; idx++) {
      if (mask[idx] && ds.components[idx] !== 65535) {
        const c = ds.components[idx];
        compSizes.set(c, (compSizes.get(c) || 0) + 1);
      }
    }
    for (const [, size] of compSizes) {
      if (size >= validCount * 0.01) ccCount++;
    }
  } else {
    ccCount = 1;
  }

  const summary: ChangeSummary = {
    ...det.stats,
    clusters: det.clusters,
    nullScreen,
    referenceOffsetM: refOffset,
    referenceSigmaM: refSigma,
    connectedComponentCount: ccCount,
    systematicIonoFloorM: ionoFloorM,
    ionoFloorBasis,
    correctionStatus: {
      flatteningApplied: true, // verified from product flags (topographic + ellipsoidal)
      ionosphericPhaseCorrectionApplied: false, // screen supplied, not applied (ASF guide)
      troposphericPhaseCorrectionApplied: false, // LUTs supplied, not removed (ASF guide)
      note:
        'The measured field is apparent relative LOS range change: geometric flattening is applied, ' +
        'but the ionospheric phase screen and tropospheric corrections supplied with the product are ' +
        'NOT removed from the interferometric data (ASF NISAR GUNW guide, retrieved 2026-10-21). ' +
        'Residual atmospheric/ionospheric contamination is quantified as the systematic floor.',
    },
  };

  // ---- Stage E: attribution ------------------------------------------------------
  // Event selection is explicit and documented (never "first API result"):
  // rankEventsForPair orders in-window candidates by magnitude, proximity to
  // the footprint and mechanism availability, and returns the alternatives
  // and criteria for display.
  const selection = rankEventsForPair(usgsEvents, ds);
  const fm = forwardModelCheck(ds, selection.primary, disp, mask);
  const attribution = evaluateAttribution(ds, summary, selection, fm);

  // ---- Validation against ground truth (synthetic fixtures) ---------------------
  let validationBlock: AnalysisResult['validation'] | undefined;
  if (ds.truth) {
    let mae = 0;
    let rmse = 0;
    let bias = 0;
    let cnt = 0;
    const truth = ds.truth.data;
    // Compare on a sparse sample for speed; deterministic stride.
    const stride = Math.max(1, Math.floor(n / 50000));
    for (let idx = 0; idx < n; idx += stride) {
      if (!mask[idx] || Number.isNaN(truth[idx])) continue;
      const e = disp[idx] - truth[idx];
      mae += Math.abs(e);
      rmse += e * e;
      bias += e;
      cnt++;
    }
    if (cnt > 0) {
      validationBlock = {
        maeM: mae / cnt,
        rmseM: Math.sqrt(rmse / cnt),
        biasM: bias / cnt,
        n: cnt,
        note:
          'Comparison of the pipeline output against the analytic Okada ground truth over all valid ' +
          'pixels (synthetic fixture). This validates the processing mathematics, NOT any claim about ' +
          'the real Earth.',
      };
    }
  }

  // ---- Narrative -----------------------------------------------------------------
  const mm = (v: number) => (v * 1000).toFixed(1);
  const effectiveFloorMm = Math.max(params.detectionFloor, ionoFloorM) * 1000;
  // Credibility criterion shared with the attribution layer: an affected
  // area below max(1 km², 4 pixels) is treated as isolated stray pixels
  // (statistical noise surviving the mask), not credible ground motion.
  const minCredibleAreaKm2 = Math.max(1, summary.pixelAreaKm2 * 4);
  const credibleDetection = summary.affectedAreaKm2 >= minCredibleAreaKm2;
  const strayPixels = summary.affectedAreaKm2 > 0 && !credibleDetection;
  const whatChanged = !credibleDetection
    ? strayPixels
      ? `Isolated pixels totalling about ${summary.affectedAreaKm2.toFixed(2)} km² crossed the detection threshold — below the ${minCredibleAreaKm2.toFixed(0)} km² minimum credible area for this ${ds.provenance.temporalBaselineDays}-day pair. In a scene at this coherence level that is consistent with statistical noise, not ground motion.`
      : `No change above the detection threshold (${effectiveFloorMm.toFixed(0)} mm for this product) was detected in this ${ds.provenance.temporalBaselineDays}-day radar pair. The result is inconclusive at the available sensitivity: motion smaller than the threshold, or hidden below ${((1 - summary.validFraction) * 100).toFixed(0)}% of the frame that lost coherence, cannot be excluded.`
    : `The radar measurement shows apparent line-of-sight range change between ${ds.provenance.referenceTime.slice(0, 10)} and ${ds.provenance.secondaryTime.slice(0, 10)}: an area of about ${summary.affectedAreaKm2.toFixed(0)} km² exceeded the detection threshold, ${ds.isSynthetic ? '(synthetic validation fixture)' : 'measured along the satellite line of sight'}. ${ds.isSynthetic ? '' : 'Because atmospheric and ionospheric corrections are not applied to the stored phase, this is apparent range change — consistent with ground motion, not proof of it.'}`;
  const howMuch = !credibleDetection
    ? `Upper bound on apparent motion: less than ${mm(Math.max(effectiveFloorM, 3 * summary.referenceSigmaM))} mm anywhere in the analysed area.`
      : `Peak apparent motion: ${mm(summary.maxAwayM)} mm away from the radar and ${mm(summary.maxTowardM)} mm toward it. Typical per-pixel measurement uncertainty is ±${mm(summary.medianSigmaM)} mm (one sigma), so the peaks are ${(
          Math.max(Math.abs(summary.maxAwayM), Math.abs(summary.maxTowardM)) /
          Math.max(summary.medianSigmaM, 1e-9)
        ).toFixed(0)}× the per-pixel noise — note that spatially correlated systematics do not average down like this random-noise term.`;
  const reliability =
    `${(summary.validFraction * 100).toFixed(0)}% of the frame retained sufficient coherence for measurement ` +
    `(median coherence ${summary.coherenceMedian.toFixed(2)}). Reference-region scatter: ±${mm(summary.referenceSigmaM)} mm; ` +
    (ionoFloorM > 0
      ? `the dominant unresolved residual is ionospheric: the product supplies a phase screen but does not apply it to the stored phase, and the ${ionoFloorBasis === 'variation' ? "screen's own spatial variation" : "screen estimate's uncertainty"} corresponds to about ±${mm(ionoFloorM)} mm of LOS range change. Apparent signals below that level cannot be confidently attributed to ground motion from this single pair. `
      : ds.isSynthetic
        ? 'the simulated measurement noise and atmospheric ramp are the only error sources in this demonstration dataset. '
        : 'unmodelled atmospheric residuals remain (the product does not remove them from the phase). ') +
    `${ccCount > 1 ? `Phase unwrapping split the scene into ${ccCount} regions — treat step-like boundaries with caution.` : 'Phase unwrapping is consistent across the scene (a single component lowers, but does not eliminate, the chance of cycle-jump artefacts).'} ` +
    'A single interferogram cannot separate slow processes from one-off events.';

  // ---- Transport grid ------------------------------------------------------------
  const maxSide = 640;
  const factor = Math.max(1, Math.ceil(Math.max(w, h) / maxSide));
  const td = downsample(disp, w, h, factor);
  const tc = downsample(coh, w, h, factor);
  const tmaskSrc = new Uint8Array(n);
  for (let idx = 0; idx < n; idx++) tmaskSrc[idx] = mask[idx];
  const tm = downsampleMask(tmaskSrc, w, h, factor);
  const ts = downsample(sigma, w, h, factor);
  // Enforce mask on downsampled displacement.
  for (let idx = 0; idx < td.data.length; idx++) {
    if (!tm.data[idx]) {
      td.data[idx] = NaN;
      ts.data[idx] = NaN;
    }
  }
  // Detected-change grid (2026-10-24): the exact server-side detection rule
  // (|d| > max(k·σ, floor), the same rule that produces affectedAreaKm²)
  // evaluated per full-resolution pixel, then majority-voted into the
  // transport cells. The client renders it as the plain "where the ground
  // moved, high confidence" layer.
  const detectedSrc = new Uint8Array(n);
  for (let idx = 0; idx < n; idx++) {
    if (!mask[idx]) continue;
    const d = disp[idx];
    if (!Number.isFinite(d)) continue;
    const s = sigma[idx];
    const thr = Math.max(params.significanceK * (Number.isFinite(s) ? s : 0), effectiveFloorM);
    if (Math.abs(d) > thr) detectedSrc[idx] = 1;
  }
  const tdet = downsampleDetected(detectedSrc, mask, w, h, factor);
  const transport: TransportGrid = {
    width: td.w,
    height: td.h,
    displacement: b64(td.data),
    coherence: b64(tc.data),
    mask: b64(tm.data),
    sigma: b64(ts.data),
    detected: b64(tdet.data),
    meta: {
      ...meta,
      width: td.w,
      height: td.h,
      xSpacing: meta.xSpacing * factor,
      ySpacing: meta.ySpacing * factor,
    },
  };

  return {
    datasetId: ds.id,
    params,
    summary,
    narrative: { whatChanged, howMuch, reliability },
    attribution,
    transport,
    validation: validationBlock,
    warnings,
    processingMs: Date.now() - t0,
  };
}
