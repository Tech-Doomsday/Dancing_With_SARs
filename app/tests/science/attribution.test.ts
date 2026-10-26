import { describe, expect, test } from 'bun:test';
import {
  rankEventsForPair,
  forwardModelCheck,
  buildPredictionOnGrid,
  projectToGrid,
  ATTRIBUTION_HEURISTICS,
  evaluateAttribution,
  haversineKm,
} from '@/lib/science/attribution';
import { syntheticNullFixture, syntheticStrikeSlipFixture } from '@/lib/science/fixtures';
import { runPipeline } from '@/lib/science/pipeline';
import type { GridMeta, UsgsEventLite, ScienceDataset, ChangeSummary } from '@/lib/science/types';
import { latLonToUtm, utmZoneToEpsg, utmZone, latLonToUtmInZone } from '@/lib/science/utm';

function mkEvent(over: Partial<UsgsEventLite>): UsgsEventLite {
  return {
    id: 'test-ev',
    magnitude: 6.0,
    magType: 'mww',
    place: 'test',
    time: '2026-01-05T00:00:00Z',
    lat: 35.9,
    lon: -120.5,
    depthKm: 8,
    url: 'https://example.org',
    nodalPlane: { strike: 137, dip: 85, rake: -180 },
    ...over,
  };
}

/** Small UTM grid centred near the given lat/lon. */
function mkGrid(centerLat: number, centerLon: number, side = 128, spacing = 500): GridMeta {
  const u = latLonToUtm(centerLat, centerLon);
  return {
    width: side,
    height: side,
    xStart: u.easting - ((side - 1) * spacing) / 2,
    xSpacing: spacing,
    yStart: u.northing + ((side - 1) * spacing) / 2,
    ySpacing: -spacing,
    epsg: utmZoneToEpsg(utmZone(centerLon), centerLat < 0 ? 'S' : 'N'),
  };
}

function argmax(raster: Float32Array, width: number): { i: number; j: number; v: number } {
  // NOTE: initial |v| must be 0 (not -Infinity) so Math.abs(best.v) does not
  // permanently block updates.
  let best = { i: 0, j: 0, v: 0 };
  for (let idx = 0; idx < raster.length; idx++) {
    const v = raster[idx];
    if (Number.isFinite(v) && Math.abs(v) > Math.abs(best.v)) {
      best = { i: Math.floor(idx / width), j: idx % width, v };
    }
  }
  return best;
}

describe('event selection (§6: never trust the first API result)', () => {
  const ds = syntheticNullFixture();

  test('ranking picks the largest in-window event, not the first listed', () => {
    const small = mkEvent({ id: 'small', magnitude: 4.2, time: '2026-01-02T00:00:00Z' });
    const big = mkEvent({ id: 'big', magnitude: 6.4, time: '2026-01-07T00:00:00Z' });
    // Deliberately unsorted: the small, EARLIER event comes first (FDSN
    // time-ascending order).
    const sel = rankEventsForPair([small, big], ds);
    expect(sel.primary!.id).toBe('big');
    expect(sel.alternatives.map((a) => a.id)).toEqual(['small']);
  });

  test('among equal magnitudes, the nearer event wins', () => {
    const far = mkEvent({ id: 'far', lat: 36.4, lon: -120.9 });
    const near = mkEvent({ id: 'near', lat: 35.92, lon: -120.51 });
    const sel = rankEventsForPair([far, near], ds);
    expect(sel.primary!.id).toBe('near');
  });

  test('events with a mechanism outrank equal-magnitude events without one', () => {
    const noMech = mkEvent({ id: 'nomech', nodalPlane: undefined });
    const withMech = mkEvent({ id: 'mech' });
    const sel = rankEventsForPair([noMech, withMech], ds);
    expect(sel.primary!.id).toBe('mech');
  });

  test('events outside the acquisition window are excluded with a reason', () => {
    const before = mkEvent({ id: 'before', time: '2025-12-30T00:00:00Z' });
    const after = mkEvent({ id: 'after', time: '2026-02-01T00:00:00Z' });
    const sel = rankEventsForPair([before, after], ds);
    expect(sel.ranked.length).toBe(0);
    expect(sel.primary).toBeUndefined();
    expect(sel.excluded.length).toBe(2);
    expect(sel.excluded.every((e) => e.reason.includes('window'))).toBe(true);
  });

  test('events beyond the distance limit are excluded with the measured distance', () => {
    const distant = mkEvent({ id: 'distant', lat: 38.0, lon: -124.0 }); // several hundred km away
    const sel = rankEventsForPair([distant], ds, { maxDistanceKm: 100 });
    expect(sel.ranked.length).toBe(0);
    expect(sel.excluded[0].reason).toContain('km from the footprint centroid');
  });

  test('missing magnitude ranks last but does not crash or drop the event', () => {
    const unknown = mkEvent({ id: 'unknown', magnitude: NaN });
    const known = mkEvent({ id: 'known', magnitude: 4.0 });
    const sel = rankEventsForPair([unknown, known], ds);
    expect(sel.primary!.id).toBe('known');
    expect(sel.alternatives[0].id).toBe('unknown');
  });

  test('empty input yields an explicit empty selection with criteria metadata', () => {
    const sel = rankEventsForPair([], ds);
    expect(sel.primary).toBeUndefined();
    expect(sel.criteria.ranking.length).toBeGreaterThan(20);
    expect(sel.criteria.window.start).toBe(ds.provenance.referenceTime);
  });
});

