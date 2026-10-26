/**
 * Stage E — cause investigation (attribution layer).
 *
 * Attribution is built ON TOP of validated change measurements and
 * independent evidence. It is a transparent rule-based screen, not a trained
 * classifier: every status is traceable to an explicit evidence item, and
 * the layer is required to say "insufficient evidence" rather than guess.
 *
 * Independent evidence used:
 *   - USGS ComCat events (FDSN API) inside the acquisition window and
 *     within/near the frame footprint.
 *   - Okada (1985) forward modelling from the published USGS moment-tensor
 *     nodal plane (strike/dip/rake) with empirical magnitude-area scaling
 *     (Wells & Coppersmith 1994) and the Hanks & Kanamori (1979) moment.
 *
 * The forward model is a CONSISTENCY SCREEN, not an inversion: we compare
 * the predicted and measured fields' amplitudes and spatial correlation and
 * report agreement classes, never a probability of causation. Agreement is
 * NOT causal proof — the same pattern could in principle be produced by
 * other sources; the output language says "consistent with", never "caused
 * by".
 *
 * REMEDIATION NOTES (2026-10-21):
 *   - Event selection is now an explicit, documented ranking
 *     (rankEventsForPair); the first API result is never assumed to be the
 *     most relevant.
 *   - The forward model is anchored at the EVENT EPICENTRE projected into
 *     the raster's own coordinate reference system (was: raster centre —
 *     a misplacement whenever the epicentre is not at the grid centre).
 *   - All numeric screening thresholds are collected in
 *     ATTRIBUTION_HEURISTICS, labelled exploratory, and exposed in the
 *     output metadata. None of them is a calibrated statistic.
 *   - Hypothesis language distinguishes "no detection above threshold"
 *     from "no change", and never treats a single unwrapping component as
 *     proof of artefact absence.
 */

import type {
  AttributionOutput,
  ChangeSummary,
  Hypothesis,
  RadarGeometry,
  ScienceDataset,
  UsgsEventLite,
  Raster,
  GridMeta,
} from './types';
import { pearson } from './statistics';
import {
  geoFaultDisplacementENU,
  momentFromMw,
  ruptureAreaFromMw,
  slipFromMoment,
} from './okada';
import { projectDisplacementToLOS, losUnitVector } from './los-projection';
import { latLonToUtmInZone, zoneFromEpsg } from './utm';

// ---------------------------------------------------------------------------
// Exploratory screening heuristics — values and rationale.
//
// NONE of these numbers is a calibrated statistic: they are order-of-magnitude
// conventions chosen so the screen is neither trivially permissive nor
// trivially strict, and they are exposed in every result so a reader can
// judge (and reproduce) the classification. Sensitivity to each value is
// exercised in tests/science/attribution.test.ts.

export interface AttributionHeuristics {
  /** Peak-|LOS| below this (mm) keeps "atmospheric residual" plausible. */
  atmosphericPeakMm: number;
  /** Measured/predicted peak ratio within [1/ratioFactor, ratioFactor] passes the amplitude check. */
  amplitudeRatioFactor: number;
  /** Predicted-vs-measured spatial correlation above this passes the pattern check. */
  patternCorrelation: number;
}

export const ATTRIBUTION_HEURISTICS: AttributionHeuristics = {
  atmosphericPeakMm: 30,
  amplitudeRatioFactor: 3,
  patternCorrelation: 0.25,
};

export const HEURISTICS_NOTE =
  'Exploratory screening values, not calibrated statistics: peak < ' +
  `${ATTRIBUTION_HEURISTICS.atmosphericPeakMm} mm keeps an atmospheric residual plausible ` +
  '(order of uncorrected tropospheric residuals for L-band pairs; Hanssen 2001 ch. 6); ' +
  `amplitude ratio within a factor of ${ATTRIBUTION_HEURISTICS.amplitudeRatioFactor} reflects the ` +
  'scatter of the Wells & Coppersmith (1994) magnitude-area scaling (±0.25 decades ≈ factor 1.8, ' +
  'plus uniform-slip idealisation); spatial correlation > ' +
  `${ATTRIBUTION_HEURISTICS.patternCorrelation} is a weak pattern-agreement floor. ` +
  'Passing these checks means "consistent with the model", never "caused by".';

/** Great-circle distance in km (spherical Earth). */
export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371.0088;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

