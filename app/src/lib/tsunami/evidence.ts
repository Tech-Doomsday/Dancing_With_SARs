/**
 * Evidence classification for "did this earthquake generate a tsunami?"
 *
 * Five transparent states (task spec §4):
 *   insufficient       — no usable ocean-sensor record in range
 *   none-detected      — sensors with good coverage show no anomalous wave
 *   possible           — seismic screening met; ocean signal unconfirmed
 *   consistent         — sensor trigger(s) AND simulation agree within
 *                        documented tolerances
 *   confirmed-official — cited to an authoritative external source (link);
 *                        the software NEVER asserts this on its own
 *
 * Separation of concerns (enforced structurally):
 *   - detection (detection.ts) reports what the sensor saw;
 *   - simulation (fv-solver) reports what the model computes;
 *   - this module combines them into a classification with reasons.
 *
 * Correlation honesty: multiple DART buoys sample the SAME wave field —
 * agreement between them is not independent confirmation, and every
 * aggregated level carries that caveat. Missing evidence is never treated
 * as evidence of absence.
 *
 * Screening heuristics (configurable, labelled as heuristics in the UI):
 *   Mw ≥ 6.5 and depth ≤ 50 km and dip-slip component |sin(rake)| ≥ 0.3.
 *   These are order-of-magnitude screening values from the research plan
 *   (§14, Level 2), NOT physical laws: deep M7.5 events can be tsunamigenic
 *   and shallow M6.3 events have generated local tsunamis.
 */

import type { DetectionResult, EvidenceAssessment, EvidenceLevel, StationComparison, TsunamiEvent } from './types';

export interface ScreeningThresholds {
  minMagnitude: number;
  maxDepthKm: number;
  /** minimum |sin(rake)| for a dip-slip component */
  minDipSlipFraction: number;
}

export const DEFAULT_SCREENING: ScreeningThresholds = {
  minMagnitude: 6.5,
  maxDepthKm: 50,
  minDipSlipFraction: 0.3,
};

export interface ScreeningResult {
  met: boolean;
  reasons: string[];
  notes: string[];
}

/** Seismic screening — a heuristic, never a verdict. */
export function screenSeismic(ev: TsunamiEvent, th: ScreeningThresholds = DEFAULT_SCREENING): ScreeningResult {
  const reasons: string[] = [];
  const notes: string[] = [];
  const magOk = ev.magnitude >= th.minMagnitude;
  const depthOk = ev.depthKm <= th.maxDepthKm;
  const np = ev.nodalPlane;
  const dipSlip = np ? Math.abs(Math.sin((np.rake * Math.PI) / 180)) : null;
  const mechOk = dipSlip === null ? false : dipSlip >= th.minDipSlipFraction;

  if (magOk) reasons.push(`magnitude M ${ev.magnitude.toFixed(1)} ≥ ${th.minMagnitude}`);
  else notes.push(`magnitude M ${ev.magnitude.toFixed(1)} below the ${th.minMagnitude} screening value`);
  if (depthOk) reasons.push(`hypocentre depth ${ev.depthKm.toFixed(0)} km ≤ ${th.maxDepthKm} km`);
  else notes.push(`hypocentre depth ${ev.depthKm.toFixed(0)} km deeper than the ${th.maxDepthKm} km screening value`);
  if (np) {
    const mech =
      dipSlip! >= 0.7 ? 'predominantly dip-slip' : dipSlip! >= 0.3 ? 'oblique (mixed strike- and dip-slip)' : 'predominantly strike-slip';
    if (mechOk) reasons.push(`${mech} mechanism (rake ${np.rake.toFixed(0)}°, dip-slip fraction |sin rake| = ${dipSlip!.toFixed(2)})`);
    else notes.push(`predominantly strike-slip mechanism (rake ${np.rake.toFixed(0)}°) — vertical seafloor displacement expected to be small unless bathymetric slopes are steep`);
  } else {
    notes.push('no moment-tensor mechanism available');
  }
  return { met: magOk && depthOk && mechOk, reasons, notes };
}