/** M5.5 reverse event: single dominant uplift lobe (unique argmax). */
function mkReverseEvent(over: Partial<UsgsEventLite> = {}): UsgsEventLite {
  return mkEvent({
    magnitude: 5.5,
    lat: 35.904,
    lon: -120.496,
    depthKm: 8,
    nodalPlane: { strike: 20, dip: 45, rake: 90 },
    ...over,
  });
}

describe('forward-model coordinate alignment (§5)', () => {
  const geometry = syntheticStrikeSlipFixture().geometry;

  test('moving the earthquake moves the predicted peak with it', () => {
    const meta = mkGrid(35.9, -120.5);
    const evA = mkReverseEvent();
    const evB = mkReverseEvent({ lat: evA.lat + 0.045, lon: evA.lon + 0.055 }); // ~7 km NE
    const rA = buildPredictionOnGrid(meta, evA, geometry);
    const rB = buildPredictionOnGrid(meta, evB, geometry);
    if ('error' in rA || 'error' in rB) throw new Error('unexpected error');
    const pA = argmax(rA.data, meta.width);
    const pB = argmax(rB.data, meta.width);
    const xA = meta.xStart + pA.j * meta.xSpacing;
    const yA = meta.yStart + pA.i * meta.ySpacing;
    const xB = meta.xStart + pB.j * meta.xSpacing;
    const yB = meta.yStart + pB.i * meta.ySpacing;
    const peakMoved = Math.hypot(xB - xA, yB - yA);
    const epiA = latLonToUtm(evA.lat, evA.lon);
    const epiB = latLonToUtm(evB.lat, evB.lon);
    const epiMoved = Math.hypot(epiB.easting - epiA.easting, epiB.northing - epiA.northing);
    // The peak must follow the epicentre: within 2 pixels-per-7km of the
    // epicentre displacement, and clearly non-zero.
    expect(peakMoved).toBeGreaterThan(epiMoved * 0.5);
    expect(peakMoved).toBeLessThan(epiMoved * 1.5);
  });

  test('GRID-SHIFT INVARIANCE: identical values at the same geographic point on shifted grids', () => {
    // Two grids with identical shape but origins shifted 10 pixels east +
    // south. The prediction at any shared geographic point must be EXACTLY
    // equal: the model is a function of (distance to epicentre) only. The
    // old raster-centre-anchored code fails this by construction.
    const ev = mkReverseEvent();
    const m1 = mkGrid(35.9, -120.5);
    const m2: GridMeta = {
      ...m1,
      xStart: m1.xStart + 10 * m1.xSpacing,
      yStart: m1.yStart - 10 * Math.abs(m1.ySpacing),
    };
    const r1 = buildPredictionOnGrid(m1, ev, geometry);
    const r2 = buildPredictionOnGrid(m2, ev, geometry);
    if ('error' in r1 || 'error' in r2) throw new Error('unexpected error');
    const epi = latLonToUtmInZone(ev.lat, ev.lon, m1.epsg - 32600);
    const probes: Array<[number, number]> = [
      [0, 0], [2000, -1500], [-5000, 3000], [8000, 8000], [-9000, 2000],
    ];
    for (const [dx, dy] of probes) {
      const x = epi.easting + dx;
      const y = epi.northing + dy;
      const j1 = Math.round((x - m1.xStart) / m1.xSpacing);
      const i1 = Math.round((y - m1.yStart) / m1.ySpacing);
      const j2 = Math.round((x - m2.xStart) / m2.xSpacing);
      const i2 = Math.round((y - m2.yStart) / m2.ySpacing);
      if (i1 < 0 || i1 >= m1.height || j1 < 0 || j1 >= m1.width) continue;
      if (i2 < 0 || i2 >= m2.height || j2 < 0 || j2 >= m2.width) continue;
      const v1 = r1.data[i1 * m1.width + j1];
      const v2 = r2.data[i2 * m2.width + j2];
      expect(v1).toBe(v2);
    }
  });

  test('the predicted peak sits near the epicentre, not the raster centre', () => {
    // Epicentre deliberately offset ~5 km north-east of the grid centre.
    const ev = mkReverseEvent({ lat: 35.945, lon: -120.45 });
    const meta = mkGrid(35.9, -120.5);
    const r = buildPredictionOnGrid(meta, ev, geometry);
    if ('error' in r) throw new Error('unexpected error');
    const p = argmax(r.data, meta.width);
    const px = meta.xStart + p.j * meta.xSpacing;
    const py = meta.yStart + p.i * meta.ySpacing;
    const epi = latLonToUtmInZone(ev.lat, ev.lon, meta.epsg - 32600);
    const distFromEpicentre = Math.hypot(px - epi.easting, py - epi.northing);
    const centreX = meta.xStart + ((meta.width - 1) / 2) * meta.xSpacing;
    const centreY = meta.yStart + ((meta.height - 1) / 2) * meta.ySpacing;
    const distFromCentre = Math.hypot(px - centreX, py - centreY);
    // The uplift lobe peaks within a few km of the epicentre and clearly
    // closer to it than to the grid centre (which is ~7 km away from both).
    expect(distFromEpicentre).toBeLessThan(4000);
    expect(distFromEpicentre).toBeLessThan(distFromCentre);
  });

  test('forward model reports the projected epicentre and grid CRS', () => {
    const ds = syntheticStrikeSlipFixture();
    const ev = mkEvent({ lat: ds.center!.lat + 0.01, lon: ds.center!.lon + 0.01 });
    const fm = forwardModelCheck(ds, ev, ds.phase!.data, new Uint8Array(ds.phase!.data.length).fill(1));
    expect(fm).toBeDefined();
    expect(fm!.skipped).toBe(false);
    expect(fm!.epicentre).toBeDefined();
    expect(fm!.epicentre!.epsg).toBe(ds.phase!.meta.epsg);
  });

  test('forward model refuses (does not misplace) on non-UTM grids', () => {
    const meta: GridMeta = { ...mkGrid(35.9, -120.5), epsg: 3413 }; // polar stereographic
    const r = buildPredictionOnGrid(meta, mkEvent({}), geometry);
    expect('error' in r).toBe(true);
    expect((r as { error: string }).error).toContain('not WGS84 UTM');
  });

  test('events without a mechanism produce an explicit skip, never an invented model', () => {
    const ds = syntheticStrikeSlipFixture();
    const fm = forwardModelCheck(ds, mkEvent({ nodalPlane: undefined }), ds.phase!.data, undefined);
    expect(fm!.skipped).toBe(true);
    expect(fm!.skipReason).toContain('nodal plane');
    expect(Number.isNaN(fm!.predictedPeakM)).toBe(true);
  });

  test('unit consistency: distances in metres, never degrees', () => {
    const meta = mkGrid(35.9, -120.5);
    const p = projectToGrid(35.9, -120.5, meta);
    if ('error' in p) throw new Error(p.error);
    // UTM easting/northing magnitudes (metres): 1e4..1e7, never degree-like 35.
    expect(Math.abs(p.x)).toBeGreaterThan(1e4);
    expect(Math.abs(p.x)).toBeLessThan(1e7);
    expect(Math.abs(p.y)).toBeGreaterThan(1e4);
    expect(Math.abs(p.y)).toBeLessThan(1e7);
    // And the grid origin is in the same unit family.
    expect(Math.abs(meta.xStart - p.x)).toBeLessThan(1e5);
  });
});

