/**
 * NOAA DART deep-ocean bottom-pressure records — client.
 *
 * Two real access paths (both verified 2026-10-24):
 *  - Historical archive: https://www.ndbc.noaa.gov/data/historical/dart/{ID}t{YEAR}.txt.gz
 *    plain "#YY MM DD hh mm ss T HEIGHT" rows, HEIGHT = bottom pressure expressed
 *    as equivalent sea-surface height in metres; 9999.0 = missing. 15-minute
 *    cadence in monitor mode, 1-minute (and 15-s in .dart) in event mode.
 *  - Realtime: https://www.ndbc.noaa.gov/data/realtime2/{ID}.dart — same format,
 *    ≈45-day lookback, 45 stations currently reporting.
 *
 * Station inventory: curated from the NDBC station pages (coordinates are the
 * CURRENT deployment positions; DART buoys are redeployed over the years — a
 * documented limitation when comparing historical events).
 *
 * Nothing here fabricates data: parse failures, gaps and HTTP errors are
 * surfaced as explicit availability states.
 */

import { promises as fs } from 'fs';
import path from 'path';
import { gunzipSync } from 'zlib';
import type { DartStation, TimeSeries, TsunamiProvenance } from './types';

export const DART_STATIONS: DartStation[] = [
  { id: '21401', lat: 42.617, lon: 152.583, name: 'SE of Iturup Island', region: 'NW Pacific' },
  { id: '21413', lat: 30.487, lon: 152.124, name: 'SE of Tokyo', region: 'NW Pacific' },
  { id: '21414', lat: 48.977, lon: 178.199, name: 'S of Amchitka', region: 'N Pacific' },
  { id: '21415', lat: 50.120, lon: 171.867, name: 'S of Attu', region: 'N Pacific' },
  { id: '21416', lat: 48.126, lon: 163.355, name: 'SE of Kamchatka', region: 'N Pacific' },
  { id: '21418', lat: 38.840, lon: 148.753, name: 'NE of Tokyo', region: 'NW Pacific' },
  { id: '21419', lat: 44.401, lon: 155.653, name: 'SE of Kuril Islands', region: 'NW Pacific' },
  { id: '21420', lat: 28.980, lon: 134.973, name: 'SE of Miyazaki', region: 'NW Pacific' },
  { id: '32401', lat: -20.611, lon: -73.394, name: 'W of Iquique, Chile', region: 'SE Pacific' },
  { id: '32402', lat: -26.743, lon: -73.983, name: 'W of Caldera, Chile', region: 'SE Pacific' },
  { id: '32403', lat: -23.163, lon: -72.037, name: 'W of Antofagasta, Chile', region: 'SE Pacific' },
  { id: '32404', lat: -32.130, lon: -73.797, name: 'NW of Valparaíso, Chile', region: 'SE Pacific' },
  { id: '32411', lat: 4.979, lon: -90.793, name: 'WSW of Panama City', region: 'E Pacific' },
  { id: '32412', lat: -17.984, lon: -86.374, name: 'SW of Lima', region: 'SE Pacific' },
  { id: '32413', lat: -7.432, lon: -93.464, name: 'WNW of Lima', region: 'SE Pacific' },
  { id: '43412', lat: 16.020, lon: -107.000, name: 'SW of Manzanillo', region: 'E Pacific' },
  { id: '43413', lat: 10.927, lon: -100.012, name: 'S of Acapulco', region: 'E Pacific' },
  { id: '46402', lat: 50.913, lon: -164.147, name: 'SSE of Dutch Harbor', region: 'Gulf of Alaska' },
  { id: '46404', lat: 45.852, lon: -128.745, name: 'W of Astoria', region: 'US West Coast' },
  { id: '51407', lat: 19.530, lon: -156.601, name: 'W of Kailua-Kona', region: 'Hawaii' },
  { id: '52402', lat: 11.928, lon: 153.876, name: 'ESE of Saipan', region: 'W Pacific' },
  { id: '52406', lat: -5.374, lon: 164.990, name: 'NE of Guadalcanal', region: 'W Pacific' },
];