export interface ConsistencyTolerances {
  /** |observed − simulated| arrival tolerance (min) */
  arrivalTolMin: number;
  /** amplitude agreement factor (obs/sim within [1/f, f]) */
  amplitudeFactor: number;
}

export const DEFAULT_CONSISTENCY: ConsistencyTolerances = {
  arrivalTolMin: 15,
  amplitudeFactor: 3,
};

/**
 * Per-station obs-vs-sim consistency. Diagnostic tolerances (documented):
 * arrival within ±15 min (≈ the 15-min DART monitor-mode cadence plus model
 * timing error) and amplitude within a factor 3 (source-model + ETOPO1
 * uncertainty). A station can only be 'consistent' when BOTH an observation
 * trigger and a simulated signal exist.
 */
export function stationConsistency(c: StationComparison, tol: ConsistencyTolerances = DEFAULT_CONSISTENCY): boolean | null {
  if (c.obsArrivalMin === null || c.simArrivalMin === null) return null;
  if (c.obsMaxAmplitudeM === null || c.simMaxAmplitudeM === null) return null;
  const dtOk = Math.abs(c.obsArrivalMin - c.simArrivalMin) <= tol.arrivalTolMin;
  const ratio = c.obsMaxAmplitudeM / Math.max(1e-6, c.simMaxAmplitudeM);
  const ampOk = ratio >= 1 / tol.amplitudeFactor && ratio <= tol.amplitudeFactor;
  return dtOk && ampOk;
}

export interface EvidenceInput {
  event: TsunamiEvent;
  detections: DetectionResult[];
  /** null when no simulation has been run */
  comparisons: StationComparison[] | null;
  screening?: ScreeningResult;
}

/**
 * Classify the evidence. Rules (all surfaced as `reasons` in the UI):
 *   L0 insufficient: no detection has availability 'available'.
 *   L1 none-detected: ≥1 available detection, none triggered.
 *   L2 possible: screening met (with or without data — data absence keeps
 *      the level at L0/L1 as appropriate) OR trigger(s) exist but no
 *      simulation to compare against yet.
 *   L3 consistent: ≥1 triggered station whose comparison is consistent;
 *      triggers that fail consistency are reported as inconsistent, not
 *      discarded.
 *   L4 confirmed-official: only when a curated official link exists AND is
 *      explicitly cited by the operator — the UI renders it as a citation,
 *      this function never fabricates it from model output.
 */
