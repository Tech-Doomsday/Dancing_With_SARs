import { describe, test, expect } from 'bun:test';
import { computeVerdict, computeRecordedVerdict, outcomeOf, areaLabel } from '@/lib/science/verdict';
import type { ChangeSummary, AttributionOutput } from '@/lib/science/types';
import { granuleTimes, pairKeyOf, haversineKm } from '@/lib/data/pairs';
import { toGeoTiff } from '@/components/app/export';

/* ---------- verdict engine ---------- */

function fakeSummary(over: Partial<ChangeSummary> = {}): ChangeSummary {
  return {
    validPixelCount: 1000,
    validFraction: 0.5,
    coherenceMean: 0.6,
    coherenceMedian: 0.6,
    pixelAreaKm2: 0.0064,
    analysedAreaKm2: 500,
    referenceOffsetM: 0,
    referenceSigmaM: 0.003,
    affectedAreaKm2: 0,
    systematicIonoFloorM: 0,
    ionoFloorBasis: 'none',
    correctionStatus: {
      flatteningApplied: true,
      ionosphericPhaseCorrectionApplied: false,
      troposphericPhaseCorrectionApplied: false,
      note: '',
    },
    towardAreaKm2: 0,
    awayAreaKm2: 0,
    maskedPixelCount: 1000,
    detectedPixelCount: 0,
    thresholdMedianM: 0.01,
    clusters: { count: 0, largestKm2: 0, sizesPx: [] } as never,
    nullScreen: { scenes: 0, detectedFractionMean: 0, detectedFractionMax: 0, scenesWithClusters: 0, correlationLengthPx: 0, note: '' } as never,
    maxAwayM: 0,
    maxTowardM: 0,
    medianSigmaAnomalyM: undefined,
    medianSigmaM: 0.005,
    displacementIntegralMKm2: 0,
    connectedComponentCount: 1,
    ...over,
  } as ChangeSummary;
}

describe('verdict engine (plain-language five-second answer)', () => {
  test('floor-dominant negative: the headline IS the signal-vs-noise comparison', () => {
    const s = fakeSummary({
      affectedAreaKm2: 0,
      maxAwayM: 0.048,
      maxTowardM: -0.02,
      systematicIonoFloorM: 0.072,
      thresholdMedianM: 0.072,
    });
    const v = computeVerdict(s);
    expect(v.tone).toBe('none');
    expect(v.headline).toBe('No motion detectable. Ionosphere noise (72 mm) exceeds the signal (48 mm).');
    // Honesty contract: never claims the ground did not move.
    expect(v.headline).not.toContain('no movement');
  });

  test('plain negative without a dominant floor cites the threshold and largest signal', () => {
    const s = fakeSummary({ affectedAreaKm2: 0, maxAwayM: 0.006, systematicIonoFloorM: 0, thresholdMedianM: 0.01 });
    const v = computeVerdict(s);
    expect(v.tone).toBe('none');
    expect(v.headline).toContain('No change detected above the 10 mm threshold');
    expect(v.headline).toContain('largest signal 6 mm');
  });

  test('stray-pixel result reads inconclusive, not detected', () => {
    const s = fakeSummary({ affectedAreaKm2: 0.012, detectedPixelCount: 2 });
    expect(outcomeOf(s)).toBe('inconclusive');
    const v = computeVerdict(s);
    expect(v.headline).toContain('Inconclusive');
  });

  test('credible-area result reads as a possible change with magnitude and area', () => {
    const s = fakeSummary({ affectedAreaKm2: 1372, maxAwayM: 0.136, detectedPixelCount: 200000 });
    expect(outcomeOf(s)).toBe('detected');
    const v = computeVerdict(s);
    expect(v.headline).toContain('Possible change detected');
    expect(v.headline).toContain('136 mm');
    expect(v.headline).toContain('1,372 km²');
  });

  test('detected result with a supported earthquake hypothesis stays at "consistent", never "caused"', () => {
    const s = fakeSummary({ affectedAreaKm2: 1372, maxAwayM: 0.136, detectedPixelCount: 200000 });
    const attr = {
      hypotheses: [
        { id: 'earthquake', status: 'supported' as const, label: 'x', description: '', evidence: [] },
      ],
    } as unknown as Pick<AttributionOutput, 'hypotheses'>;
    const v = computeVerdict(s, attr);
    expect(v.support).toContain('consistent with it');
    expect(v.support).not.toContain('caused by the earthquake');
    expect(v.support).not.toContain('confirms');
  });

  test('recorded verdict reproduces the Mojave headline from the recorded figures', () => {
    const v = computeRecordedVerdict({
      summary: { maxAwayM: 0, maxTowardM: 0, systematicIonoFloorM: 0.0718, thresholdMedianM: 0.0718, affectedAreaKm2: 0 } as never,
      measuredFindings: { maxAbsLosM: 0.048 },
    });
    expect(v.headline).toBe('No motion detectable. Ionosphere noise (72 mm) exceeds the signal (48 mm).');
  });

  test('area labels never round a small positive area to 0 km²', () => {
    expect(areaLabel(0.013)).toBe('0.01 km²');
    expect(areaLabel(5.4)).toBe('5.4 km²');
    expect(areaLabel(1372.4)).toBe('1,372 km²');
  });
});