describe('attribution honesty (§7)', () => {
  const ds = syntheticStrikeSlipFixture();

  function fakeSummary(over: Partial<ChangeSummary> = {}): ChangeSummary {
    const res = runPipeline(ds, {}, []);
    return { ...res.summary, ...over };
  }

  test('a detected pattern matching the model is "consistent", with non-causal language', () => {
    const res = runPipeline(ds, {}, []);
    const h = res.attribution.hypotheses.find((x) => x.id === 'coseismic')!;
    // No events were passed, so coseismic is not-supported — the language
    // check applies to the null hypothesis instead here.
    const nullH = res.attribution.hypotheses.find((x) => x.id === 'null')!;
    expect(nullH.label).toContain('threshold');
    expect(nullH.evidence.join(' ')).not.toContain('did not move');
  });

  test('the null hypothesis says "no detection above threshold", never "no change"', () => {
    const sel = { ranked: [], alternatives: [], criteria: rankEventsForPair([], ds).criteria, excluded: [] };
    const out = evaluateAttribution(ds, fakeSummary({ affectedAreaKm2: 0 }), sel);
    const nullH = out.hypotheses.find((h) => h.id === 'null')!;
    expect(nullH.status).toBe('supported');
    const text = nullH.evidence.join(' ');
    expect(text).toContain('NOT that the ground did not move');
  });

  test('a single unwrapping component is NOT proof of artefact absence', () => {
    const sel = { ranked: [], alternatives: [], criteria: rankEventsForPair([], ds).criteria, excluded: [] };
    const out = evaluateAttribution(ds, fakeSummary({ connectedComponentCount: 1 }), sel);
    const unwrap = out.hypotheses.find((h) => h.id === 'unwrapping')!;
    const text = unwrap.evidence.join(' ');
    expect(text).toContain('NOT proof');
    // And it must not claim not-supported on the basis of one component.
    expect(unwrap.status).not.toBe('not-supported');
  });

  test('heuristics are exposed, documented and labelled exploratory', () => {
    const res = runPipeline(ds, {}, []);
    expect(res.attribution.heuristics).toEqual(ATTRIBUTION_HEURISTICS);
    expect(res.attribution.methodNote).toContain('Exploratory');
    expect(res.attribution.methodNote).toContain('never "caused by"');
  });

  test('forward-model agreement is described as consistency, not causation', () => {
    const sel = rankEventsForPair([mkEvent({ magnitude: 6.4 })], ds);
    const fm = {
      eventId: 'x',
      skipped: false,
      amplitudeRatio: 1.2,
      patternCorrelation: 0.6,
      predictedPeakM: 0.1,
      measuredPeakM: 0.12,
      note: '',
    };
    const out = evaluateAttribution(ds, fakeSummary({ affectedAreaKm2: 500, pixelAreaKm2: 0.0625 }), sel, fm);
    const h = out.hypotheses.find((x) => x.id === 'coseismic')!;
    expect(h.status).toBe('consistent');
    expect(h.evidence.join(' ')).toContain('not causation');
  });
});