export function classifyEvidence(input: EvidenceInput): EvidenceAssessment {
  const { event, detections, comparisons } = input;
  const screening = input.screening ?? screenSeismic(event);
  const usable = detections.filter((d) => d.availability.state === 'available');
  const triggered = usable.filter((d) => d.triggerMs !== null);
  const caveats: string[] = [];
  const contributing: EvidenceAssessment['contributing'] = [];

  // base level from data availability
  let level: EvidenceLevel;
  let headline: string;
  const reasons: string[] = [];

  if (usable.length === 0) {
    level = 'insufficient';
    const nMiss = detections.length;
    headline =
      nMiss === 0
        ? 'No ocean-sensor records in range of this event'
        : 'Ocean-sensor records could not be retrieved for this event';
    reasons.push(
      detections.length > 0
        ? `${detections.length} station record(s) attempted; none usable (${detections
            .map((d) => `${d.stationId}: ${d.availability.state === 'missing' ? d.availability.reason : 'unknown'}`)
            .slice(0, 3)
            .join('; ')})`
        : 'no DART station within the search radius',
    );
    caveats.push('missing evidence is not evidence of absence — the event may or may not have generated a tsunami');
    if (screening.met) {
      reasons.push('seismic screening criteria met (heuristic)');
      contributing.push('seismic-screen');
    }
    return { level, headline, reasons, caveats, contributing };
  }

  contributing.push('ocean-sensor');

  if (triggered.length === 0) {
    level = 'none-detected';
    headline = `No tsunami-like signal detected above ${usable.length > 0 ? 'the noise-adaptive threshold' : 'threshold'} at ${usable.length} station${usable.length === 1 ? '' : 's'}`;
    reasons.push(
      `${usable.length} station record(s) with event coverage show no trigger (k·σ rule, k = 5)`,
    );
    caveats.push('a null result is limited to the sensors, cadence and noise level actually available');
    caveats.push('deep-ocean sensors can miss small local tsunamis; coastal impacts cannot be excluded');
    if (screening.met) {
      reasons.push('seismic screening criteria met (heuristic) despite the null sensor result');
      contributing.push('seismic-screen');
    }
    return { level, headline, reasons, caveats, contributing };
  }

  // ≥1 trigger
  contributing.push('seismic-screen');
  reasons.push(
    `${triggered.length} station(s) triggered: ${triggered
      .map((d) => `${d.stationId} (+${(d.arrivalMin ?? 0).toFixed(0)} min, max ${(100 * (d.maxAmplitudeM ?? 0)).toFixed(0)} cm)`)
      .join(', ')}`,
  );

  if (!comparisons) {
    level = 'possible';
    headline = 'Possible tsunami signal — run the simulation to test consistency';
    caveats.push('an anomalous wave is not yet attributed: tides, meteotsunamis and sensor glitches can also trigger');
    return { level, headline, reasons, caveats, contributing };
  }

  contributing.push('simulation');
  const cmpOk = comparisons.filter((c) => c.consistent === true);
  const cmpBad = comparisons.filter((c) => c.consistent === false);
  const cmpNull = comparisons.filter((c) => c.consistent === null);

  if (cmpOk.length > 0) {
    level = 'consistent';
    headline = `${cmpOk.length} station${cmpOk.length === 1 ? '' : 's'} show observations consistent with the simulated tsunami`;
    reasons.push(
      ...cmpOk.map(
        (c) =>
          `${c.stationId}: arrival ${c.arrivalResidualMin === null ? '—' : `${c.arrivalResidualMin >= 0 ? '+' : ''}${c.arrivalResidualMin.toFixed(0)} min`}, amplitude ratio ${(c.amplitudeRatio ?? NaN).toFixed(2)}`,
      ),
    );
  } else {
    level = 'possible';
    headline = 'Wave detected, but not yet consistent with this simulation';
    if (cmpBad.length > 0) {
      reasons.push(
        ...cmpBad.map(
          (c) =>
            `${c.stationId} inconsistent: ${c.note}`,
        ),
      );
    }
    if (cmpNull.length > 0) {
      reasons.push(`${cmpNull.length} station(s) without a comparable simulated signal (${cmpNull.map((c) => c.stationId).join(', ')})`);
    }
  }

  caveats.push('buoy agreement is correlated evidence, not independent confirmation — all stations sample the same wave field');
  caveats.push('consistency tolerances (±15 min, ×3 amplitude) are documented diagnostics, not validated error bars');
  if (event.officialUrl) {
    caveats.push(`official status: see ${event.officialSource ?? 'the authoritative source'} (linked below) — this system never asserts it`);
  }
  return { level, headline, reasons, caveats, contributing };
}

/** UI labels for the five levels. */
export const EVIDENCE_LEVEL_INFO: Record<EvidenceLevel, { label: string; tone: 'neutral' | 'warn' | 'good' | 'info' }> = {
  insufficient: { label: 'Insufficient data', tone: 'neutral' },
  'none-detected': { label: 'No tsunami evidence in available observations', tone: 'neutral' },
  possible: { label: 'Possible tsunami signal', tone: 'warn' },
  consistent: { label: 'Multiple observations consistent with a tsunami', tone: 'good' },
  'confirmed-official': { label: 'Confirmed by an authoritative source (see link)', tone: 'info' },
};
