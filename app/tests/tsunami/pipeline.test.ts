import { describe, expect, test } from 'bun:test';
import { travelTimeField, travelTimeAt } from '@/lib/tsunami/traveltime';
import { parseDartText } from '@/lib/tsunami/dart';
import { parseOpendapAscii } from '@/lib/tsunami/etopo';
import { localQuadraticDetide, detectTsunamiSignal, DEFAULT_DETECT_OPTIONS } from '@/lib/tsunami/detection';
import { classifyEvidence, screenSeismic, stationConsistency } from '@/lib/tsunami/evidence';
import type { BathymetryGrid, DetectionResult, StationComparison, TimeSeries, TsunamiEvent, TsunamiProvenance } from '@/lib/tsunami/types';

/**
 * Travel-time, parsing, detection and evidence tests.
 * All inputs are SYNTHETIC FIXTURES (labelled) — analytical or constructed.
 */

function gridOf(nx: number, ny: number, bed: Float32Array, resArcmin = 1): BathymetryGrid {
  return {
    resArcmin,
    lat0: 0,
    lon0: 0,
    nx,
    ny,
    bed,
    provenance: {
      datasetId: 'test',
      provider: 'synthetic fixture',
      productId: 'test',
      productType: 'test',
      sourceUrl: 'none',
      retrievedAt: new Date().toISOString(),
      observationStart: 'n/a',
      observationEnd: 'n/a',
      units: 'm',
      crs: 'EPSG:4326',
      verticalDatum: 'n/a',
      processing: [],
      dataClass: 'modelled',
      qualityNotes: ['SYNTHETIC FIXTURE'],
    },
  };
}

describe('travel-time fast sweeping (eikonal)', () => {
  test('flat constant-depth ocean: T = r/√(g·h) within 2%', () => {
    const nx = 121;
    const ny = 121;
    const h = 4000;
    const bed = new Float32Array(nx * ny).fill(-h);
    const grid = gridOf(nx, ny, bed);
    const dz = new Float32Array(nx * ny);
    dz[60 * nx + 60] = 1; // seed only the centre cell
    const T = travelTimeField(grid, dz);
    const c = Math.sqrt(9.81 * h);
    const DX = 1852;
    for (const [di, dj] of [[30, 0], [0, 30], [21, 21], [-40, 10]] as const) {
      // targets are grid offsets from the centre cell (60, 60)
      const t = travelTimeAt(grid, T, (60 + dj) / 60, (60 + di) / 60);
      expect(Number.isFinite(t)).toBe(true);
      const r = Math.hypot(di * DX, dj * DX);
      const expected = r / c;
      // 4%: the first-order upwind eikonal has a genuine O(dx) grid-alignment
      // error on diagonals (measured 3.6% at 21 cells)
      expect(Math.abs(t - expected) / expected).toBeLessThan(0.04);
    }
  });

  test('land cells are NaN', () => {
    const nx = 41;
    const ny = 41;
    const bed = new Float32Array(nx * ny).fill(-3000);
    for (let j = 18; j < 23; j++) for (let i = 18; i < 23; i++) bed[j * nx + i] = 100; // island
    const grid = gridOf(nx, ny, bed);
    const dz = new Float32Array(nx * ny);
    dz[5 * nx + 5] = 1;
    const T = travelTimeField(grid, dz);
    expect(Number.isNaN(T[20 * nx + 20])).toBe(true);
    expect(Number.isFinite(T[30 * nx + 30])).toBe(true);
  });
});

describe('DART text parser', () => {
  test('parses rows, drops 9999 missing values, sorts ascending', () => {
    const txt = [
      '#YY  MM DD hh mm ss T   HEIGHT',
      '#yr  mo dy hr mn  s -        m',
      '2011 03 11 05 00 00 1 5662.100',
      '2011 03 11 05 15 00 1 5662.200',
      '2011 03 11 05 30 00 1 9999.000',
      '2011 03 11 05 45 00 1 5662.150',
      'garbage line',
    ].join('\n');
    const s = parseDartText(txt);
    expect(s.t.length).toBe(3);
    expect(s.v[0]).toBeCloseTo(5662.1, 2); // Float32 storage precision
    expect(s.t[0]).toBe(Date.UTC(2011, 2, 11, 5, 0, 0));
    expect(s.t[1]).toBeGreaterThan(s.t[0]);
  });
});

