import { describe, expect, test, beforeAll, afterAll } from 'bun:test';
import { getEtopoRegion } from '@/lib/tsunami/etopo';
import { getDartSeries } from '@/lib/tsunami/dart';
import { stationsWithin, distanceKm } from '@/lib/tsunami/dart';

/**
 * Failure-path handling: data clients must surface structured errors and
 * the honest availability states — never fabricate data or swallow errors.
 */

const realFetch = globalThis.fetch;

beforeAll(() => {
  // ETOPO cache must be bypassed for the failure test: point the cache at a
  // temp key by using a unique region (no on-disk cache exists for it).
});

afterAll(() => {
  globalThis.fetch = realFetch;
});

describe('etopo client failure handling', () => {
  test('HTTP 500 from THREDDS → structured error, no fake bathymetry', async () => {
    globalThis.fetch = (async () =>
      new Response('Internal Server Error', { status: 500 })) as unknown as typeof fetch;
    await expect(
      getEtopoRegion({ bbox: [123.456, 12.345, 124.567, 13.456], resArcmin: 4 }),
    ).rejects.toThrow(/HTTP 500/);
  });

  test('network abort/timeout → error propagates', async () => {
    globalThis.fetch = (async () => {
      throw new Error('simulated network failure');
    }) as unknown as typeof fetch;
    await expect(
      getEtopoRegion({ bbox: [111.111, 11.111, 112.222, 12.222], resArcmin: 4 }),
    ).rejects.toThrow(/simulated network failure/);
  });

  test('invalid stride rejected before any network call', async () => {
    globalThis.fetch = realFetch;
    await expect(getEtopoRegion({ bbox: [0, 0, 1, 1], resArcmin: 3 })).rejects.toThrow(/resArcmin/);
  });
});

describe('dart client failure handling', () => {
  test('unknown station id → explicit error', async () => {
    globalThis.fetch = realFetch;
    await expect(getDartSeries('99999', 2011)).rejects.toThrow(/Unknown DART station/);
  });

  test('HTTP 404 for a historical station-year → error with the URL, availability handled upstream', async () => {
    globalThis.fetch = (async () => new Response('Not Found', { status: 404 })) as unknown as typeof fetch;
    // a 1999 EVENT timestamp: far outside the realtime window, so the client
    // must go to the historical archive (which the mock 404s)
    await expect(getDartSeries('21418', Date.UTC(1999, 0, 1))).rejects.toThrow(/HTTP 404/);
    globalThis.fetch = realFetch;
  });
});

describe('station geometry helpers', () => {
  test('distanceKm sanity: equator 1° ≈ 111.19 km', () => {
    const d = distanceKm(0, 0, 0, 1);
    expect(Math.abs(d - 111.19)).toBeLessThan(0.5);
  });

  test('stationsWithin sorts by distance and respects the radius', () => {
    const near = stationsWithin(38.3, 142.37, 1200); // Tōhoku epicentre
    expect(near.length).toBeGreaterThan(0);
    expect(near[0].id).toBe('21418');
    for (let i = 1; i < near.length; i++) {
      expect(near[i].distKm).toBeGreaterThanOrEqual(near[i - 1].distKm);
    }
    expect(near.every((s) => s.distKm <= 1200)).toBe(true);
  });
});
