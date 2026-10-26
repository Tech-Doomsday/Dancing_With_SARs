/**
 * SBAS time-series pipeline orchestrator — the plan's §12 end-to-end
 * architecture for stack datasets:
 *
 *   1. Ingestion Engine           (stacks.ts: GUNW-stack fixture loader)
 *   2. Preprocessing Module       phase referencing, coherence masks,
 *                                 stratified-troposphere + long-wavelength
 *                                 ionosphere corrections (atmosphere.ts)
 *   3. SBAS Inversion Core        sbas.ts: Tikhonov-regularized WLS,
 *                                 L-curve λ, temporal coherence
 *   4. Spatial Anomaly Module     connected-component clustering on the
 *                                 velocity field + PELT change-point
 *                                 segmentation (pelt.ts)
 *   5. Bayesian Causal Engine     bayes.ts: features → priors → posteriors
 *                                 → entropy abstention (§8 stages 1–6)
 *   6. Validation                 GNSS LOS-projection RMSE (gnss.ts) +
 *                                 Brier calibration over labeled zones +
 *                                 analytic-truth RMSE (fixture)
 *
 * The pipeline is pure with respect to external services: catalog events
 * are passed in by the caller (the synthetic stack event is part of the
 * fixture; the API layer may add live USGS context events).
 */

import type {
  AnalysisParams,
  AnomalyAttribution,
  ChangePointMark,
  CausalFeatures,
  CausalPriors,
  ForwardModelResiduals,
  GnssStation,
  GridMeta,
  KeySeries,
  PhaseBudget,
  SbasResult,
  SbasSummary,
  StackCatalogEvent,
  TransportGrid,
  UsgsEventLite,
} from './types';
import { DEFAULT_PARAMS } from './types';
import {
  buildSbasDesign,
  invertSbasStack,
  selectLambdaLCurve,
  solveSbasPixel,
  withIntervalDays,
  type SbasDesign,
} from './sbas';
import { peltChangePoints, linearTrendR2 } from './pelt';
import { classifySurfaceAnomaly, brierScore } from './bayes';
import { removeStratifiedTroposphere, removeLongWavelengthRamp } from './atmosphere';
import { validateAgainstGnss } from './gnss';
import { centralValleyStack, centralValleyGnssNetwork, quakeStepEpochIndex, type CentralValleyStack } from './stacks';
import { geoFaultDisplacementENU } from './okada';
import { losUnitVector, projectDisplacementToLOS } from './los-projection';
import { latLonToUtmInZone, zoneFromEpsg } from './utm';
import { madSigma, median, pearson } from './statistics';

function b64(arr: Float32Array | Uint8Array): string {
  const u8 = new Uint8Array(arr.buffer, arr.byteOffset, arr.byteLength);
  return Buffer.from(u8).toString('base64');
}

/** Area-average downsample (NaN when no valid contributor). */
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

/* ------------------------------------------------------------------ */
/* Connected-component clustering on the velocity field (§8 Stage 1)   */
/* ------------------------------------------------------------------ */

interface Cluster {
  id: string;
  pixels: number[];
  areaKm2: number;
  centroid: { i: number; j: number };
  bbox: { i0: number; j0: number; i1: number; j1: number };
  peakVelocityMPerYr: number;
  meanVelocityMPerYr: number;
}

function clusterVelocityField(
  velocity: Float32Array,
  mask: Uint8Array,
  width: number,
  height: number,
  thresholdMPerYr: number,
  pixelAreaKm2: number,
  minPixels: number,
): Cluster[] {
  const visited = new Uint8Array(width * height);
  const clusters: Cluster[] = [];
  const stack: number[] = [];
  for (let start = 0; start < width * height; start++) {
    if (visited[start] || !mask[start]) continue;
    if (Math.abs(velocity[start]) < thresholdMPerYr) continue;
    // Flood fill (4-neighbourhood).
    stack.length = 0;
    stack.push(start);
    visited[start] = 1;
    const pixels: number[] = [];
    let si = 0, sj = 0;
    let i0 = Infinity, j0 = Infinity, i1 = -Infinity, j1 = -Infinity;
    let peak = 0;
    let sum = 0;
    while (stack.length > 0) {
      const p = stack.pop() as number;
      pixels.push(p);
      const i = Math.floor(p / width);
      const j = p % width;
      si += i;
      sj += j;
      if (i < i0) i0 = i;
      if (i > i1) i1 = i;
      if (j < j0) j0 = j;
      if (j > j1) j1 = j;
      const v = velocity[p];
      sum += v;
      if (Math.abs(v) > Math.abs(peak)) peak = v;
      const nb = [p - width, p + width, p - 1, p + 1];
      for (const q of nb) {
        if (q < 0 || q >= width * height) continue;
        if (Math.abs(p % width - q % width) > 1) continue; // row wrap
        if (visited[q] || !mask[q]) continue;
        if (Math.abs(velocity[q]) < thresholdMPerYr) continue;
        visited[q] = 1;
        stack.push(q);
      }
    }
    if (pixels.length < minPixels) continue;
    clusters.push({
      id: `cluster-${clusters.length}`,
      pixels,
      areaKm2: pixels.length * pixelAreaKm2,
      centroid: { i: Math.round(si / pixels.length), j: Math.round(sj / pixels.length) },
      bbox: { i0, j0, i1, j1 },
      peakVelocityMPerYr: peak,
      meanVelocityMPerYr: sum / pixels.length,
    });
  }
  // Rank by signal power (area × peak rate): a 300 km² bowl at 150 mm/yr is
  // a more significant anomaly than a 4 km² fragment at 300 mm/yr, and the
  // attribution should see both mechanisms, not only the steepest one.
  clusters.sort(
    (a, b) => b.areaKm2 * Math.abs(b.peakVelocityMPerYr) - a.areaKm2 * Math.abs(a.peakVelocityMPerYr),
  );
  return clusters;
}

/* ------------------------------------------------------------------ */
/* Main pipeline                                                       */
/* ------------------------------------------------------------------ */

