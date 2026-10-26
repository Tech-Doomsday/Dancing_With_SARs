/**
 * Plain-language verdict computation — the "five-second answer".
 *
 * Given an analysis summary, produce ONE sentence a non-geophysicist can read
 * at the top of the results panel, e.g.
 *   "No motion detectable. Ionosphere noise (72 mm) exceeds the signal (48 mm)."
 *
 * Rules (honesty contract):
 *  - never says "no movement" (only "no change detected above the threshold");
 *  - never says "earthquake confirmed" (at most "possible earthquake-related
 *    pattern");
 *  - when the systematic floor exceeds the largest signal, that comparison IS
 *    the headline, because it is the reason nothing was flagged;
 *  - numbers are rounded for reading, exact values stay in the stats grid.
 */

import type { ChangeSummary, AttributionOutput } from './types';

export type VerdictTone = 'detected' | 'none' | 'inconclusive';

export interface Verdict {
  tone: VerdictTone;
  /** The one-line headline. */
  headline: string;
  /** One supporting sentence (what would make this change / what it means). */
  support: string;
}

function mm(m: number, digits = 0): string {
  return `${(m * 1000).toFixed(digits)} mm`;
}

/** Credible-area rule mirrors pipeline Stage C / results-panel. */
export function credibleAreaKm2(s: ChangeSummary): number {
  return Math.max(1, s.pixelAreaKm2 * 4);
}

export function outcomeOf(s: ChangeSummary): VerdictTone {
  if (s.affectedAreaKm2 === 0) return 'none';
  return s.affectedAreaKm2 < credibleAreaKm2(s) ? 'inconclusive' : 'detected';
}

/**
 * Compute the plain-language verdict from a summary (+ attribution if known).
 * Pure: same inputs -> same sentence, so it is unit-tested.
 */
export function computeVerdict(
  s: ChangeSummary,
  attribution?: Pick<AttributionOutput, 'hypotheses'>,
): Verdict {
  const tone = outcomeOf(s);
  const largestSignalM = Math.max(s.maxAwayM, Math.abs(s.maxTowardM));
  const floorM = s.systematicIonoFloorM;
  const floorDominates = floorM > 0 && largestSignalM > 0 && floorM >= largestSignalM;

  if (tone === 'none') {
    if (floorDominates) {
      return {
        tone,
        headline: `No motion detectable. Ionosphere noise (${mm(floorM)}) exceeds the signal (${mm(largestSignalM)}).`,
        support:
          'The radar signal passed through a disturbed ionosphere, and the size of that contamination is larger than the biggest apparent change in this scene — so no ground movement can be claimed from this pair.',
      };
    }
    return {
      tone,
      headline: `No change detected above the ${mm(s.thresholdMedianM)} threshold (largest signal ${mm(largestSignalM)}).`,
      support:
        'Smaller changes may still be present; the analysis only flags what it can distinguish from measurement noise.',
    };
  }

  if (tone === 'inconclusive') {
    return {
      tone,
      headline: `Inconclusive — only isolated pixels (${s.affectedAreaKm2 < 0.1 ? 'under 0.1' : s.affectedAreaKm2.toFixed(1)} km²) crossed the threshold.`,
      support:
        'At this signal quality, stray pixels are consistent with measurement noise rather than a real patch of ground movement.',
    };
  }

  // Detected: mention the earthquake check outcome in the headline support.
  const quake = attribution?.hypotheses.find((h) => h.id === 'earthquake');
  let quakeSupport = '';
  if (quake) {
    if (quake.status === 'supported') {
      quakeSupport =
        ' The pattern also fits what an earthquake model predicts for a nearby recorded event — consistent with it, though a single radar view cannot prove cause.';
    } else if (quake.status === 'not-supported') {
      quakeSupport =
        ' It does not match what nearby recorded earthquakes would predict, so another explanation is more likely.';
    } else {
      quakeSupport =
        ' Nearby earthquake records were checked; none clearly explains the pattern.';
    }
  }
  return {
    tone,
    headline: `Possible change detected — up to ${mm(Math.max(s.maxAwayM, Math.abs(s.maxTowardM)))} across ${areaLabel(s.affectedAreaKm2)}.`,
    support:
      `This passes the detection threshold, but it is an <em>apparent</em> movement toward or away from the satellite, not proof of ground motion.${quakeSupport}`,
  };
}

export function areaLabel(km2: number): string {
  if (km2 >= 1000) return `${Math.round(km2).toLocaleString()} km²`;
  if (km2 >= 100) return `${Math.round(km2)} km²`;
  if (km2 >= 1) return `${km2.toFixed(1)} km²`;
  return `${km2.toFixed(2)} km²`;
}

/** Same verdict computation for a RECORDED run (summary-shaped subset). */
export function computeRecordedVerdict(r: {
  summary: Pick<ChangeSummary, 'maxAwayM' | 'maxTowardM' | 'systematicIonoFloorM' | 'thresholdMedianM' | 'affectedAreaKm2'>;
  measuredFindings?: { maxAbsLosM: number };
}): Verdict {
  const largest = r.measuredFindings?.maxAbsLosM ?? Math.max(r.summary.maxAwayM || 0, Math.abs(r.summary.maxTowardM || 0));
  const floor = r.summary.systematicIonoFloorM;
  if (floor > 0 && largest > 0 && floor >= largest) {
    return {
      tone: 'none',
      headline: `No motion detectable. Ionosphere noise (${mm(floor)}) exceeds the signal (${mm(largest)}).`,
      support:
        'Recorded from the real analysis of this product: the ionospheric contamination floor is larger than the biggest apparent change, so no ground movement can be claimed from this pair.',
    };
  }
  return {
    tone: 'none',
    headline: `No change detected above the ${mm(r.summary.thresholdMedianM || 0.01)} threshold (largest signal ${mm(largest)}).`,
    support: 'Recorded from the real analysis of this product.',
  };
}