export function stationById(id: string): DartStation | undefined {
  return DART_STATIONS.find((s) => s.id === id);
}

/** Great-circle distance in km (spherical earth, R = 6371.0088 km). */
export function distanceKm(aLat: number, aLon: number, bLat: number, bLon: number): number {
  const R = 6371.0088;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(bLat - aLat);
  const dLon = toRad(bLon - aLon);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(aLat)) * Math.cos(toRad(bLat)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

/** Stations within `maxKm` of a point, nearest first. */
export function stationsWithin(lat: number, lon: number, maxKm: number): Array<DartStation & { distKm: number }> {
  return DART_STATIONS.map((s) => ({ ...s, distKm: distanceKm(lat, lon, s.lat, s.lon) }))
    .filter((s) => s.distKm <= maxKm)
    .sort((a, b) => a.distKm - b.distKm);
}

/**
 * Parse the NDBC DART text format (both archive .txt and realtime .dart).
 * Rows: "YYYY MM DD hh mm ss T HEIGHT". Missing HEIGHT = 9999.0.
 * Returns samples ascending in time with missing values removed.
 */
export function parseDartText(text: string): TimeSeries {
  const tArr: number[] = [];
  const vArr: number[] = [];
  for (const raw of text.split('\n')) {
    const l = raw.trim();
    if (!/^\d{4}\s/.test(l)) continue;
    const p = l.split(/\s+/);
    if (p.length < 8) continue;
    const yy = +p[0], mm = +p[1], dd = +p[2], hh = +p[3], mi = +p[4], ss = +p[5];
    const v = parseFloat(p[7]);
    if (!Number.isFinite(v) || v >= 9000) continue; // 9999 = missing
    const t = Date.UTC(yy, mm - 1, dd, hh, mi, ss);
    if (!Number.isFinite(t)) continue;
    tArr.push(t);
    vArr.push(v);
  }
  // Realtime files arrive NEWEST-FIRST (verified 2026-10-24); the historical
  // archive is oldest-first. Sort ascending so downstream windowing works.
  const order = tArr.map((t, i) => [t, i] as const).sort((a, b) => a[0] - b[0]);
  return {
    t: Float64Array.from(order, ([t]) => t),
    v: Float32Array.from(order, ([, i]) => vArr[i]),
  };
}

/* ---------------- caching ---------------- */

function dartCacheDir(): string {
  return path.join(process.cwd(), 'data', 'cache', 'dart');
}

interface CachedSeries {
  stationId: string;
  kind: 'historical' | 'realtime';
  provenance: TsunamiProvenance;
  t: number[];
  v: number[];
}

async function readCache(stationId: string, kind: string, maxAgeMs: number, year?: number): Promise<TimeSeries & { provenance: TsunamiProvenance } | null> {
  try {
    const p = path.join(dartCacheDir(), year ? `${stationId}.${year}.${kind}.json` : `${stationId}.${kind}.json`);
    const st = await fs.stat(p);
    if (Date.now() - st.mtimeMs > maxAgeMs) return null;
    const c = JSON.parse(await fs.readFile(p, 'utf-8')) as CachedSeries;
    return {
      t: Float64Array.from(c.t),
      v: Float32Array.from(c.v),
      provenance: c.provenance,
    };
  } catch {
    return null;
  }
}

async function writeCache(entry: CachedSeries, year?: number): Promise<void> {
  try {
    await fs.mkdir(dartCacheDir(), { recursive: true });
    const name = year ? `${entry.stationId}.${year}.${entry.kind}.json` : `${entry.stationId}.${entry.kind}.json`;
    await fs.writeFile(path.join(dartCacheDir(), name), JSON.stringify(entry));
  } catch {
    // non-fatal
  }
}

async function fetchBinary(url: string, timeoutMs: number): Promise<Uint8Array> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) throw new Error(`NDBC returned HTTP ${res.status} for ${url}`);
    return new Uint8Array(await res.arrayBuffer());
  } finally {
    clearTimeout(timer);
  }
}