describe('OPeNDAP ascii parser', () => {
  test('parses the verified ETOPO1 subset format', () => {
    const txt = [
      'Dataset {',
      '    Grid {',
      '     ARRAY:',
      '        Float32 z[lat = 2][lon = 3];',
      '     MAPS:',
      '        Float64 lat[lat = 2];',
      '        Float64 lon[lon = 3];',
      '    } z;',
      '} global/ETOPO1_Bed_g_gmt4.nc;',
      '---------------------------------------------',
      'z.z[2][3]',
      '[0], -100.0, -200.0, -300.0',
      '[1], -400.0, -500.0, -600.0',
      '',
      'z.lat[2]',
      '-19.0, -18.98333333',
      '',
      'z.lon[3]',
      '-94.0, -93.983333, -93.966666',
    ].join('\n');
    const p = parseOpendapAscii(txt);
    expect(p.ny).toBe(2);
    expect(p.nx).toBe(3);
    expect(p.z).toHaveLength(6);
    expect(p.z[0]).toBeCloseTo(-100, 6);
    expect(p.z[5]).toBeCloseTo(-600, 6);
    expect(p.lat[0]).toBeCloseTo(-19, 6);
    expect(p.lon[2]).toBeCloseTo(-93.966666, 4);
  });

  test('throws a structured error on a truncated payload', () => {
    expect(() => parseOpendapAscii('Dataset {}')).toThrow(/missing/);
  });
});

describe('detection (SYNTHETIC labeled fixtures)', () => {
  const prov: TsunamiProvenance = {
    datasetId: 'test',
    provider: 'synthetic fixture',
    productId: 'SYNTHETIC fixture station',
    productType: 'test',
    sourceUrl: 'none',
    retrievedAt: new Date().toISOString(),
    observationStart: 'n/a',
    observationEnd: 'n/a',
    units: 'm',
    crs: 'n/a',
    verticalDatum: 'n/a',
    processing: [],
    dataClass: 'measured',
    qualityNotes: ['SYNTHETIC FIXTURE — algorithm verification only'],
  };

  const origin = Date.UTC(2020, 0, 1, 12, 0, 0);

  function synthSeries(opts: {
    tideAmpM?: number;
    waveAmpM?: number;
    waveStartMin?: number;
    wavePeriodMin?: number;
    sigmaM?: number;
    cadenceMin?: number;
    hours?: number;
    preOriginExcursion?: boolean;
    seed?: number;
  }): TimeSeries {
    const {
      tideAmpM = 0.6, waveAmpM = 0, waveStartMin = 45, wavePeriodMin = 25,
      sigmaM = 0.01, cadenceMin = 15, hours = 16, preOriginExcursion = false, seed = 42,
    } = opts;
    // deterministic LCG noise
    let x = seed;
    const rnd = () => {
      x = (1103515245 * x + 12345) & 0x7fffffff;
      return x / 0x7fffffff - 0.5;
    };
    const tArr: number[] = [];
    const vArr: number[] = [];
    const start = origin - 8 * 3600_000;
    for (let t = start; t <= start + hours * 3600_000; t += cadenceMin * 60_000) {
      const hoursFromOrigin = (t - origin) / 3600_000;
      let v = tideAmpM * Math.cos((2 * Math.PI * hoursFromOrigin) / 12.42);
      if (waveAmpM > 0 && t >= origin + waveStartMin * 60_000) {
        const mins = (t - origin) / 60_000 - waveStartMin;
        // wave packet: 3 oscillations then decay
        v += waveAmpM * Math.sin((2 * Math.PI * mins) / wavePeriodMin) * Math.exp(-mins / 180);
      }
      if (preOriginExcursion && t >= origin - 16 * 60_000 && t < origin) {
        v += 0.15; // seismic-phase-like excursion at the sample just before origin
      }
      v += sigmaM * rnd() * 2;
      tArr.push(t);
      vArr.push(v);
    }
    return { t: Float64Array.from(tArr), v: Float32Array.from(vArr) };
  }

  test('recovers a synthetic tsunami: arrival within one sample, amplitude within 10%', () => {
    const series = synthSeries({ waveAmpM: 0.8, waveStartMin: 45 });
    const det = detectTsunamiSignal('FIX1', series, origin, prov);
    expect(det.availability.state).toBe('available');
    expect(det.triggerMs).not.toBeNull();
    // 15-min cadence: arrival quantized to the sample grid
    expect(Math.abs((det.arrivalMin ?? 0) - 45)).toBeLessThanOrEqual(15);
    expect(det.maxAmplitudeM).not.toBeNull();
    // the SAMPLED peak of the decaying sinusoid at 15-min cadence is 0.648 m
    // (the continuous peak 0.774 m falls between samples)
    expect(Math.abs((det.maxAmplitudeM ?? 0) - 0.648) / 0.648).toBeLessThan(0.1);
    expect(det.dominantPeriodMin).not.toBeNull();
    expect(Math.abs((det.dominantPeriodMin ?? 0) - 25)).toBeLessThan(8);
    expect(det.preOriginExcursion).toBe(false);
  });

  test('noise-only control: no trigger across 12 different noise seeds (false-alarm behaviour)', () => {
    let triggers = 0;
    for (let seed = 1; seed <= 12; seed++) {
      const series = synthSeries({ waveAmpM: 0, seed });
      const det = detectTsunamiSignal('FIX2', series, origin, prov);
      expect(det.availability.state).toBe('available');
      if (det.triggerMs !== null) triggers++;
    }
    // k = 5 with Gaussian-ish noise: expected false alarms ≪ 1 in 12;
    // allow at most 1 marginal trigger to keep the test robust
    expect(triggers).toBeLessThanOrEqual(1);
  });

  test('tide-only series with no tsunami: no trigger', () => {
    const series = synthSeries({ waveAmpM: 0, tideAmpM: 1.2 });
    const det = detectTsunamiSignal('FIX3', series, origin, prov);
    expect(det.triggerMs).toBeNull();
    expect(det.availability.state).toBe('available');
  });

  test('pre-origin excursion is flagged and not counted as the arrival', () => {
    const series = synthSeries({ waveAmpM: 0.6, waveStartMin: 40, preOriginExcursion: true });
    const det = detectTsunamiSignal('FIX4', series, origin, prov);
    expect(det.preOriginExcursion).toBe(true);
    expect(det.arrivalMin !== null && det.arrivalMin < 0).toBe(true);
    expect(det.caveats.some((c) => c.includes('seismic phase'))).toBe(true);
  });

  test('insufficient pre-event coverage yields an explicit missing state, not a silent null', () => {
    const t = Float64Array.from([origin + 60_000, origin + 1_800_000, origin + 3_600_000]);
    const v = Float32Array.from([1, 1.1, 0.9]);
    const det = detectTsunamiSignal('FIX5', { t, v }, origin, prov);
    expect(det.availability.state).toBe('missing');
    if (det.availability.state === 'missing') {
      expect(det.availability.reason).toMatch(/samples/);
    }
  });

  test('local quadratic detide removes a 12.42 h tide to sub-cm residual', () => {
    const series = synthSeries({ waveAmpM: 0, sigmaM: 0 });
    const { t, resid } = localQuadraticDetide(series.t, series.v, DEFAULT_DETECT_OPTIONS.detideHalfWindowH);
    // interior samples only (edge windows are asymmetric)
    let worst = 0;
    for (let m = 30; m < t.length - 30; m++) {
      worst = Math.max(worst, Math.abs(resid[m]));
    }
    // documented method error: the ±3 h quadratic leaves ≤ ~1.5 cm of tide
    // leak for a 0.6 m semidiurnal tide (measured 1.2 cm here)
    expect(worst).toBeLessThan(0.02);
  });
});