/** Distance from a point to the polygon centroid (km) — coarse containment proxy. */
export function distanceToFootprintKm(ds: ScienceDataset, lat: number, lon: number): number {
  const poly = ds.footprintPolygon;
  if (!poly || poly.length === 0) return NaN;
  let cx = 0;
  let cy = 0;
  for (const [x, y] of poly) {
    cx += x;
    cy += y;
  }
  cx /= poly.length;
  cy /= poly.length;
  return haversineKm(lat, lon, cy, cx);
}

// ---------------------------------------------------------------------------
// Event selection (§6 of the remediation): explicit ranking, alternatives,
// criteria metadata. Never "the first API result".

export interface EventSelection {
  /** Ranked best-first; only events strictly inside the acquisition window. */
  ranked: UsgsEventLite[];
  /** The highest-ranked candidate (undefined when none qualify). */
  primary?: UsgsEventLite;
  /** Lower-ranked in-window candidates, best-first. */
  alternatives: UsgsEventLite[];
  /** Machine-readable description of how the ranking was computed. */
  criteria: {
    window: { start: string; end: string };
    maxDistanceKm: number;
    distanceTo: 'footprint centroid';
    ranking: string;
    magnitudeHandling: string;
    mechanismHandling: string;
  };
  /** Events seen by the caller but excluded, with the reason. */
  excluded: Array<{ id: string; reason: string }>;
}

/**
 * Rank USGS events for one interferometric pair.
 *
 * Criteria (in priority order, no hidden composite score):
 *   1. The origin time must fall strictly inside (reference, secondary).
 *   2. The event must be within maxDistanceKm of the footprint centroid.
 *   3. Candidates are ordered by expected relevance to a measurable
 *      deformation signal: magnitude (larger first), then distance to the
 *      footprint (nearer first), then availability of a moment-tensor
 *      mechanism (available first).
 *
 * Events with a missing magnitude are kept but ranked last (they may still
 * be informative context); events outside the window/distance are excluded
 * with an explicit reason.
 */
export function rankEventsForPair(
  events: UsgsEventLite[],
  ds: ScienceDataset,
  opts: { maxDistanceKm?: number } = {},
): EventSelection {
  const maxDistanceKm = opts.maxDistanceKm ?? 300;
  const t0 = Date.parse(ds.provenance.referenceTime);
  const t1 = Date.parse(ds.provenance.secondaryTime);
  const excluded: EventSelection['excluded'] = [];
  const inWindow: UsgsEventLite[] = [];

  for (const ev of events) {
    const t = Date.parse(ev.time);
    if (!Number.isFinite(t)) {
      excluded.push({ id: ev.id, reason: 'event time is not parseable' });
      continue;
    }
    if (!(t > t0 && t < t1)) {
      excluded.push({
        id: ev.id,
        reason: `origin time outside the acquisition window (${ds.provenance.referenceTime.slice(0, 10)} … ${ds.provenance.secondaryTime.slice(0, 10)})`,
      });
      continue;
    }
    const dist = distanceToFootprintKm(ds, ev.lat, ev.lon);
    if (Number.isNaN(dist)) {
      excluded.push({ id: ev.id, reason: 'footprint unavailable; cannot check distance' });
      continue;
    }
    if (dist > maxDistanceKm) {
      excluded.push({
        id: ev.id,
        reason: `${dist.toFixed(0)} km from the footprint centroid (> ${maxDistanceKm} km)`,
      });
      continue;
    }
    inWindow.push(ev);
  }

  const distanceOf = (ev: UsgsEventLite) => distanceToFootprintKm(ds, ev.lat, ev.lon);
  const ranked = [...inWindow].sort((a, b) => {
    // Missing magnitudes rank last within the same distance class.
    const magA = Number.isFinite(a.magnitude) ? a.magnitude : -Infinity;
    const magB = Number.isFinite(b.magnitude) ? b.magnitude : -Infinity;
    if (magB !== magA) return magB - magA;
    const dA = distanceOf(a);
    const dB = distanceOf(b);
    if (dA !== dB) return dA - dB;
    const mA = a.nodalPlane ? 1 : 0;
    const mB = b.nodalPlane ? 1 : 0;
    if (mB !== mA) return mB - mA;
    return a.id.localeCompare(b.id); // deterministic tie-break
  });

  return {
    ranked,
    primary: ranked[0],
    alternatives: ranked.slice(1),
    criteria: {
      window: { start: ds.provenance.referenceTime, end: ds.provenance.secondaryTime },
      maxDistanceKm,
      distanceTo: 'footprint centroid',
      ranking:
        'in-window candidates ordered by magnitude (descending), then distance to the footprint ' +
        'centroid (ascending), then moment-tensor availability; ties broken by event id. This ' +
        'favours the event most likely to produce a measurable signal — it is a relevance order, ' +
        'not a statement that the selected event caused anything.',
      magnitudeHandling:
        'events with a missing magnitude are kept but ranked last (context only); magnitude type is recorded on each event',
      mechanismHandling:
        'a published moment-tensor nodal plane is required for any quantitative forward-model check; without one the check is skipped, never invented',
    },
    excluded,
  };
}