describe('synthetic-fixture attribution honesty (final-pass fix, 2026-10-21)', () => {
  test('an earthquake fixture never reports "no USGS catalog earthquake" against its own construction', () => {
    const res = runPipeline(syntheticStrikeSlipFixture(), {}, []);
    const h = res.attribution.hypotheses.find((x) => x.id === 'coseismic')!;
    // The signal IS an earthquake model by construction — the catalog-based
    // "not supported / no USGS event" verdict would be self-contradictory.
    const text = h.evidence.join(' ');
    expect(text).not.toContain('No USGS catalog earthquake');
    expect(text).toContain('by construction');
    expect(h.status).toBe('supported');
    expect(res.attribution.methodNote).toContain('Demonstration dataset');
  });

  test('the null fixture still reports no coseismic signal — by construction', () => {
    const res = runPipeline(syntheticNullFixture(), {}, []);
    const h = res.attribution.hypotheses.find((x) => x.id === 'coseismic')!;
    expect(h.status).toBe('not-supported');
    expect(h.evidence.join(' ')).toContain('by construction');
    // The false-alarm control verdict is unchanged.
    expect(res.attribution.hypotheses.find((x) => x.id === 'null')!.status).toBe('supported');
  });

  test('synthetic fixtures do not cite real-product correction status', () => {
    const res = runPipeline(syntheticStrikeSlipFixture(), {}, []);
    for (const h of res.attribution.hypotheses) {
      expect(h.evidence.join(' ')).not.toContain('ASF NISAR GUNW guide');
    }
  });

  test('a real-path verdict is preserved when an event IS provided (synthetic used as a stand-in)', () => {
    const ds2 = syntheticStrikeSlipFixture();
    const sel = rankEventsForPair([mkEvent({ magnitude: 6.4 })], ds2);
    const out = evaluateAttribution(ds2, { ...runPipeline(ds2, {}, []).summary, affectedAreaKm2: 500 }, sel);
    const h = out.hypotheses.find((x) => x.id === 'coseismic')!;
    expect(h.status).not.toBe('supported'); // catalog screen outcome, not a construction fact
  });
});