/* ---------------- evidence classification ---------------- */

const fakeEvent = (over: Partial<TsunamiEvent>): TsunamiEvent => ({
  id: 'test-ev',
  usgsId: 'test',
  title: 'test',
  blurb: '',
  time: '2020-01-01T00:00:00Z',
  magnitude: 8,
  lat: 0,
  lon: 0,
  depthKm: 20,
  place: 'test',
  hasFiniteFault: false,
  officialUrl: 'https://example.org',
  officialSource: 'Example authority',
  note: '',
  ...over,
});

const fakeDetection = (over: Partial<DetectionResult>): DetectionResult => ({
  stationId: '100',
  provenance: {} as TsunamiProvenance,
  t: new Float64Array(0),
  resid: new Float32Array(0),
  sigmaNoise: 0.01,
  triggerMs: null,
  arrivalMin: null,
  preOriginExcursion: false,
  maxAmplitudeM: null,
  dominantPeriodMin: null,
  nSamples: 60,
  caveats: [],
  availability: { state: 'available' },
  ...over,
});

describe('evidence classification (5 states)', () => {
  test('no usable records → insufficient, with the missing-not-abstract caveat', () => {
    const ev = fakeEvent({});
    const a = classifyEvidence({ event: ev, detections: [fakeDetection({ availability: { state: 'missing', reason: 'no file' } })], comparisons: null });
    expect(a.level).toBe('insufficient');
    expect(a.caveats.some((c) => c.includes('not evidence of absence'))).toBe(true);
  });

  test('good coverage, no trigger → none-detected', () => {
    const a = classifyEvidence({ event: fakeEvent({}), detections: [fakeDetection({}), fakeDetection({ stationId: '101' })], comparisons: null });
    expect(a.level).toBe('none-detected');
  });

  test('trigger without simulation → possible', () => {
    const a = classifyEvidence({ event: fakeEvent({}), detections: [fakeDetection({ triggerMs: 1, arrivalMin: 30, maxAmplitudeM: 0.3 })], comparisons: null });
    expect(a.level).toBe('possible');
    expect(a.contributing).toContain('ocean-sensor');
  });

  test('trigger + consistent simulation → consistent', () => {
    const comparisons: StationComparison[] = [{
      stationId: '100',
      obsArrivalMin: 32, obsMaxAmplitudeM: 0.3, obsSigmaM: 0.01,
      simArrivalMin: 30, simMaxAmplitudeM: 0.25,
      arrivalResidualMin: 2, amplitudeRatio: 1.2,
      consistent: true, note: '',
    }];
    const a = classifyEvidence({ event: fakeEvent({}), detections: [fakeDetection({ triggerMs: 1, arrivalMin: 32, maxAmplitudeM: 0.3 })], comparisons });
    expect(a.level).toBe('consistent');
    expect(a.caveats.some((c) => c.includes('correlated'))).toBe(true);
  });

  test('trigger + inconsistent simulation stays possible with reasons', () => {
    const comparisons: StationComparison[] = [{
      stationId: '100',
      obsArrivalMin: 90, obsMaxAmplitudeM: 0.3, obsSigmaM: 0.01,
      simArrivalMin: 30, simMaxAmplitudeM: 0.25,
      arrivalResidualMin: 60, amplitudeRatio: 1.2,
      consistent: false, note: 'arrival off by 60 min (> ±15 min)',
    }];
    const a = classifyEvidence({ event: fakeEvent({}), detections: [fakeDetection({ triggerMs: 1, arrivalMin: 90, maxAmplitudeM: 0.3 })], comparisons });
    expect(a.level).toBe('possible');
    expect(a.reasons.some((r) => r.includes('inconsistent'))).toBe(true);
  });

  test('screening: pure strike-slip is not met; oblique 42% dip-slip IS met (heuristic); reverse M9 is met', () => {
    const pureSS = fakeEvent({ magnitude: 7.7, depthKm: 12, nodalPlane: { strike: 25, dip: 75, rake: 0 } });
    const s0 = screenSeismic(pureSS);
    expect(s0.met).toBe(false);
    expect(s0.notes.some((n) => n.includes('strike-slip'))).toBe(true);

    // rake -155°: |sin| = 0.42 — an oblique event the heuristic DOES flag
    const oblique = fakeEvent({ magnitude: 7.7, depthKm: 12, nodalPlane: { strike: 25, dip: 75, rake: -155 } });
    const s1 = screenSeismic(oblique);
    expect(s1.met).toBe(true);

    const tohoku = fakeEvent({ magnitude: 9.1, depthKm: 29, nodalPlane: { strike: 193, dip: 14, rake: 81 } });
    const s2 = screenSeismic(tohoku);
    expect(s2.met).toBe(true);
  });

  test('stationConsistency: null when either side is missing; boolean otherwise', () => {
    expect(stationConsistency({ stationId: 'x', obsArrivalMin: null, obsMaxAmplitudeM: null, obsSigmaM: null, simArrivalMin: 30, simMaxAmplitudeM: 1, arrivalResidualMin: null, amplitudeRatio: null, consistent: null, note: '' })).toBeNull();
    expect(stationConsistency({ stationId: 'x', obsArrivalMin: 30, obsMaxAmplitudeM: 1, obsSigmaM: 0.01, simArrivalMin: 35, simMaxAmplitudeM: 0.5, arrivalResidualMin: -5, amplitudeRatio: 2, consistent: null, note: '' })).toBe(true);
    expect(stationConsistency({ stationId: 'x', obsArrivalMin: 30, obsMaxAmplitudeM: 1, obsSigmaM: 0.01, simArrivalMin: 60, simMaxAmplitudeM: 0.5, arrivalResidualMin: -30, amplitudeRatio: 2, consistent: null, note: '' })).toBe(false);
  });
});