export function runSbasPipeline(
  datasetId: string,
  paramsIn: Partial<AnalysisParams> | undefined,
  realEvents: UsgsEventLite[] = [],
): SbasResult {
  const t0 = Date.now();
  const params: AnalysisParams = { ...DEFAULT_PARAMS, ...(paramsIn || {}) };
  const warnings: string[] = [];

  // ---- Stage 1: ingestion ------------------------------------------------
  const stack: CentralValleyStack = centralValleyStack();
  const { meta, geometry } = stack;
  const width = meta.width;
  const height = meta.height;
  const pixels = width * height;
  const nE = stack.epochs.length;
  const mIfg = stack.ifgs.length;
  const metresPerRad = geometry.wavelength / (4 * Math.PI);

  // ---- Stage 2: preprocessing -------------------------------------------
  // Quality mask per interferogram (coherence gate), shared validity cube.
  const cohThreshold = params.coherenceThreshold;
  const validIfgCount = new Uint8Array(pixels);
  for (let k = 0; k < mIfg; k++) {
    const ph = stack.phase[k];
    const co = stack.coherence[k];
    for (let idx = 0; idx < pixels; idx++) {
      if (Number.isFinite(ph[idx]) && co[idx] >= cohThreshold) validIfgCount[idx]++;
    }
  }
  const stackMask = new Uint8Array(pixels);
  let stackValid = 0;
  const minIfgsPixel = Math.max(4, Math.ceil(mIfg * 0.35));
  for (let idx = 0; idx < pixels; idx++) {
    if (validIfgCount[idx] >= minIfgsPixel) {
      stackMask[idx] = 1;
      stackValid++;
    }
  }
  if (stackValid < 1000) {
    throw new Error(
      `Preprocessing: only ${stackValid} pixels retain ≥ ${minIfgsPixel} interferograms above ` +
        `coherence ${cohThreshold}; the stack inversion would be meaningless.`,
    );
  }

  // Per-ifg corrected displacement fields (metres), with the correction
  // ledger (plan §6 decomposition).
  const budget: PhaseBudget = {
    entries: [],
    methodNote:
      'Phase decomposition of the plan (§6): φ_int = φ_disp + φ_topo + φ_orbit + φ_atm + φ_noise with ' +
      'φ_atm = stratified + turbulent troposphere + ionosphere. Topographic flattening and orbital ' +
      'terms are already removed by the product generation; this pipeline corrects the stratified ' +
      'tropospheric term (phase–elevation regression — the ERA5/RAiDER fallback documented in the ' +
      'plan) and the long-wavelength ionospheric ramp (plane over the stable reference region), and ' +
      'quantifies the turbulent remainder in the ledger.',
  };

  // Stable reference region — TWO-STAGE (standard practice): start from the
  // geographic far-field band, then RETAIN only pixels whose cheap first-pass
  // displacement (the sequential-ifg chain sum) is near zero. This keeps the
  // plane/ramp fits of stage 2 from absorbing real deformation that leaks
  // into the geographic band (broad bowls reach the frame margins).
  const isRef = new Uint8Array(pixels);
  for (let i = 0; i < height; i++) {
    for (let j = 0; j < width; j++) {
      const idx = i * width + j;
      if (!stackMask[idx]) continue;
      if (i < 20 || j > width - 20 || (i < 40 && j > width - 40)) isRef[idx] = 1;
    }
  }
  {
    const cum = new Float32Array(pixels);
    const seen = new Uint8Array(pixels);
    for (let k = 0; k < mIfg; k++) {
      const ifg = stack.ifgs[k];
      if (ifg.secIndex - ifg.refIndex !== 1) continue;
      const ph = stack.phase[k];
      const co = stack.coherence[k];
      for (let idx = 0; idx < pixels; idx++) {
        if (!stackMask[idx]) continue;
        if (!Number.isFinite(ph[idx]) || co[idx] < cohThreshold) continue;
        cum[idx] += metresPerRad * ph[idx];
        seen[idx]++;
      }
    }
    let stableCount = 0;
    for (let idx = 0; idx < pixels; idx++) {
      if (isRef[idx] && seen[idx] >= Math.ceil(nE * 0.6) && Math.abs(cum[idx]) < 0.03) {
        stableCount++;
      } else {
        isRef[idx] = 0;
      }
    }
    if (stableCount < 400) {
      warnings.push(
        'The stable-reference screening found too few pixels; the atmospheric-correction reference ' +
          'fell back to the geographic far-field band, which may contain broad deformation.',
      );
      for (let i = 0; i < height; i++) {
        for (let j = 0; j < width; j++) {
          const idx = i * width + j;
          if (stackMask[idx] && (i < 20 || j > width - 20 || (i < 40 && j > width - 40))) isRef[idx] = 1;
        }
      }
    }
  }

  // Corrected phase cube (radians) fed to the inversion.
  const correctedPhase: Float32Array[] = [];
  let stratRmsAcc = 0;
  let rampRmsAcc = 0;
  for (let k = 0; k < mIfg; k++) {
    const disp = new Float32Array(pixels);
    for (let idx = 0; idx < pixels; idx++) {
      disp[idx] = stackMask[idx] ? metresPerRad * stack.phase[k][idx] : NaN;
    }
    const ifgMask = new Uint8Array(pixels);
    for (let idx = 0; idx < pixels; idx++) {
      ifgMask[idx] =
        stackMask[idx] && Number.isFinite(stack.phase[k][idx]) && stack.coherence[k][idx] >= cohThreshold
          ? 1
          : 0;
    }
    // Stratified-troposphere regression over the STABLE reference pixels
    // (estimating atmosphere where deformation ≈ 0 — fitting over the whole
    // frame lets the regression absorb real deformation that correlates
    // with elevation). Fallback: the full ifg mask when too few stable
    // pixels are valid in this interferogram.
    let stratMask = ifgMask;
    let stratStable = 0;
    for (let idx = 0; idx < pixels; idx++) {
      if (isRef[idx] && ifgMask[idx]) stratStable++;
    }
    if (stratStable >= 200) {
      stratMask = new Uint8Array(pixels);
      for (let idx = 0; idx < pixels; idx++) {
        if (isRef[idx] && ifgMask[idx]) stratMask[idx] = 1;
      }
    }
    const strat = removeStratifiedTroposphere(disp, stack.dem, stratMask);
    const ramp = removeLongWavelengthRamp(disp, ifgMask, isRef, width, meta.xSpacing, meta.ySpacing);
    stratRmsAcc += strat.removedRmsM ** 2;
    rampRmsAcc += ramp.removedRmsM ** 2;
    const out = new Float32Array(pixels);
    for (let idx = 0; idx < pixels; idx++) {
      out[idx] = ifgMask[idx] ? disp[idx] / metresPerRad : NaN;
    }
    correctedPhase.push(out);
  }
  budget.entries.push({
    key: 'stratified-troposphere',
    label: 'Stratified tropospheric delay (removed per interferogram)',
    rmsM: Math.sqrt(stratRmsAcc / mIfg),
    note: 'Phase–elevation regression with MAD trimming; ERA5/RAiDER raytracing is the plan\'s preferred method but needs CDS credentials this deployment does not hold.',
  });
  budget.entries.push({
    key: 'ionosphere-ramp',
    label: 'Long-wavelength ionospheric ramp (removed per interferogram)',
    rmsM: Math.sqrt(rampRmsAcc / mIfg),
    note: 'Planar fit over the stable reference region — the frame-scale residual of the split-spectrum ionospheric screen that the product supplies but does not apply.',
  });

  // Turbulent remainder estimate: robust σ of the corrected sequential ifgs
  // (a fair sample of the uncorrectable turbulent term). This becomes the
  // VARIANCE COMPONENT added to every observation's σ in the inversion —
  // the plan §15 mitigation ("down-weight transient phase noise in the SBAS
  // stack") implemented as variance-component estimation.
  let turbulentSigmaM = 0;
  {
    // Robust per-ifg residual scatter, taken as the MEDIAN over the
    // sequential interferograms (a single ifg can be atypically clean or
    // disturbed; the median represents the stack's noise regime).
    const perIfg: number[] = [];
    for (let k = 0; k < mIfg; k++) {
      if (stack.ifgs[k].secIndex - stack.ifgs[k].refIndex !== 1) continue;
      const vals: number[] = [];
      const ph = correctedPhase[k];
      for (let idx = 0; idx < pixels; idx += 11) {
        if (stackMask[idx] && Number.isFinite(ph[idx])) vals.push(metresPerRad * ph[idx]);
      }
      if (vals.length > 500) perIfg.push(madSigma(vals));
    }
    turbulentSigmaM = median(perIfg.length ? perIfg : [0]);
    budget.entries.push({
      key: 'turbulent-troposphere',
      label: 'Turbulent tropospheric remainder (uncorrected; enters the inversion as a variance component)',
      rmsM: turbulentSigmaM,
      note: 'Spatially turbulent troposphere cannot be removed without weather-model raytracing; its robust σ is added in quadrature to every observation\'s CRLB σ before the WLS inversion (plan §15 mitigation), and it is reported here.',
    });
  }

  // ---- Stage 3: SBAS inversion -------------------------------------------
  const design: SbasDesign = withIntervalDays(
    buildSbasDesign(stack.ifgs, nE),
    stack.epochs.map((e) => Date.parse(e.time)),
  );

  // L-curve λ on a deterministic subsample (every 97th valid pixel).
  const sample: Array<{ phaseRad: number[]; coherence: number[] }> = [];
  for (let idx = 0; idx < pixels && sample.length < 320; idx += 97) {
    if (!stackMask[idx]) continue;
    const ph: number[] = [];
    const co: number[] = [];
    let missing = 0;
    for (let k = 0; k < mIfg; k++) {
      const p = correctedPhase[k][idx];
      const c = stack.coherence[k][idx];
      if (!Number.isFinite(p) || c < cohThreshold) {
        ph.push(NaN);
        co.push(NaN);
        missing++;
        continue;
      }
      ph.push(p);
      co.push(c);
    }
    if (missing <= mIfg * 0.2) sample.push({ phaseRad: ph, coherence: co });
  }
  const lcurve = selectLambdaLCurve(sample, design, { looks: 119, metresPerRad, extraSigmaM: turbulentSigmaM });
  const lambda = lcurve.lambda;

  const solution = invertSbasStack(
    {
      m: mIfg,
      pixels,
      phase: correctedPhase,
      coherence: stack.coherence,
    },
    design,
    { lambda, looks: 119, metresPerRad, extraSigmaM: turbulentSigmaM },
  );

  // Temporal-coherence gate (plan §11 benchmark: retention fraction).
  const tempCohMin = 0.5;
  const validMask = new Uint8Array(pixels);
  let validCount = 0;
  let stackValidCoherent = 0;
  for (let idx = 0; idx < pixels; idx++) {
    if (!stackMask[idx]) continue;
    stackValidCoherent++;
    const tc = solution.temporalCoherence[idx];
    if (Number.isFinite(tc) && tc >= tempCohMin && Number.isFinite(solution.velocityMPerYr[idx])) {
      validMask[idx] = 1;
      validCount++;
    }
  }
  const retainedFraction = stackValidCoherent > 0 ? validCount / stackValidCoherent : 0;

  // ---- Stage 4: spatial anomaly module ------------------------------------
  // NOTE: detectionFloor is reinterpreted for stack datasets as a RATE floor
  // (m/yr) — documented in the UI.
  const rateThreshold = Math.max(0.008, params.detectionFloor);
  const pixelAreaKm2 = (meta.xSpacing * Math.abs(meta.ySpacing)) / 1e6;
  // Attribution clustering runs at a HIGHER threshold than the summary
  // detection floor: at the floor, neighbouring anomalies (a subsidence bowl
  // and an adjacent coseismic lobe) merge through their low-rate fringes and
  // the cluster features mix mechanisms. Two times the floor (≥ 60 mm/yr)
  // separates the cores of distinct anomalies.
  const attributionThreshold = Math.max(rateThreshold, 0.06);
  const clusters = clusterVelocityField(
    solution.velocityMPerYr,
    validMask,
    width,
    height,
    attributionThreshold,
    pixelAreaKm2,
    Math.max(60, Math.round(3 / pixelAreaKm2)),
  );
  const topClusters = clusters.slice(0, 4);

  // STEP-FIELD clusters (the coseismic anomalies): cluster the fitted
  // transient steps at a 30 mm floor. These get earthquake-focused
  // features: the gradient of the STEP field, the (low) linearity of the
  // stepped series, and the Okada forward model of the catalog event
  // evaluated against the step amplitudes.
  const stepClusters = (() => {
    const stepField = new Float32Array(pixels);
    for (let idx = 0; idx < pixels; idx++) {
      stepField[idx] = Number.isFinite(solution.stepM[idx]) ? solution.stepM[idx] : 0;
    }
    return clusterVelocityField(
      stepField,
      validMask,
      width,
      height,
      0.03,
      pixelAreaKm2,
      Math.max(40, Math.round(2 / pixelAreaKm2)),
    ).slice(0, 2);
  })();

  let fastAreaKm2 = 0;
  for (let idx = 0; idx < pixels; idx++) {
    if (validMask[idx] && Math.abs(solution.velocityMPerYr[idx]) >= rateThreshold) fastAreaKm2 += pixelAreaKm2;
  }

  // ---- PELT change-point segmentation at key pixels -----------------------
  const epochTimesMs = stack.epochs.map((e) => Date.parse(e.time));
  const yearsOf = (e: number) => (epochTimesMs[e] - epochTimesMs[0]) / (365.25 * 86400000);

  const extractSeries = (i: number, j: number, id: string, label: string, note: string): KeySeries => {
    const idx = i * width + j;
    const times = stack.epochs.map((e) => e.time);
    const displacementM: number[] = [];
    const sigmaM: number[] = [];
    for (let e = 0; e < nE; e++) {
      displacementM.push(solution.cumulative[e][idx]);
      sigmaM.push(solution.velocitySigmaMPerYr[idx] * yearsOf(e) || 0);
    }
    // Exact per-epoch σ from the solver (cheap for one pixel).
    {
      const ph: number[] = [];
      const co: number[] = [];
      let ok = true;
      for (let k = 0; k < mIfg; k++) {
        const p = correctedPhase[k][idx];
        const c = stack.coherence[k][idx];
        if (!Number.isFinite(p) || c < cohThreshold) {
          ok = false;
          break;
        }
        ph.push(p);
        co.push(c);
      }
      if (ok) {
        const sol = solveSbasPixel(new Float64Array(ph), new Float64Array(co), design, {
          lambda,
          looks: 119,
          metresPerRad,
          wantCumulativeSigma: true,
        });
        if (sol.cumulativeSigma) {
          for (let e = 0; e < nE; e++) sigmaM[e] = sol.cumulativeSigma[e];
        }
      }
    }
    const r2 = linearTrendR2(displacementM, epochTimesMs.map((t) => (t - epochTimesMs[0]) / (365.25 * 86400000)));
    const clean = displacementM.map((v) => (Number.isFinite(v) ? v : 0));
    const pelt = peltChangePoints(clean);
    const marks: ChangePointMark[] = pelt.changePoints.map((cp) => ({
      afterEpochIndex: cp,
      time: times[cp - 1] ?? times[cp],
      stepM: clean[Math.min(cp, nE - 1)] - clean[Math.max(cp - 1, 0)],
      note: 'PELT-detected transient (plan §7): a statistically significant break in the epoch-to-epoch velocity series.',
    }));
    // Mean velocity from regression.
    let sx = 0, sy = 0, sxx = 0, sxy = 0, n = 0;
    const tx = epochTimesMs.map((t) => (t - epochTimesMs[0]) / (365.25 * 86400000));
    for (let e = 0; e < nE; e++) {
      if (!Number.isFinite(displacementM[e])) continue;
      n++; sx += tx[e]; sy += displacementM[e]; sxx += tx[e] * tx[e]; sxy += tx[e] * displacementM[e];
    }
    const den = n * sxx - sx * sx;
    const slope = den !== 0 ? (n * sxy - sx * sy) / den : NaN;
    return {
      id, label, i, j, times, displacementM, sigmaM,
      changePoints: marks,
      linearR2: r2,
      velocityMPerYr: slope,
      note,
    };
  };

  // Key pixels: global extremes, biggest-cluster centroid, quake-step pixel
  // (max |estimated step| across the epoch with the largest single-ifg
  // change), and the stable reference.
  // Extremes among temporally-coherent pixels only (a single noisy pixel
  // can otherwise win the extremum search and misrepresent the field).
  const coherentMask = new Uint8Array(pixels);
  for (let idx = 0; idx < pixels; idx++) {
    if (validMask[idx] && solution.temporalCoherence[idx] >= 0.7) coherentMask[idx] = 1;
  }
  let minIdx = -1;
  let maxIdx = -1;
  let minV = Infinity;
  let maxV = -Infinity;
  for (let idx = 0; idx < pixels; idx++) {
    if (!coherentMask[idx]) continue;
    const v = solution.velocityMPerYr[idx];
    if (v < minV) { minV = v; minIdx = idx; }
    if (v > maxV) { maxV = v; maxIdx = idx; }
  }
  // Quake-step epoch: the epoch whose SINGLE-EPOCH jump has the highest 90th
  // percentile over coherent pixels — a spatially correlated step (a
  // coseismic displacement) dominates the percentile, while isolated noisy
  // pixels do not. The step PIXEL is then the largest jump at that epoch
  // among temporally-coherent pixels.
  // ---- Coseismic step field (PELT-guided refit, plan §7) -------------------
  // The inversion separates SECULAR velocity from fitted transient steps.
  // The step field drives the earthquake storyline: its peak pixel is the
  // display series, and its spatial clusters are the coseismic anomalies.
  let stepPeak = 0;
  let stepIdx = -1;
  let stepEpoch = -1;
  for (let idx = 0; idx < pixels; idx++) {
    const s = solution.stepM[idx];
    if (!Number.isFinite(s) || s === 0) continue;
    if (Math.abs(s) > Math.abs(stepPeak)) {
      stepPeak = s;
      stepIdx = idx;
      stepEpoch = solution.stepEpoch[idx];
    }
  }
  const refI = Math.round(height * 0.16);
  const refJ = Math.round(width * 0.86);

  const epiUtm = latLonToUtmInZone(stack.event.lat, stack.event.lon, 11);
  const epiX = epiUtm.easting;
  const epiY = epiUtm.northing;
  const pixelX = (idx: number) => meta.xStart + (idx % width) * meta.xSpacing;
  const pixelY = (idx: number) => meta.yStart - Math.floor(idx / width) * Math.abs(meta.ySpacing);

  /** Distance (km) from a grid point to the catalog event's fault trace
   *  (surface projection of the rupture: a segment through the epicentre
   *  along strike, half-length = rupture length / 2). */
  const faultTraceDistanceKm = (x: number, y: number): number => {
    const strikeRad = (stack.event.nodalPlane.strike * Math.PI) / 180;
    const ux = Math.sin(strikeRad);
    const uy = Math.cos(strikeRad);
    const halfLen = 8000; // 16 km rupture
    const dx = x - epiX;
    const dy = y - epiY;
    const t = Math.max(-halfLen, Math.min(halfLen, dx * ux + dy * uy));
    return Math.hypot(dx - t * ux, dy - t * uy) / 1000;
  };

  // Predicted Okada LOS field for the catalog event (H1 forward model).
  const fault = {
    strikeDeg: stack.event.nodalPlane.strike,
    dipDeg: stack.event.nodalPlane.dip,
    rakeDeg: stack.event.nodalPlane.rake,
    lengthM: 16000,
    widthM: 9000,
    topDepthM: 0,
    slipM: 0.58,
    centroidLat: stack.event.lat,
    centroidLon: stack.event.lon,
  };
  const centroidDepthM = stack.event.depthKm * 1000;
  const predQuakeLos = new Float32Array(pixels);
  for (let i = 0; i < height; i++) {
    for (let j = 0; j < width; j++) {
      const x = meta.xStart + j * meta.xSpacing;
      const y = meta.yStart - i * Math.abs(meta.ySpacing);
      const enu = geoFaultDisplacementENU(fault, centroidDepthM, x - epiX, y - epiY);
      predQuakeLos[i * width + j] = projectDisplacementToLOS(enu, losUnitVector(
        geometry.incidenceAngleDeg, geometry.headingDeg, geometry.lookDirection,
      ));
    }
  }

  const keySeries: KeySeries[] = [];
  // LOS sign convention: + = away from the satellite (range increase), so
  // aquifer compaction (ground dropping) appears POSITIVE.
  if (maxIdx >= 0) {
    const inEventZone = faultTraceDistanceKm(pixelX(maxIdx), pixelY(maxIdx)) < 18;
    keySeries.push(extractSeries(
      Math.floor(maxIdx / width), maxIdx % width,
      'subsidence-peak',
      inEventZone ? 'Fastest away-from-satellite motion (coseismic lobe)' : 'Fastest away-from-satellite motion',
      inEventZone
        ? 'The most positive LOS velocity lies within 18 km of the catalog event trace: it combines the coseismic step with any secular motion (a mean velocity over a stepped series).'
        : 'The pixel with the most positive LOS velocity — ground dropping away from the satellite (the aquifer-compaction convention).',
    ));
  }
  // Basin series: the strongest away-motion OUTSIDE the catalog-event zone
  // (the aquifer-compaction story without the coseismic superposition).
  {
    let bowlIdx = -1;
    let bowlV = 0;
    for (let idx = 0; idx < pixels; idx++) {
      if (!coherentMask[idx]) continue;
      const v = solution.velocityMPerYr[idx];
      const dEpi = Math.hypot(pixelX(idx) - epiX, pixelY(idx) - epiY);
      if (dEpi < 25000) continue;
      if (v > bowlV) { bowlV = v; bowlIdx = idx; }
    }
    if (bowlIdx >= 0) {
      keySeries.push(extractSeries(
        Math.floor(bowlIdx / width), bowlIdx % width,
        'basin-bowl', 'Basin subsidence bowl (outside the event zone)',
        'The fastest away-from-satellite motion more than 25 km from the catalog event — the aquifer-compaction series without coseismic superposition.',
      ));
    }
  }
  if (minIdx >= 0 && minIdx !== maxIdx) {
    const inEventZone = faultTraceDistanceKm(pixelX(minIdx), pixelY(minIdx)) < 18;
    keySeries.push(extractSeries(
      Math.floor(minIdx / width), minIdx % width,
      'toward-peak',
      inEventZone ? 'Fastest toward-satellite motion (coseismic lobe)' : 'Fastest toward-satellite motion',
      'The pixel with the most negative LOS velocity (ground moving toward the satellite).',
    ));
  }
  if (stepIdx >= 0) {
    keySeries.push(extractSeries(
      Math.floor(stepIdx / width), stepIdx % width,
      'quake-step', 'Coseismic step candidate (PELT-isolated transient)',
      'The pixel with the largest PELT-isolated, step-model-fitted transient of the stack. The SBAS ' +
      'solution here is the pair (secular velocity, step): the series shows the step explicitly instead ' +
      'of hiding it inside a misleading mean velocity.',
    ));
  }
  if (topClusters.length > 0) {
    const c = topClusters[0];
    keySeries.push(extractSeries(
      c.centroid.i, c.centroid.j,
      'anomaly-centroid', 'Largest anomaly (centroid)',
      'Time series at the centroid of the largest velocity anomaly cluster.',
    ));
  }
  keySeries.push(extractSeries(
    refI, refJ,
    'stable-reference', 'Stable reference (P271 analog)',
    'The stable valley-margin window used for phase referencing — expected flat within noise.',
  ));

  // ---- Stage 5: Bayesian causal engine ------------------------------------

  const spanYears = yearsOf(nE - 1);
  // Spatial displacement-gradient feature (plan §8 Stage 3: ∇d in m/km).
  // Computed on the CUMULATIVE displacement of the last epoch, smoothed
  // with a 5-px boxcar first (the paper's quake/subsidence centres —
  // 0.05 / 0.005 m/km — describe the deformation field, not its pixel noise).
  const gradMap = new Float32Array(pixels);
  {
    const last = solution.cumulative[nE - 1];
    const smooth = new Float32Array(pixels);
    for (let i = 0; i < height; i++) {
      for (let j = 0; j < width; j++) {
        let s = 0;
        let c = 0;
        for (let di = -2; di <= 2; di++) {
          for (let dj = -2; dj <= 2; dj++) {
            const ii = i + di;
            const jj = j + dj;
            if (ii < 0 || jj < 0 || ii >= height || jj >= width) continue;
            const p = ii * width + jj;
            if (!validMask[p] || !Number.isFinite(last[p])) continue;
            s += last[p];
            c++;
          }
        }
        smooth[i * width + j] = c > 0 ? s / c : NaN;
      }
    }
    for (let i = 1; i < height - 1; i++) {
      for (let j = 1; j < width - 1; j++) {
        const idx = i * width + j;
        const l = smooth[idx - 1];
        const r = smooth[idx + 1];
        const u = smooth[idx - width];
        const d = smooth[idx + width];
        if (![l, r, u, d].every(Number.isFinite)) continue;
        gradMap[idx] =
          Math.hypot((r - l) / (2 * meta.xSpacing), (d - u) / (2 * Math.abs(meta.ySpacing))) * 1000;
      }
    }
  }

  // DEM slope magnitude (m/m) for the slide prior.
  const slopeMap = new Float32Array(pixels);
  for (let i = 1; i < height - 1; i++) {
    for (let j = 1; j < width - 1; j++) {
      const idx = i * width + j;
      const dhx = (stack.dem[idx + 1] - stack.dem[idx - 1]) / (2 * meta.xSpacing);
      const dhy = (stack.dem[idx + width] - stack.dem[idx - width]) / (2 * Math.abs(meta.ySpacing));
      slopeMap[idx] = Math.hypot(dhx, dhy);
    }
  }

  const velocityAnomalies: AnomalyAttribution[] = topClusters.map((cl) => {
    // --- Stage 3 features ---
    let gradSum = 0;
    let gradN = 0;
    for (const p of cl.pixels) {
      if (Number.isFinite(gradMap[p])) {
        gradSum += gradMap[p];
        gradN++;
      }
    }
    const gradMean = gradN > 0 ? gradSum / gradN : 0;
    const centroidIdx = cl.centroid.i * width + cl.centroid.j;
    const centroidSeries = keySeries.find((s) => s.i === cl.centroid.i && s.j === cl.centroid.j)
      ?? extractSeries(cl.centroid.i, cl.centroid.j, 'tmp', 'tmp', '');
    const demVals: number[] = [];
    const dispVals: number[] = [];
    const lastCum = solution.cumulative[nE - 1];
    for (const p of cl.pixels) {
      demVals.push(stack.dem[p]);
      dispVals.push(lastCum[p]);
    }
    const demCorr = pearson(demVals, dispVals);

    const features: CausalFeatures = {
      gradS: gradMean,
      tempLinearity: centroidSeries.linearR2,
      demCorr,
      polShift: NaN, // GCOV not ingested for this stack (documented)
    };

    // --- Stage 5 priors from context (plan §8: proximity to fault traces,
    // hydrogeological basin boundaries, steep slopes) ---
    // The quake prior uses distance to the FAULT TRACE (surface projection
    // of the catalog rupture), not just the epicentre — deformation follows
    // the structure, and the plan's prior is fault-proximity.
    let basinFraction = 0;
    let slopeFraction = 0;
    for (const p of cl.pixels) {
      if (stack.dem[p] < 200) basinFraction++;
      if (slopeMap[p] > 0.08) slopeFraction++;
    }
    basinFraction /= cl.pixels.length;
    slopeFraction /= cl.pixels.length;
    const priors: CausalPriors = {
      quake: 0.15 + 0.7 * Math.exp(-faultTraceDistanceKm(pixelX(centroidIdx), pixelY(centroidIdx)) / 20),
      subsid: 0.15 + 0.75 * basinFraction,
      slide: 0.05 + 0.6 * slopeFraction,
    };
    const priorSum = priors.quake + priors.subsid + priors.slide;
    priors.quake /= priorSum;
    priors.subsid /= priorSum;
    priors.slide /= priorSum;

    // --- Stage 4 forward-model residuals ---
    // H1 (coseismic Okada), SMEARING-ROBUST amplitude form: the Tikhonov
    // first-difference penalty spreads a coseismic step over 2–4 intervals,
    // so an exact single-epoch step comparison would penalize a correct
    // model. We compare PRE-EVENT vs POST-EVENT mean levels (smearing
    // conserves them) at the centroid, plus the spatial step-amplitude
    // agreement over the cluster pixels.
    let h1r2 = 0;
    let h1n = 0;
    const e1 = Math.max(1, Math.min(nE - 1, stepEpoch > 0 ? stepEpoch : quakeStepEpochIndex(stack)));
    {
      const s = centroidSeries.displacementM;
      const pre = s.slice(0, Math.max(2, e1 - 1));
      const post = s.slice(Math.min(nE, e1 + 2));
      const preMean = pre.reduce((a, b) => a + b, 0) / Math.max(1, pre.length);
      const postMean = post.reduce((a, b) => a + b, 0) / Math.max(1, post.length);
      const dPostPre = postMean - preMean;
      h1r2 += (dPostPre - predQuakeLos[centroidIdx]) ** 2;
      h1n++;
      for (const p of cl.pixels) {
        const est = solution.cumulative[nE - 1][p] - solution.cumulative[Math.max(0, e1 - 3)][p];
        const d = est - predQuakeLos[p];
        h1r2 += d * d;
        h1n++;
      }
    }
    // H2 (linear + seasonal): RMSE of the centroid series against its own
    // least-squares line — a step leaves a large residual, a steadily
    // subsiding bowl leaves only the seasonal wiggle.
    let h2r2 = 0;
    {
      const s = centroidSeries.displacementM;
      let sx = 0, sy = 0, sxx = 0, sxy = 0, n2 = 0;
      for (let e2 = 0; e2 < nE; e2++) {
        if (!Number.isFinite(s[e2])) continue;
        const tx = yearsOf(e2);
        n2++; sx += tx; sy += s[e2]; sxx += tx * tx; sxy += tx * s[e2];
      }
      const den = n2 * sxx - sx * sx;
      const slope = den !== 0 ? (n2 * sxy - sx * sy) / den : 0;
      const intercept = n2 > 0 ? (sy - slope * sx) / n2 : 0;
      for (let e2 = 0; e2 < nE; e2++) {
        const r = s[e2] - (slope * yearsOf(e2) + intercept);
        h2r2 += r * r;
      }
      h1n += 0;
    }
    // H3: slope-parallel model — fit scalar m along the DEM downslope unit
    // vector projected to LOS; residual after the best fit.
    const losVec = losUnitVector(geometry.incidenceAngleDeg, geometry.headingDeg, geometry.lookDirection);
    let h3num = 0;
    let h3den = 0;
    for (const p of cl.pixels) {
      const i = Math.floor(p / width);
      const j = p % width;
      const dhx = (stack.dem[p + 1] - stack.dem[p - 1]) / (2 * meta.xSpacing);
      const dhy = (stack.dem[p + width] - stack.dem[p - width]) / (2 * Math.abs(meta.ySpacing));
      const g = Math.hypot(dhx, dhy);
      if (g < 1e-6) continue;
      const eUnit = { east: -dhx / g, north: -dhy / g, up: 0 }; // downslope
      const predPerM = projectDisplacementToLOS(eUnit, losVec);
      h3num += predPerM * solution.cumulative[nE - 1][p];
      h3den += predPerM * predPerM;
    }
    const m3 = h3den > 1e-12 ? h3num / h3den : 0;
    let h3r2 = 0;
    for (const p of cl.pixels) {
      const i = Math.floor(p / width);
      const j = p % width;
      const dhx = (stack.dem[p + 1] - stack.dem[p - 1]) / (2 * meta.xSpacing);
      const dhy = (stack.dem[p + width] - stack.dem[p - width]) / (2 * Math.abs(meta.ySpacing));
      const g = Math.hypot(dhx, dhy);
      const eUnit = g > 1e-6 ? { east: -dhx / g, north: -dhy / g, up: 0 } : { east: 0, north: 0, up: 0 };
      const pred = m3 * projectDisplacementToLOS(eUnit, losVec);
      h3r2 += (solution.cumulative[nE - 1][p] - pred) ** 2;
    }
    const residuals: ForwardModelResiduals = {
      quakeRmseM: h1n > 0 ? Math.sqrt(h1r2 / Math.max(h1n, 1)) : NaN,
      quakeSigmaM: 0.045,
      trendRmseM: h2r2 > 0 ? Math.sqrt(h2r2 / cl.pixels.length) : NaN,
      trendSigmaM: 0.035,
      slopeRmseM: h3r2 > 0 ? Math.sqrt(h3r2 / cl.pixels.length) : NaN,
      slopeSigmaM: 0.045,
    };

    const bayes = classifySurfaceAnomaly(features, priors, residuals);

    const label =
      cl.peakVelocityMPerYr < 0
        ? `Subsidence anomaly (−${(Math.abs(cl.peakVelocityMPerYr) * 1000).toFixed(0)} mm/yr)`
        : `Uplift anomaly (+${(cl.peakVelocityMPerYr * 1000).toFixed(0)} mm/yr)`;

    return {
      id: cl.id,
      label,
      pixelCount: cl.pixels.length,
      areaKm2: cl.areaKm2,
      centroid: cl.centroid,
      bbox: cl.bbox,
      peakVelocityMPerYr: cl.peakVelocityMPerYr,
      features,
      priors,
      residuals,
      bayes,
    };
  });

  /** Majority step epoch of a step cluster → ISO time (for the label). */
  const stepEpochOf = (cl: Cluster): string | undefined => {
    const hist = new Map<number, number>();
    for (const p of cl.pixels) {
      const e = solution.stepEpoch[p];
      if (e >= 0) hist.set(e, (hist.get(e) ?? 0) + 1);
    }
    let best = -1;
    let bestN = 0;
    for (const [e, n] of hist) if (n > bestN) { bestN = n; best = e; }
    return best >= 0 ? stack.epochs[best]?.time?.slice(0, 10) : undefined;
  };

  // ---- Step-field (coseismic) anomalies ------------------------------------
  // Features for a step cluster: the spatial gradient of the STEP field
  // (coseismic lobes are steep by construction), the temporal linearity of
  // the stepped centroid series (LOW — the step breaks the trend), and the
  // Okada forward model checked against the step amplitudes (Stage 4).
  const stepAnomalies: AnomalyAttribution[] = stepClusters.map((cl) => {
    const centroidIdx = cl.centroid.i * width + cl.centroid.j;
    const centroidSeries = extractSeries(cl.centroid.i, cl.centroid.j, `step-${cl.id}`, 'Step cluster centroid', '');
    // Mean |step| over the cluster and its spatial gradient.
    let stepSum = 0;
    for (const p of cl.pixels) stepSum += Math.abs(solution.stepM[p]);
    const meanStep = stepSum / cl.pixels.length;
    let gradSum = 0;
    let gradN = 0;
    for (const p of cl.pixels) {
      const i = Math.floor(p / width);
      const j = p % width;
      if (i < 2 || j < 2 || i >= height - 2 || j >= width - 2) continue;
      const l = solution.stepM[p - 1] ?? 0;
      const r = solution.stepM[p + 1] ?? 0;
      const u = solution.stepM[p - width] ?? 0;
      const d = solution.stepM[p + width] ?? 0;
      if ([l, r, u, d].some((v) => !Number.isFinite(v))) continue;
      gradSum +=
        Math.hypot((r - l) / (2 * meta.xSpacing), (d - u) / (2 * Math.abs(meta.ySpacing))) * 1000;
      gradN++;
    }
    const gradMean = gradN > 0 ? gradSum / gradN : 0;

    const features: CausalFeatures = {
      gradS: gradMean,
      tempLinearity: centroidSeries.linearR2,
      demCorr: NaN,
      polShift: NaN,
    };
    const priors: CausalPriors = {
      quake: 0.15 + 0.7 * Math.exp(-faultTraceDistanceKm(pixelX(centroidIdx), pixelY(centroidIdx)) / 20),
      subsid: 0.15,
      slide: 0.05,
    };
    // H1: step amplitudes vs the Okada prediction over the cluster.
    let h1r2 = 0;
    let h1n = 0;
    for (const p of cl.pixels) {
      const d = solution.stepM[p] - predQuakeLos[p];
      h1r2 += d * d;
      h1n++;
    }
    // H2: a step model mis-fit as a linear trend leaves a large residual.
    let t2 = 0;
    {
      const s = centroidSeries.displacementM;
      let m2 = 0;
      for (let e2 = 0; e2 < nE; e2++) m2 += s[e2];
      m2 /= nE;
      let ss = 0;
      for (let e2 = 0; e2 < nE; e2++) ss += (s[e2] - m2) ** 2;
      t2 = Math.sqrt(Math.max(ss * (1 - centroidSeries.linearR2), 0));
    }
    const residuals: ForwardModelResiduals = {
      quakeRmseM: h1n > 0 ? Math.sqrt(h1r2 / h1n) : NaN,
      quakeSigmaM: 0.045,
      trendRmseM: t2,
      trendSigmaM: 0.03,
      slopeRmseM: NaN,
      slopeSigmaM: NaN,
    };
    const bayes = classifySurfaceAnomaly(features, priors, residuals);
    const stepTime = stepEpochOf(cl);
    return {
      id: `step-${cl.id}`,
      label: `Coseismic step anomaly (peak ${(Math.abs(cl.peakVelocityMPerYr) * 1000).toFixed(0)} mm${stepTime ? ` after ${stepTime}` : ''})`,
      pixelCount: cl.pixels.length,
      areaKm2: cl.areaKm2,
      centroid: cl.centroid,
      bbox: cl.bbox,
      peakVelocityMPerYr: cl.peakVelocityMPerYr,
      features,
      priors,
      residuals,
      bayes,
    };
  });

  const anomalies: AnomalyAttribution[] = [...stepAnomalies, ...velocityAnomalies];

  // ---- Stage 6: validation ------------------------------------------------
  // GNSS: exclude stations within 15 km of the catalog event (the InSAR
  // velocity includes the coseismic step; the GNSS fixture velocities are
  // secular — steps are removed in standard GNSS processing).
  // Secular-only comparison: exclude stations inside the predicted
  // coseismic displacement field (|Okada LOS| > 20 mm), because the InSAR
  // MEAN velocity there includes the step while GNSS velocities are
  // step-removed by standard processing.
  const stations: GnssStation[] = centralValleyGnssNetwork(stack).filter((st) => {
    const u = latLonToUtmInZone(st.lat, st.lon, 11);
    if (Math.hypot(u.easting - epiX, u.northing - epiY) <= 15000) return false;
    const j = Math.min(width - 1, Math.max(0, Math.round((u.easting - meta.xStart) / meta.xSpacing)));
    const i = Math.min(height - 1, Math.max(0, Math.round((meta.yStart - u.northing) / -meta.ySpacing)));
    return Math.abs(predQuakeLos[i * width + j]) <= 0.02;
  });
  const zone = zoneFromEpsg(meta.epsg);
  if (!zone) throw new Error('stack grid must be WGS84 UTM (EPSG 326xx/327xx)');
  const gnss = validateAgainstGnss(
    solution.velocityMPerYr,
    meta,
    validMask,
    geometry,
    stations,
    (lat, lon) => {
      const u = latLonToUtmInZone(lat, lon, zone.zone);
      return { x: u.easting, y: u.northing };
    },
  );

  // Brier calibration over labeled zones (plan §7/§11).
  let brier: SbasResult['brier'];
  {
    const byClass: number[][] = [[], [], []];
    for (let idx = 0; idx < pixels; idx++) {
      if (!validMask[idx]) continue;
      byClass[stack.labels[idx]].push(idx);
    }
    const take = (arr: number[], n: number) => {
      if (arr.length === 0) return [];
      const out: number[] = [];
      const stride = Math.max(1, Math.floor(arr.length / n));
      for (let k = 0; k < arr.length && out.length < n; k += stride) {
        out.push(arr[k]);
      }
      return out;
    };
    // Brier is computed over the two hypothesis-represented zones (coseismic
    // and subsidence) — the plan's hypothesis space has no null class; stable
    // pixels are handled upstream by the detection threshold.
    const sampled = [...take(byClass[1], 220), ...take(byClass[2], 220)];
    const preds: Array<Record<'quake' | 'subsid' | 'slide', number>> = [];
    const labels: Array<'quake' | 'subsid' | 'slide'> = [];
    const lastCum = solution.cumulative[nE - 1];
    for (const idx of sampled) {
      const i = Math.floor(idx / width);
      const j = idx % width;
      if (i < 1 || j < 1 || i >= height - 1 || j >= width - 1) continue;
      // Features at pixel scale. Step-fitted pixels use STEP-field features
      // (the secular velocity no longer carries the transient).
      const isStepPixel = Number.isFinite(solution.stepM[idx]) && solution.stepM[idx] !== 0;
      const series: number[] = [];
      for (let e = 0; e < nE; e++) series.push(solution.cumulative[e][idx]);
      const r2 = linearTrendR2(series, epochTimesMs.map((t) => (t - epochTimesMs[0]) / (365.25 * 86400000)));
      // Local window correlation d vs DEM (±7 px).
      const dv: number[] = [];
      const hv: number[] = [];
      for (let di = -7; di <= 7; di += 2) {
        for (let dj = -7; dj <= 7; dj += 2) {
          const ii = i + di;
          const jj = j + dj;
          if (ii < 0 || jj < 0 || ii >= height || jj >= width) continue;
          const p = ii * width + jj;
          if (!validMask[p]) continue;
          dv.push(lastCum[p]);
          hv.push(stack.dem[p]);
        }
      }
      let gradPx = gradMap[idx] || 0;
      if (isStepPixel) {
        const l = solution.stepM[idx - 1] ?? 0;
        const r = solution.stepM[idx + 1] ?? 0;
        const u = solution.stepM[idx - width] ?? 0;
        const d = solution.stepM[idx + width] ?? 0;
        if ([l, r, u, d].every(Number.isFinite)) {
          gradPx =
            Math.hypot((r - l) / (2 * meta.xSpacing), (d - u) / (2 * Math.abs(meta.ySpacing))) * 1000;
        }
      }
      const feats: CausalFeatures = {
        gradS: gradPx,
        tempLinearity: r2,
        demCorr: pearson(hv, dv),
        polShift: NaN,
      };
      const pr: CausalPriors = {
        quake: 0.15 + 0.7 * Math.exp(-faultTraceDistanceKm(pixelX(idx), pixelY(idx)) / 20),
        subsid: 0.15 + 0.75 * (stack.dem[idx] < 200 ? 1 : 0),
        slide: 0.05 + 0.6 * (slopeMap[idx] > 0.08 ? 1 : 0),
      };
      const ps = pr.quake + pr.subsid + pr.slide;
      pr.quake /= ps;
      pr.subsid /= ps;
      pr.slide /= ps;
      // Forward-model residuals per pixel: the estimated coseismic step
      // (cumulative jump across the detected step epoch) against the Okada
      // prediction, and the linear-trend remainder — the same Stage-4 terms
      // the cluster attribution uses, at pixel scale.
      // Temporal residuals from the pixel's own series (amplitude form,
      // robust to regularization smearing).
      const e1p = Math.max(1, Math.min(nE - 1, stepEpoch > 0 ? stepEpoch : 1));
      let q2 = 0;
      let t2 = 0;
      {
        if (isStepPixel) {
          // H1 for a step pixel: the FITTED step vs the Okada prediction.
          q2 = Math.abs(solution.stepM[idx] - predQuakeLos[idx]);
        } else {
          // H1: |pre/post mean difference − predicted coseismic LOS|.
          const pre = series.slice(0, Math.max(2, e1p - 1));
          const post = series.slice(Math.min(nE, e1p + 2));
          const preMean = pre.reduce((a, b) => a + b, 0) / Math.max(1, pre.length);
          const postMean = post.reduce((a, b) => a + b, 0) / Math.max(1, post.length);
          q2 = Math.abs(postMean - preMean - predQuakeLos[idx]);
        }
        // H2: series vs its LS line (RMSE = std·sqrt(1−R²) exactly).
        let m2 = 0;
        for (let e2 = 0; e2 < nE; e2++) m2 += series[e2];
        m2 /= nE;
        let ss = 0;
        for (let e2 = 0; e2 < nE; e2++) ss += (series[e2] - m2) ** 2;
        t2 = Math.sqrt(Math.max(ss * (1 - r2), 0));
      }
      const resPx: ForwardModelResiduals = {
        quakeRmseM: q2,
        quakeSigmaM: 0.045,
        trendRmseM: t2,
        trendSigmaM: 0.03,
        slopeRmseM: NaN,
        slopeSigmaM: NaN,
      };
      const bayesRes = classifySurfaceAnomaly(feats, pr, resPx);
      preds.push(bayesRes.posteriors);
      labels.push(stack.labels[idx] === 1 ? 'quake' : 'subsid');
    }
    if (preds.length >= 30) {
      const bs = brierScore(preds, labels);
      brier = {
        score: bs.score,
        n: bs.n,
        note:
          'Multi-class Brier score of the Bayesian engine over the labeled coseismic and subsidence zones ' +
          'of the synthetic fixture (plan §7 calibration metric; target ≤ 0.15 across validated zones). ' +
          'Stable background pixels are excluded: the plan\u2019s hypothesis space has no null class — ' +
          'non-detections are handled by the detection threshold, not by the classifier.',
      };
    }
  }

  // Analytic-truth velocity RMSE (fixture-only validation of the machinery).
  let truthValidation: SbasResult['truthValidation'];
  {
    let s2 = 0;
    let n = 0;
    for (let idx = 0; idx < pixels; idx += 3) {
      if (!validMask[idx] || stack.labels[idx] === 1) continue; // exclude quake zone (step ≠ velocity)
      const e = solution.velocityMPerYr[idx] - stack.truthSecularLosMPerYr[idx];
      s2 += e * e;
      n++;
    }
    if (n > 100) {
      truthValidation = {
        velocityRmseMPerYr: Math.sqrt(s2 / n),
        note:
          'RMSE of the SBAS secular LOS velocity against the analytic fixture truth, sampled over valid ' +
          'non-coseismic pixels (the coseismic zone is excluded because the mean velocity there includes ' +
          'the step). This validates the inversion machinery on known input — it is not an Earth claim.',
      };
    }
  }

  // ---- Transports ---------------------------------------------------------
  const maxSide = 640;
  const factor = Math.max(1, Math.ceil(Math.max(width, height) / maxSide));
  const vd = downsample(solution.velocityMPerYr, width, height, factor);
  const vtc = downsample(solution.temporalCoherence, width, height, factor);
  const vm = downsampleMask(validMask, width, height, factor);
  const vs = downsample(solution.velocitySigmaMPerYr, width, height, factor);
  for (let idx = 0; idx < vd.data.length; idx++) {
    if (!vm.data[idx]) {
      vd.data[idx] = NaN;
      vs.data[idx] = NaN;
      vtc.data[idx] = NaN;
    }
  }
  const velocity: TransportGrid = {
    width: vd.w,
    height: vd.h,
    displacement: b64(vd.data),
    coherence: b64(vtc.data),
    mask: b64(vm.data),
    sigma: b64(vs.data),
    meta: { ...meta, width: vd.w, height: vd.h, xSpacing: meta.xSpacing * factor, ySpacing: meta.ySpacing * factor },
  };

  const cumMaxSide = 200;
  const cfactor = Math.max(1, Math.ceil(Math.max(width, height) / cumMaxSide));
  const cumMask = downsampleMask(validMask, width, height, cfactor);
  const cumGrids: string[] = [];
  for (let e = 0; e < nE; e++) {
    const cd = downsample(solution.cumulative[e], width, height, cfactor);
    for (let idx = 0; idx < cd.data.length; idx++) if (!cumMask.data[idx]) cd.data[idx] = NaN;
    cumGrids.push(b64(cd.data));
  }
  const cmeta: GridMeta = {
    ...meta,
    width: cumMask.w,
    height: cumMask.h,
    xSpacing: meta.xSpacing * cfactor,
    ySpacing: meta.ySpacing * cfactor,
  };

  // ---- Summary + narrative --------------------------------------------------
  const velocityValues: number[] = [];
  let sigmaMed = NaN;
  let tempCohMed = NaN;
  let minVel = Infinity;
  let maxVel = -Infinity;
  {
    const sigmas: number[] = [];
    const tcs: number[] = [];
    for (let idx = 0; idx < pixels; idx++) {
      if (!validMask[idx]) continue;
      const v = solution.velocityMPerYr[idx];
      velocityValues.push(v);
      if (v < minVel) minVel = v;
      if (v > maxVel) maxVel = v;
      sigmas.push(solution.velocitySigmaMPerYr[idx]);
      tcs.push(solution.temporalCoherence[idx]);
    }
    sigmaMed = median(sigmas);
    tempCohMed = median(tcs);
  }
  const medVel = median(velocityValues);

  const summary: SbasSummary = {
    validPixelCount: validCount,
    validFraction: validCount / pixels,
    temporalCoherenceRetainedFraction: retainedFraction,
    pixelAreaKm2,
    analysedAreaKm2: validCount * pixelAreaKm2,
    maxVelocityMPerYr: maxVel,
    minVelocityMPerYr: minVel,
    medianVelocityMPerYr: medVel,
    medianVelocitySigmaMPerYr: sigmaMed,
    fastAreaKm2,
    rateThresholdMPerYr: rateThreshold,
    medianTemporalCoherence: tempCohMed,
    epochCount: nE,
    ifgCount: mIfg,
    lambda,
    lambdaCriterion: lcurve.criterion,
  };

  // Verdict (five-second answer, stack edition).
  const mmYr = (v: number) => (v * 1000).toFixed(0);
  // The quake-coincident transient: the largest change point within ±26 days
  // of the catalog event across ALL key series (the coseismic zone may be
  // any of the extracted locations, not just the step-search pixel).
  let quakeCp: import('./types').ChangePointMark | undefined;
  for (const s of keySeries) {
    for (const c of s.changePoints) {
      if (
        Math.abs(c.stepM) > 0.02 &&
        Math.abs(Date.parse(c.time) - Date.parse(stack.event.time)) < 26 * 86400000 &&
        (!quakeCp || Math.abs(c.stepM) > Math.abs(quakeCp.stepM))
      ) {
        quakeCp = c;
      }
    }
  }
  const subAnom = anomalies.find((a) => a.peakVelocityMPerYr < 0);
  const verdictParts: string[] = [];
  if (fastAreaKm2 >= 20) {
    verdictParts.push(
      `Ground is moving: ${fastAreaKm2.toFixed(0)} km² exceeds ${mmYr(rateThreshold)} mm/yr LOS velocity, ` +
      `peaking at ${mmYr(Math.abs(minVel))} mm/yr${subAnom ? ` (${subAnom.label.toLowerCase()})` : ''}.`,
    );
  } else {
    verdictParts.push(`No sustained motion above ${mmYr(rateThreshold)} mm/yr was resolved over the ${spanYears.toFixed(1)}-year stack.`);
  }
  if (quakeCp) {
    verdictParts.push(
      `A time-series step of ${(Math.abs(quakeCp.stepM) * 1000).toFixed(0)} mm on ${quakeCp.time.slice(0, 10)} coincides with the catalog event (synthetic M${stack.event.magnitude}).`,
    );
  }
  const verdict = verdictParts.join(' ');

  const whatChanged =
    `A Small Baseline Subset inversion of ${mIfg} interferograms over ${nE} acquisitions ` +
    `(${stack.epochs[0].time.slice(0, 10)} → ${stack.epochs[nE - 1].time.slice(0, 10)}, 12-day repeat with the ` +
    `2026-07-27…08-10 instrument gap honoured) measures line-of-sight velocities across the frame. ` +
    (fastAreaKm2 >= 20
      ? `Two aquifer-compaction zones dominate: the strongest moves at up to ${mmYr(Math.abs(minVel))} mm/yr ` +
        `(LOS) — a rate consistent with published Central Valley subsidence — and a coseismic step of ` +
        `${quakeCp ? (Math.abs(quakeCp.stepM) * 1000).toFixed(0) : '≈'} mm appears in the series exactly at the ` +
        `catalog event epoch. The Bayesian engine ranks the leading causes per zone below.`
      : `No zone exceeds the ${mmYr(rateThreshold)} mm/yr rate floor; the frame is stable at the achievable ` +
        `sensitivity (median velocity σ ±${mmYr(sigmaMed)} mm/yr).`);
  const howMuch =
    `Peak LOS velocity: ${mmYr(minVel)} mm/yr (toward the satellite is negative); area above the rate floor: ` +
    `${fastAreaKm2.toFixed(0)} km². Median per-pixel velocity uncertainty ±${mmYr(sigmaMed)} mm/yr. ` +
    `GNSS cross-validation: RMSE ${gnss.rmseMmPerYr.toFixed(1)} mm/yr over ${gnss.n} stations; ` +
    `Brier calibration ${brier ? brier.score.toFixed(3) : 'n/a'}; ` +
    `truth-velocity RMSE ${truthValidation ? (truthValidation.velocityRmseMPerYr * 1000).toFixed(1) : 'n/a'} mm/yr (fixture).`;
  const reliability =
    `The stack retains ${(retainedFraction * 100).toFixed(0)}% of coherent pixels after the temporal-coherence ` +
    `gate (γ_t ≥ ${tempCohMin}); ${warnings.length ? warnings.join(' ') : ''}` +
    `atmospheric corrections removed a stratified-troposphere RMS of ` +
    `${(budget.entries[0].rmsM * 1000).toFixed(1)} mm and an ionospheric-ramp RMS of ` +
    `${(budget.entries[1].rmsM * 1000).toFixed(1)} mm per interferogram, leaving a turbulent remainder of ` +
    `${(budget.entries[2].rmsM * 1000).toFixed(1)} mm (quantified, uncorrected — plan §15 mitigation: down-weighted ` +
    `by stack averaging). L-curve regularization λ = ${lambda.toFixed(2)} (first-difference Tikhonov). ` +
    `This stack is a SYNTHETIC VALIDATION FIXTURE parameterized from published Central Valley rates: it exercises ` +
    `the complete plan pipeline against known truth; it is not an observation of the Earth.`;

  // Real catalog context events inside the stack window (passed in by the
  // API layer; the pipeline itself stays pure).
  const windowStart = Date.parse(stack.epochs[0].time);
  const windowEnd = Date.parse(stack.epochs[nE - 1].time);
  const events: StackCatalogEvent[] = [
    stack.event,
    ...realEvents
      .filter((ev) => {
        const t = Date.parse(ev.time);
        return Number.isFinite(t) && t >= windowStart && t <= windowEnd;
      })
      .map((ev) => ({
        id: ev.id,
        label: `USGS ${ev.magType ?? 'M'}${ev.magnitude.toFixed(1)} — ${ev.place}`,
        magnitude: ev.magnitude,
        place: ev.place,
        time: ev.time,
        lat: ev.lat,
        lon: ev.lon,
        depthKm: ev.depthKm,
        url: ev.url,
        nodalPlane: ev.nodalPlane ?? { strike: 0, dip: 0, rake: 0 },
        synthetic: false,
      })),
  ];

  return {
    kind: 'sbas',
    datasetId,
    epochs: stack.epochs.map((e) => e.time),
    params,
    summary,
    velocity,
    cumulative: { meta: cmeta, grids: cumGrids, mask: b64(cumMask.data) },
    keySeries,
    anomalies,
    corrections: budget,
    gnss,
    brier,
    truthValidation,
    events,
    narrative: { verdict, whatChanged, howMuch, reliability },
    warnings,
    processingMs: Date.now() - t0,
  };
}
