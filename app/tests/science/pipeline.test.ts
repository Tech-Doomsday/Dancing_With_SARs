import { describe, expect, test } from 'bun:test';
import { latLonToUtm, utmToLatLon, epsgToLatLon, utmZone, epsgForLatLon } from '@/lib/science/utm';
import { validateDataset } from '@/lib/science/validation';
import { runPipeline } from '@/lib/science/pipeline';
import { getDataset, getDatasetAvailability } from '@/lib/data/datasets';
import { syntheticStrikeSlipFixture, syntheticNullFixture, mulberry32 } from '@/lib/science/fixtures';
import type { ScienceDataset } from '@/lib/science/types';

// Real-data tests are opt-in by availability: they run when the local derived
// products are present (they are committed to this repository) and skip with
// an explicit message when the scientific data has not been obtained — the
// default offline workflow never depends on them.
const LA_AVAILABLE = getDatasetAvailability('real-nisar-la-2008').state === 'available';

describe('UTM conversion (Snyder 1987)', () => {
  test('zone determination', () => {
    expect(utmZone(-118.2)).toBe(11);
    expect(utmZone(-80.77)).toBe(17);
    expect(utmZone(0)).toBe(31);
    expect(utmZone(-0.1)).toBe(30);
  });

  test('round trip: lat/lon -> UTM -> lat/lon within ~1 m', () => {
    const cases: Array<[number, number]> = [
      [34.32, -118.23], // LA region (NISAR sample footprint)
      [7.5868, -80.769], // Panama epicentre
      [35.9, -120.5], // Parkfield
      [-33.87, 151.21], // Sydney (southern hemisphere)
      [60.0, 5.0], // Bergen
    ];
    for (const [lat, lon] of cases) {
      const u = latLonToUtm(lat, lon);
      const back = utmToLatLon(u.easting, u.northing, u.zone, lat < 0);
      const dLat = Math.abs(back.lat - lat) * 111320;
      const dLon = Math.abs(back.lon - lon) * 111320 * Math.cos((lat * Math.PI) / 180);
      expect(dLat).toBeLessThan(1.0);
      expect(dLon).toBeLessThan(1.0);
    }
  });

  test('NISAR sample grid corner inverse-projection falls inside the recorded footprint bbox', () => {
    // Sample GUNW: EPSG 32611, xStart 365520, yStart 3913580 (northern edge).
    const ll = epsgToLatLon(365520, 3913580, 32611);
    // The product's bounding polygon starts near (-118.23, 34.32) and the
    // grid spans roughly lon -118.24..-117.24, lat 34.21..35.35.
    expect(ll.lon).toBeGreaterThan(-118.6);
    expect(ll.lon).toBeLessThan(-117.9);
    expect(ll.lat).toBeGreaterThan(35.0);
    expect(ll.lat).toBeLessThan(35.7);
  });

  test('epsgForLatLon', () => {
    expect(epsgForLatLon(34.3, -118.2)).toBe(32611);
    expect(epsgForLatLon(7.6, -80.8)).toBe(32617);
    expect(epsgForLatLon(-33.9, 151.2)).toBe(32756);
  });
});

describe('Stage A input validation', () => {
  const mk = (over: Partial<ScienceDataset>): ScienceDataset => {
    const base = syntheticNullFixture();
    return { ...base, ...over };
  };

  test('accepts a well-formed synthetic dataset', () => {
    const v = validateDataset(syntheticNullFixture());
    expect(v.ok).toBe(true);
  });

  test('rejects mismatched grid shapes', () => {
    const ds = mk({});
    const bad = {
      ...ds,
      coherence: {
        meta: { ...ds.coherence!.meta, width: ds.coherence!.meta.width + 1 },
        data: ds.coherence!.data,
      },
    };
    const v = validateDataset(bad);
    expect(v.ok).toBe(false);
    expect(v.errors.join(' ')).toContain('different shapes');
  });

  test('rejects invalid temporal order', () => {
    const ds = mk({});
    const p = ds.provenance;
    const v = validateDataset({
      ...ds,
      provenance: { ...p, referenceTime: '2026-02-01T00:00:00Z', secondaryTime: '2026-01-01T00:00:00Z' },
    });
    expect(v.ok).toBe(false);
    expect(v.errors.join(' ')).toContain('after');
  });

  test('rejects implausible wavelength', () => {
    const ds = mk({});
    const v = validateDataset({ ...ds, geometry: { ...ds.geometry, wavelength: 42 } });
    expect(v.ok).toBe(false);
  });
});