function provenanceFor(
  station: DartStation,
  kind: 'historical' | 'realtime',
  url: string,
  series: TimeSeries,
): TsunamiProvenance {
  const first = series.t.length ? new Date(series.t[0]).toISOString() : 'n/a';
  const last = series.t.length ? new Date(series.t.at(-1)!).toISOString() : 'n/a';
  return {
    datasetId: `dart-${station.id}-${kind}`,
    provider: 'NOAA National Data Buoy Center',
    productId: `DART ${station.id} (${station.name})`,
    productType: 'Deep-ocean bottom-pressure recorder (15-min monitor / 1-min event mode)',
    sourceUrl: url,
    retrievedAt: new Date().toISOString(),
    observationStart: first,
    observationEnd: last,
    units: 'bottom pressure as equivalent sea-surface height, metres',
    crs: `station position ${station.lat.toFixed(3)}, ${station.lon.toFixed(3)} (WGS84)`,
    verticalDatum: 'pressure-derived height; referenced to the record mean (de-tided in processing)',
    processing: [
      'downloaded raw text (gzip for archive files)',
      'removed rows with HEIGHT = 9999.0 (missing)',
      'sorted ascending by time',
    ],
    dataClass: 'measured',
    qualityNotes: [
      'station coordinates are the CURRENT deployment position (buoys are redeployed over the years)',
      'the pressure record can carry a seismic-phase excursion minutes after a large nearby earthquake — detection treats pre-origin excursions separately',
    ],
    license: 'public domain (U.S. Government work)',
  };
}

export interface DartFetchResult {
  series: TimeSeries;
  provenance: TsunamiProvenance;
  kind: 'historical' | 'realtime';
}

/**
 * Fetch the DART record for one station covering the event at `eventMs`:
 * the realtime file (~45-day lookback) when the event is recent, otherwise
 * the historical yearly archive. Cached on disk: historical permanently,
 * realtime for 3 hours. (The gate compares against the EVENT time — an
 * earlier version compared against the year start and wrongly skipped the
 * realtime file for events older than ~60 days into the year.)
 */
export async function getDartSeries(stationId: string, eventMs: number): Promise<DartFetchResult> {
  const station = stationById(stationId);
  if (!station) throw new Error(`Unknown DART station ${stationId}`);

  const now = Date.now();
  const year = new Date(eventMs).getUTCFullYear();
  const realtimeCovers = now - eventMs < 50 * 86_400_000;

  const histUrl = `https://www.ndbc.noaa.gov/data/historical/dart/${stationId}t${year}.txt.gz`;
  const rtUrl = `https://www.ndbc.noaa.gov/data/realtime2/${stationId}.dart`;

  if (realtimeCovers) {
    const cachedRt = await readCache(stationId, 'realtime', 3 * 3600_000);
    if (cachedRt) return { series: { t: cachedRt.t, v: cachedRt.v }, provenance: cachedRt.provenance, kind: 'realtime' };
    try {
      const buf = await fetchBinary(rtUrl, 60_000);
      const text = new TextDecoder().decode(buf);
      const series = parseDartText(text);
      if (series.t.length === 0) throw new Error('realtime file parsed to 0 samples');
      const prov = provenanceFor(station, 'realtime', rtUrl, series);
      await writeCache({ stationId, kind: 'realtime', provenance: prov, t: Array.from(series.t), v: Array.from(series.v) });
      return { series, provenance: prov, kind: 'realtime' };
    } catch (err) {
      // fall through to historical for the same year (older events)
      void err;
    }
  }

  const cachedHist = await readCache(stationId, 'historical', Infinity, year);
  if (cachedHist) return { series: { t: cachedHist.t, v: cachedHist.v }, provenance: cachedHist.provenance, kind: 'historical' };

  const buf = await fetchBinary(histUrl, 120_000);
  const text = new TextDecoder().decode(gunzipSync(Buffer.from(buf)));
  const series = parseDartText(text);
  if (series.t.length === 0) throw new Error(`DART ${stationId} ${year}: archive file parsed to 0 samples`);
  const prov = provenanceFor(station, 'historical', histUrl, series);
  await writeCache({ stationId, kind: 'historical', provenance: prov, t: Array.from(series.t), v: Array.from(series.v) }, year);
  return { series, provenance: prov, kind: 'historical' };
}