// ---------------------------------------------------------------------------
// Forward model — epicentre-anchored, projection-aware.

/**
 * Project a geographic point (lat/lon) into the coordinate reference system
 * of a raster grid.
 *
 * Supported CRS: WGS84 UTM (EPSG 326xx / 327xx) — the CRS of the NISAR GUNW
 * products we consume. Polar stereographic granules (EPSG 3413/3031) would
 * need a different inverse; for those the forward model is disabled with an
 * explanation instead of silently misplacing it.
 */
export function projectToGrid(
  lat: number,
  lon: number,
  meta: GridMeta,
): { x: number; y: number } | { error: string } {
  const zone = zoneFromEpsg(meta.epsg);
  if (!zone) {
    return {
      error:
        `grid EPSG ${meta.epsg} is not WGS84 UTM; the forward model only supports UTM grids ` +
        '(polar-stereographic granules would need a different projection — refusing rather than misplacing the model)',
    };
  }
  try {
    const u = latLonToUtmInZone(lat, lon, zone.zone);
    return { x: u.easting, y: u.northing };
  } catch (err) {
    return { error: `projection failed: ${(err as Error).message}` };
  }
}

/** Build a fault model from a USGS event (documented assumptions). */
export function faultFromEvent(event: UsgsEventLite): {
  fault: import('./okada').GeoFault;
  centroidDepthM: number;
} | { error: string } {
  const np = event.nodalPlane;
  if (!np) {
    return { error: 'no moment-tensor nodal plane published for this event' };
  }
  if (!Number.isFinite(event.magnitude)) {
    return { error: 'event magnitude is missing; scaling relations need it' };
  }
  if (!Number.isFinite(event.depthKm) || event.depthKm < 0) {
    return { error: 'hypocentral depth is missing or negative' };
  }
  const area = ruptureAreaFromMw(event.magnitude);
  const moment = momentFromMw(event.magnitude);
  // Aspect ratio 2:1 (documented assumption).
  const width = Math.sqrt(area / 2);
  const length = 2 * width;
  const slip = slipFromMoment(moment, area);
  const centroidDepth = Math.max(4000, event.depthKm * 1000);
  return {
    fault: {
      strikeDeg: np.strike,
      dipDeg: np.dip,
      rakeDeg: np.rake,
      lengthM: length,
      widthM: width,
      topDepthM: 0, // computed inside geoFaultDisplacementENU from centroid depth
      slipM: slip,
      centroidLat: event.lat,
      centroidLon: event.lon,
    },
    centroidDepthM: centroidDepth,
  };
}

/**
 * Forward-model consistency check: predict LOS displacement from a USGS
 * event's nodal plane over the dataset grid, compare with measurement.
 *
 * COORDINATE FRAME (remediated 2026-10-21): the epicentre is projected into
 * the raster's own CRS (UTM zone from the grid EPSG) and per-pixel offsets
 * are taken RELATIVE TO THE EPICENTRE — not relative to the raster centre.
 * Moving the earthquake therefore moves the predicted field; moving the grid
 * origin does not (asserted in tests/science/attribution.test.ts).
 */