describe('pipeline integration', () => {
  test('null fixture produces no detection (false-alarm control)', () => {
    const res = runPipeline(syntheticNullFixture(), {}, []);
    expect(res.summary.affectedAreaKm2).toBe(0);
    expect(res.attribution.hypotheses.find((h) => h.id === 'null')!.status).toBe('supported');
    expect(res.validation).toBeDefined();
    expect(res.validation!.rmseM).toBeLessThan(0.01);
  });

  test('strike-slip fixture is recovered against analytic ground truth', () => {
    const res = runPipeline(syntheticStrikeSlipFixture(), {}, []);
    expect(res.summary.affectedAreaKm2).toBeGreaterThan(100);
    expect(res.validation!.maeM).toBeLessThan(0.01); // MAE < 10 mm
    expect(res.validation!.rmseM).toBeLessThan(0.015);
    expect(Math.abs(res.validation!.biasM)).toBeLessThan(0.01);
    // Transport grid is well-formed.
    expect(res.transport.width).toBeGreaterThan(100);
    expect(res.transport.displacement.length).toBeGreaterThan(1000);
    // Detected-change transport grid (2026-10-24): present, sized to the
    // transport resolution, and strictly a subset of the valid mask.
    expect(res.transport.detected).toBeDefined();
    const buf = Buffer.from(res.transport.detected!, 'base64');
    const det = new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
    expect(det.length).toBe(res.transport.width * res.transport.height);
    const maskBuf = Buffer.from(res.transport.mask, 'base64');
    const mask = new Uint8Array(maskBuf.buffer, maskBuf.byteOffset, maskBuf.byteLength);
    let detectedCells = 0;
    for (let i = 0; i < det.length; i++) {
      if (det[i]) {
        detectedCells++;
        expect(mask[i]).toBe(1); // detected ⊆ valid
      }
    }
    expect(detectedCells).toBeGreaterThan(0); // the strike-slip fixture IS detected
  });

  // Data-dependent: skips with an explicit message when the derived LA
  // product is absent (fresh clone without data).
  test.skipIf(!LA_AVAILABLE)('real NISAR LA sample runs and reports honestly [requires local data]', () => {
    const ds = getDataset('real-nisar-la-2008')!;
    expect(ds).toBeDefined();
    const res = runPipeline(ds, {}, []);
    // The ionospheric systematic floor must be reported (this product has ~4.3 rad screen uncertainty).
    expect(res.summary.systematicIonoFloorM).toBeGreaterThan(0.05);
    expect(res.summary.validFraction).toBeGreaterThan(0.05);
    expect(res.summary.coherenceMedian).toBeGreaterThan(0.4);
    expect(res.narrative.reliability).toContain('ionospheric');
    // The detected-change transport grid exists on the real product too.
    expect(res.transport.detected).toBeDefined();
  });

  test.skipIf(!LA_AVAILABLE)('coherence threshold rejection surfaces a clear error [requires local data]', () => {
    const ds = getDataset('real-nisar-la-2008')!;
    expect(() => runPipeline(ds, { coherenceThreshold: 0.97 }, [])).toThrow(/coherence threshold/);
  });

  test('fixtures are deterministic', () => {
    const a = mulberry32(42);
    const b = mulberry32(42);
    for (let i = 0; i < 10; i++) expect(a()).toBe(b());
    const d1 = syntheticStrikeSlipFixture();
    const d2 = syntheticStrikeSlipFixture();
    expect(d1.phase!.data[1000]).toBe(d2.phase!.data[1000]);
    expect(d1.truth!.data[5000]).toBe(d2.truth!.data[5000]);
  });
});