/* ---------- pair finding (pure helpers) ---------- */

describe('pair grouping helpers', () => {
  const gid = 'NISAR_L2_PR_GUNW_021_013_D_071_022_4000_SH_20260522T024038_20260522T024116_20260603T024038_20260603T024115_P05023_N_F_J_001';

  test('granuleTimes extracts both acquisition times from a real granule id', () => {
    const t = granuleTimes(gid);
    expect(t).not.toBeNull();
    expect(t!.reference.toISOString().slice(0, 10)).toBe('2026-05-22');
    expect(t!.secondary.toISOString().slice(0, 10)).toBe('2026-06-03');
  });

  test('the same track/frames on a different revisit is a DIFFERENT pair', () => {
    const other = gid
      .replace('20260522T024038', '20260614T024038')
      .replace('20260522T024116', '20260614T024116')
      .replace('20260603T024038', '20260626T024038')
      .replace('20260603T024115', '20260626T024115');
    expect(pairKeyOf(gid)).not.toBe(pairKeyOf(other));
  });

  test('the same pair repeated as overlapping frame variants groups together', () => {
    // Different frame numbers but the same acquisitions + secondary day.
    const variant = gid.replace('_071_022_', '_072_023_');
    expect(pairKeyOf(gid)).toBe(pairKeyOf(variant));
  });

  test('haversine distance is sane for known points', () => {
    const la = { lat: 34.05, lon: -118.24 };
    const sf = { lat: 37.77, lon: -122.42 };
    const d = haversineKm(la, sf);
    expect(d).toBeGreaterThan(540);
    expect(d).toBeLessThan(560);
  });
});

/* ---------- GeoTIFF writer (structural) ---------- */

describe('client GeoTIFF writer', () => {
  test('emits a little-endian classic TIFF with float pixels and 15 tags', async () => {
    const meta = { xStart: 400000, xSpacing: 80, yStart: 3800000, ySpacing: -80, width: 4, height: 3, epsg: 32611 };
    const data = new Float32Array([0, 1, -2.5, NaN, 0.5, 0.25, -0.125, 3, 9, -9, 12, -12]);
    const blob = toGeoTiff(data, meta, 'test');
    const buf = new Uint8Array(await blob.arrayBuffer());
    // Header: little-endian "II", magic 42.
    expect(buf[0]).toBe(0x49);
    expect(buf[1]).toBe(0x49);
    expect(buf[2]).toBe(42);
    expect(buf[3]).toBe(0);
    const view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
    // First IFD offset = 8.
    expect(view.getUint32(4, true)).toBe(8);
    // IFD entry count == 15 tags declared.
    expect(view.getUint16(8, true)).toBe(15);
    // Find SAMPLEFORMAT (339) among the tags and confirm value 3 (IEEE float).
    let sampleFormat = 0;
    for (let i = 0; i < 15; i++) {
      const tag = view.getUint16(10 + i * 12, true);
      if (tag === 339) sampleFormat = view.getUint16(10 + i * 12 + 8, true);
    }
    expect(sampleFormat).toBe(3);
    // The blob type is TIFF.
    expect(blob.type).toBe('image/tiff');
  });

  test('rejects a data/meta size mismatch instead of writing a corrupt file', () => {
    const meta = { xStart: 0, xSpacing: 1, yStart: 0, ySpacing: -1, width: 4, height: 3, epsg: 32611 };
    expect(() => toGeoTiff(new Float32Array(5), meta, 'bad')).toThrow('size mismatch');
  });
});