export function forwardModelCheck(
  ds: ScienceDataset,
  event: UsgsEventLite | undefined,
  displacement: Float32Array | undefined,
  mask: Uint8Array | undefined,
): AttributionOutput['forwardModelCheck'] | undefined {
  if (!event || !ds.phase || !displacement) return undefined;

  const faultRes = faultFromEvent(event);
  if ('error' in faultRes) {
    return {
      eventId: event.id,
      skipped: true,
      skipReason: faultRes.error,
      amplitudeRatio: NaN,
      patternCorrelation: NaN,
      predictedPeakM: NaN,
      measuredPeakM: NaN,
      note:
        'Forward-model check skipped: ' +
        faultRes.error +
        '. No fault geometry was invented; the coseismic hypothesis remains unevaluated quantitatively.',
    };
  }

  const meta = ds.phase.meta;
  const epi = projectToGrid(event.lat, event.lon, meta);
  if ('error' in epi) {
    return {
      eventId: event.id,
      skipped: true,
      skipReason: epi.error,
      amplitudeRatio: NaN,
      patternCorrelation: NaN,
      predictedPeakM: NaN,
      measuredPeakM: NaN,
      note: 'Forward-model check skipped: ' + epi.error,
    };
  }

  const { fault, centroidDepthM } = faultRes;
  const los = losUnitVector(
    ds.geometry.incidenceAngleDeg,
    ds.geometry.headingDeg,
    ds.geometry.lookDirection,
  );

  // Evaluate on a deterministic strided subgrid: the Okada field is smooth at
  // grid scale, so a subgrid carries the same amplitude/pattern information
  // while keeping the consistency screen cheap on large NISAR frames
  // (full frames reach ~2e7 pixels). Peaks are computed with explicit loops
  // (never argument spreads, which overflow on large arrays).
  const n = meta.width * meta.height;
  const stride = Math.max(1, Math.floor(n / 400000));
  const meas: number[] = [];
  const prd: number[] = [];
  let predPeak = 0;
  let measuredPeak = 0;

  for (let idx = 0; idx < n; idx += stride) {
    const i = Math.floor(idx / meta.width);
    const j = idx % meta.width;
    // Offsets RELATIVE TO THE EPICENTRE in the raster's own projection.
    const x = meta.xStart + j * meta.xSpacing - epi.x;
    const y = meta.yStart + i * meta.ySpacing - epi.y;
    const d = geoFaultDisplacementENU(fault, centroidDepthM, x, y);
    if (d.singular) continue;
    const losM = projectDisplacementToLOS(d, los);
    if (Math.abs(losM) > Math.abs(predPeak)) predPeak = losM;
    if (mask && !mask[idx]) continue;
    const dv = displacement[idx];
    if (!Number.isNaN(dv)) {
      meas.push(dv);
      prd.push(losM);
      if (Math.abs(dv) > measuredPeak) measuredPeak = Math.abs(dv);
    }
  }

  const amplitudeRatio =
    Math.abs(predPeak) > 1e-9 ? measuredPeak / Math.abs(predPeak) : NaN;
  const corr = pearson(prd, meas);

  return {
    eventId: event.id,
    skipped: false,
    amplitudeRatio: Number.isNaN(amplitudeRatio) ? 0 : amplitudeRatio,
    patternCorrelation: Number.isNaN(corr) ? 0 : corr,
    predictedPeakM: predPeak,
    measuredPeakM: measuredPeak,
    epicentre: { lat: event.lat, lon: event.lon, projectedX: epi.x, projectedY: epi.y, epsg: meta.epsg },
    note:
      'Predicted field uses a uniform-slip rectangular dislocation with the published nodal plane, ' +
      'anchored at the epicentre projected into the raster CRS (EPSG ' + meta.epsg + '); ' +
      'fault dimensions follow empirical magnitude-area scaling with substantial scatter, so only ' +
      'order-of-magnitude amplitude agreement is meaningful. Evaluated on a deterministic subgrid ' +
      `of about ${Math.floor(n / stride)} points (the model field is smooth at grid scale). ` +
      'Agreement classes are exploratory screening, not causation.',
  };
}

/** Build the Okada forward-model LOS prediction on an arbitrary grid (epicentre-anchored). */
export function buildPredictionOnGrid(
  meta: GridMeta,
  event: UsgsEventLite,
  geometry: RadarGeometry,
): Raster | { error: string } {
  const faultRes = faultFromEvent(event);
  if ('error' in faultRes) return { error: faultRes.error };
  const epi = projectToGrid(event.lat, event.lon, meta);
  if ('error' in epi) return { error: epi.error };

  const { fault, centroidDepthM } = faultRes;
  const los = losUnitVector(
    geometry.incidenceAngleDeg,
    geometry.headingDeg,
    geometry.lookDirection,
  );
  const n = meta.width * meta.height;
  const pred = new Float32Array(n);
  for (let idx = 0; idx < n; idx++) {
    const i = Math.floor(idx / meta.width);
    const j = idx % meta.width;
    const d = geoFaultDisplacementENU(
      fault,
      centroidDepthM,
      meta.xStart + j * meta.xSpacing - epi.x,
      meta.yStart + i * meta.ySpacing - epi.y,
    );
    pred[idx] = d.singular ? NaN : projectDisplacementToLOS(d, los);
  }
  return { meta, data: pred };
}

/** Helper to attach prediction raster (for the map layer) on a dataset grid. */
export function buildPredictionRaster(
  ds: ScienceDataset,
  event: UsgsEventLite,
): Raster | undefined {
  if (!ds.phase) return undefined;
  const r = buildPredictionOnGrid(ds.phase.meta, event, ds.geometry);
  return 'error' in r ? undefined : r;
}

// ---------------------------------------------------------------------------
// Hypothesis evaluation.

/**
 * Evaluate hypotheses given the measured summary, dataset geometry and the
 * (already ranked) USGS event selection.
 */
export function evaluateAttribution(
  ds: ScienceDataset,
  summary: ChangeSummary,
  selection: EventSelection,
  forwardModel?: AttributionOutput['forwardModelCheck'],
): AttributionOutput {
  const hyp: Hypothesis[] = [];
  const events = selection.ranked;
  const minCredibleAreaKm2 = Math.max(1, summary.pixelAreaKm2 * 4);
  const detected =
    summary.affectedAreaKm2 > 0 && summary.affectedAreaKm2 >= minCredibleAreaKm2;

  // ---- H1: coseismic deformation -------------------------------------
  const ev = selection.primary;
  const coseismicEvidence: string[] = [];
  let coseismicStatus: Hypothesis['status'] = 'insufficient-evidence';
  if (ev) {
    coseismicEvidence.push(
      `${events.length} in-window USGS catalog event(s) within ${selection.criteria.maxDistanceKm} km of the footprint ` +
        `(${ds.provenance.referenceTime.slice(0, 10)} to ${ds.provenance.secondaryTime.slice(0, 10)}); ` +
        `largest/most relevant: M${Number.isFinite(ev.magnitude) ? ev.magnitude.toFixed(1) : '?'} ${ev.magType ?? ''} ${ev.place} ` +
        `at ${ev.time.slice(0, 16).replace('T', ' ')} UTC, ${ev.depthKm.toFixed(0)} km deep ` +
        `(${selection.criteria.ranking.split('.')[0].toLowerCase()}).`,
    );
    if (selection.alternatives.length > 0) {
      coseismicEvidence.push(
        'Alternative in-window candidates (not selected as primary): ' +
          selection.alternatives
            .map((a) => `M${Number.isFinite(a.magnitude) ? a.magnitude.toFixed(1) : '?'} ${a.place} (${a.time.slice(0, 10)})`)
            .join('; ') +
          '.',
      );
    }
    if (detected && forwardModel && !forwardModel.skipped) {
      coseismicEvidence.push(
        `Okada (1985) forward model from the USGS nodal plane (epicentre-anchored) predicts a peak ` +
          `LOS displacement of ${(forwardModel.predictedPeakM * 1000).toFixed(0)} mm; the measurement peaks at ` +
          `${(forwardModel.measuredPeakM * 1000).toFixed(0)} mm (amplitude ratio ` +
          `${forwardModel.amplitudeRatio.toFixed(2)}).`,
      );
      coseismicEvidence.push(
        `Spatial correlation between the predicted and measured displacement fields: r = ` +
          `${Number.isNaN(forwardModel.patternCorrelation) ? 'n/a' : forwardModel.patternCorrelation.toFixed(2)}.`,
      );
      const ampOk =
        forwardModel.amplitudeRatio > 1 / ATTRIBUTION_HEURISTICS.amplitudeRatioFactor &&
        forwardModel.amplitudeRatio < ATTRIBUTION_HEURISTICS.amplitudeRatioFactor;
      const corrOk = forwardModel.patternCorrelation > ATTRIBUTION_HEURISTICS.patternCorrelation;
      if (ampOk && corrOk) {
        coseismicStatus = 'consistent';
        coseismicEvidence.push(
          'Amplitude within the exploratory factor-of-3 band and positive spatial correlation: the ' +
            'measured field is CONSISTENT with elastic dislocation from the catalogued event. ' +
            'Consistency is not causation — other sources producing a similar pattern cannot be ' +
            'excluded from a single interferogram.',
        );
      } else if (ampOk || corrOk) {
        coseismicStatus = 'plausible';
        coseismicEvidence.push(
          'Only part of the exploratory consistency checks pass; other contributions (afterslip, ' +
            'atmospheric residuals, unwrapping errors) may also be present.',
        );
      } else {
        coseismicStatus = 'not-supported';
        coseismicEvidence.push(
          'Amplitude and pattern do not match the forward model under the exploratory thresholds; ' +
            'a coseismic origin of the measured signal is not supported (the event may be outside ' +
            'the coherent footprint, or its true dimensions far from the empirical scaling).',
        );
      }
    } else if (forwardModel && forwardModel.skipped) {
      coseismicEvidence.push(
        'Forward-model check skipped: ' + forwardModel.skipReason,
      );
    } else if (!detected) {
      coseismicStatus = 'insufficient-evidence';
      coseismicEvidence.push(
        'No change above the detection threshold was measured, so coseismic deformation cannot be ' +
          'confirmed from this pair. This is NOT evidence that no deformation occurred: the event ' +
          'may be outside the coherent coverage, its signal may sit below the noise/systematic ' +
          'floor, or the epicentral area may have decorrelated.',
      );
    } else {
      coseismicStatus = 'insufficient-evidence';
      coseismicEvidence.push(
        'Measured change exists but no usable focal mechanism / geometry was available for a ' +
          'forward-model check; no fault geometry was invented.',
      );
    }
  } else {
    if (ds.isSynthetic && ds.truth) {
      // SYNTHETIC FIXTURE with analytic ground truth and no catalog events.
      // The cause of the signal is fixed by construction, so the catalog-based
      // screen ("no USGS catalog earthquake near this footprint") would be
      // actively misleading for an earthquake fixture — it would read as
      // "this is not an earthquake". State the construction fact instead.
      // (Fix verified 2026-10-21: final-pass audit finding.)
      let truthPeakM = 0;
      const stride = Math.max(1, Math.floor(ds.truth.data.length / 20000));
      for (let i = 0; i < ds.truth.data.length; i += stride) {
        const v = ds.truth.data[i];
        if (Number.isFinite(v) && Math.abs(v) > truthPeakM) truthPeakM = Math.abs(v);
      }
      if (truthPeakM > 1e-4) {
        coseismicStatus = 'supported';
        coseismicEvidence.push(
          'Demonstration dataset: the displacement field was GENERATED from a known earthquake ' +
            `dislocation model (analytic ground truth, peak ${(truthPeakM * 1000).toFixed(0)} mm LOS). ` +
            'The cause is known by construction — a validation statement, not an attribution inference.',
          'Catalog-based attribution (USGS event cross-match, forward-model consistency) applies to ' +
            'real measurements; it is intentionally not exercised on synthetic data.',
        );
      } else {
        coseismicStatus = 'not-supported';
        coseismicEvidence.push(
          'Demonstration dataset (null fixture): the ground truth contains zero displacement by ' +
            'construction, so no coseismic signal exists to attribute. This is a false-alarm control, ' +
            'not an inference about the Earth.',
        );
      }
    } else if (events.length === 0 && selection.excluded.length > 0) {
      coseismicEvidence.push(
        'No USGS catalog earthquake qualified for this pair (excluded candidates and reasons: ' +
          selection.excluded.map((e) => `${e.id}: ${e.reason}`).join('; ') + ').',
      );
    } else {
      coseismicEvidence.push(
        'No USGS catalog earthquake of the queried magnitude occurred inside the acquisition window ' +
          'near this footprint.',
      );
    }
    if (!(ds.isSynthetic && ds.truth)) coseismicStatus = 'not-supported';
  }
  hyp.push({
    id: 'coseismic',
    label: 'Earthquake (coseismic deformation)',
    description:
      'Sudden slip on a fault moves the surface elastically during the time between the two radar acquisitions.',
    status: coseismicStatus,
    evidence: coseismicEvidence,
    discriminatingObservation: ds.isSynthetic
      ? 'Not applicable — the ground truth is known exactly for this demonstration dataset (compare it ' +
        'against the ground-truth check in the advanced results).'
      : 'An independent geodetic or seismic source model of the event, or an interferogram from a ' +
        'different viewing geometry, would confirm or reject this explanation.',
  });

  // ---- H2: anthropogenic / slow surface motion ------------------------
  const anthroEvidence: string[] = [];
  let anthroStatus: Hypothesis['status'] = 'insufficient-evidence';
  if (detected) {
    anthroEvidence.push(
      `Detected anomaly covers ${summary.affectedAreaKm2.toFixed(0)} km² with peak apparent LOS motion ` +
        `of ${(summary.maxAwayM * 1000).toFixed(1)} mm away / ${(summary.maxTowardM * 1000).toFixed(1)} mm toward the radar.`,
    );
    if (ds.isSynthetic && ds.truth) {
      anthroEvidence.push(
        'Demonstration dataset: alternative-cause screening is not meaningful when the cause is ' +
          'fixed by construction (see the earthquake hypothesis).',
      );
    } else if (events.length === 0 && coseismicStatus === 'not-supported') {
      anthroStatus = 'plausible';
      anthroEvidence.push(
        'No catalogued earthquake coincides with the signal, so non-seismic processes ' +
          '(groundwater withdrawal, compaction, landslides, volcanic or geothermal activity) remain plausible.',
      );
    } else {
      anthroEvidence.push(
        'A seismic candidate exists; slow anthropogenic processes cannot be excluded from a single pair alone.',
      );
    }
    anthroEvidence.push(
      'This system does not hold land-use or groundwater data, so it cannot decide between these ' +
        'sub-mechanisms; it reports them as unresolved.',
    );
  } else {
    anthroEvidence.push(
      'No change detected above the threshold — slow processes below the detection sensitivity ' +
        'remain possible and cannot be excluded.',
    );
  }
  hyp.push({
    id: 'anthropogenic-slow',
    label: 'Slow surface motion (subsidence / uplift / landslide)',
    description:
      'Groundwater extraction, soil compaction, mining, landsliding or volcanic sources move the surface gradually.',
    status: anthroStatus,
    evidence: anthroEvidence,
    discriminatingObservation:
      'A displacement time series over many acquisitions (trend persistence) plus land-use context ' +
      'would discriminate subsidence-style processes from episodic events.',
  });

  // ---- H3: atmospheric / ionospheric residual --------------------------
  const atmEvidence: string[] = [];
  let atmStatus: Hypothesis['status'] = 'insufficient-evidence';
  const peakMm = Math.max(Math.abs(summary.maxAwayM), Math.abs(summary.maxTowardM)) * 1000;
  if (ds.isSynthetic) {
    // Synthetic fixtures simulate their own noise + atmospheric ramp; the
    // real-product correction-status language does not apply to them.
    atmEvidence.push(
      'Demonstration dataset: the simulator adds a small smooth atmospheric ramp and ' +
        'coherence-consistent measurement noise by construction (src/lib/science/fixtures.ts). ' +
        'No real atmospheric state is involved, so real-world atmospheric screening is not meaningful here.',
    );
    if (detected) {
      atmEvidence.push(
        'The detected pattern is the known earthquake signal of the fixture; the simulated ramp is a minor add-on.',
      );
    } else {
      atmStatus = 'plausible';
      atmEvidence.push(
        'What remains in this null fixture is exactly the simulated noise + ramp — by construction.',
      );
    }
  } else {
    atmEvidence.push(
      'The product supplies ionospheric and tropospheric correction layers but does NOT apply them ' +
        'to the stored phase (ASF NISAR GUNW guide), so atmospheric/ionospheric residuals are ' +
        'structurally present in every GUNW measurement (Hanssen 2001, chapters 6 and 9).',
    );
  }
  if (!ds.isSynthetic && summary.systematicIonoFloorM > 0) {
    atmEvidence.push(
      `The ionospheric systematic floor for this pair is ±${(summary.systematicIonoFloorM * 1000).toFixed(0)} mm ` +
        `(from the ${summary.ionoFloorBasis === 'variation' ? "supplied screen's own spatial variation" : "screen estimate's uncertainty"}).`,
    );
  }
  if (!ds.isSynthetic) {
    if (detected) {
      if (peakMm < ATTRIBUTION_HEURISTICS.atmosphericPeakMm) {
        atmStatus = 'plausible';
        atmEvidence.push(
          `Peak apparent displacement (${peakMm.toFixed(0)} mm) is below the exploratory ` +
            `${ATTRIBUTION_HEURISTICS.atmosphericPeakMm} mm atmospheric-plausibility value, the order of typical ` +
            'uncorrected tropospheric residuals; an atmospheric contribution to the whole pattern cannot be excluded.',
        );
      } else {
        atmEvidence.push(
          `Peak apparent displacement (${peakMm.toFixed(0)} mm) exceeds what residual atmosphere typically ` +
            'produces; a purely atmospheric origin is unlikely to explain the full amplitude (exploratory judgement).',
        );
      }
    } else {
      atmStatus = 'plausible';
      atmEvidence.push(
        'Sub-threshold spatially smooth variation is exactly what uncorrected atmospheric residuals ' +
          'look like at this sensitivity.',
      );
    }
  }
  hyp.push({
    id: 'atmospheric',
    label: 'Atmospheric / ionospheric residual',
    description:
      'Water-vapour and ionosphere changes between acquisitions imprint apparent phase; the GUNW product does not remove them from the stored data.',
    status: atmStatus,
    evidence: atmEvidence,
    discriminatingObservation:
      'Comparison with an independent weather model (e.g. ERA5 delays), application of the supplied ' +
      'ionospheric screen, or a pair from a different time spanning the same area would separate ' +
      'atmospheric artefacts from real motion.',
  });

  // ---- H4: unwrapping error --------------------------------------------
  const unwrapEvidence: string[] = [];
  let unwrapStatus: Hypothesis['status'] = 'insufficient-evidence';
  if (summary.connectedComponentCount > 1) {
    unwrapStatus = 'plausible';
    unwrapEvidence.push(
      `${summary.connectedComponentCount} unwrapping connected components hold valid pixels; ` +
        'phase offsets between components can masquerade as step-like displacement.',
    );
  } else {
    unwrapEvidence.push(
      'A single unwrapping connected component covers the valid pixels, which lowers the likelihood ' +
        'of gross inter-region 2π-cycle offsets. This is NOT proof of their absence: cycle errors ' +
        'can occur within one component, and the unwrapper itself can be misled by low coherence.',
    );
  }
  hyp.push({
    id: 'unwrapping',
    label: 'Phase-unwrapping artefact',
    description:
      'If the phase unwrapper jumps by whole cycles between regions, apparent displacement steps appear that are not physical.',
    status: unwrapStatus,
    evidence: unwrapEvidence,
    discriminatingObservation:
      'Re-unwrapping with a different algorithm or inspecting the wrapped phase along the component ' +
      'boundary would reveal cycle jumps.',
  });

  // ---- H5: no change above threshold ------------------------------------
  hyp.push({
    id: 'null',
    label: 'No change above the detection threshold',
    description:
      'No apparent LOS range change above the configured detection threshold and minimum credible area was found.',
    status: detected ? 'not-supported' : 'supported',
    evidence: [
      detected
        ? `A credible anomaly (${summary.affectedAreaKm2.toFixed(0)} km² above threshold) was detected.`
        : 'No pixel exceeded the combined statistical and physical detection threshold (or only ' +
          'isolated stray pixels below the minimum credible area did).',
      `Median per-pixel one-sigma uncertainty: ${(summary.medianSigmaM * 1000).toFixed(1)} mm; ` +
        `detection floor: ${Math.max(summary.systematicIonoFloorM, 0.01) >= 0.01 ? '' : ''}` +
        `${((summary.systematicIonoFloorM || 0.01) * 1000).toFixed(0)} mm.`,
      detected
        ? undefined
        : 'Supported here means exactly "no detection above threshold at the available sensitivity" — ' +
          'NOT that the ground did not move.',
    ].filter((x): x is string => x !== undefined),
    discriminatingObservation: undefined,
  });

  // Order: supported/consistent first, then plausible, then insufficient, then not-supported.
  const rank: Record<Hypothesis['status'], number> = {
    supported: 0,
    consistent: 1,
    plausible: 2,
    'insufficient-evidence': 3,
    'not-supported': 4,
  };
  hyp.sort((a, b) => rank[a.status] - rank[b.status]);

  return {
    hypotheses: hyp,
    events,
    eventSelection: {
      criteria: selection.criteria,
      excluded: selection.excluded,
      alternatives: selection.alternatives.map((a) => ({ id: a.id, magnitude: a.magnitude, place: a.place, time: a.time })),
    },
    forwardModelCheck: forwardModel,
    heuristics: ATTRIBUTION_HEURISTICS,
    methodNote:
      (ds.isSynthetic
        ? 'Demonstration dataset: hypotheses state construction facts from the known ground truth ' +
          'rather than screening outcomes; catalog-based attribution is not exercised on synthetic data. '
        : '') +
      'Rule-based, evidence-linked screening. Forward-model checks use the Okada (1985) elastic ' +
      'half-space with the USGS moment-tensor nodal plane anchored at the projected epicentre, ' +
      'Wells & Coppersmith (1994) area scaling and Hanks & Kanamori (1979) moment-magnitude ' +
      'relation. ' + HEURISTICS_NOTE,
  };
}
