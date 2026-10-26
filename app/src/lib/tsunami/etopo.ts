/**
 * ETOPO1 bathymetry/topography client via NCEI THREDDS OPeNDAP.
 *
 * Source (verified 2026-10-24):
 *   https://www.ngdc.noaa.gov/thredds/dodsC/global/ETOPO1_Bed_g_gmt4.nc
 *   "ETOPO1_Bed_g_gmt4.nc" — ETOPO1 Ice-surface... (Bedrock variant),
 *   1 arc-minute global relief, cell-centred at exact arc-minute
 *   coordinates: z[lat=0..10800][lon=0..21600], lat[0] = -90, lon[0] = -180.
 *   Vertical datum: metres, positive up, ~MSL (product documentation).
 *
 * Subsetting through the .ascii interface returns a text payload
 * (verified: 8°×6° at 1' ≈ 1.5 MB in ≈4 s).
 *
 * Caching: every fetched region is stored under data/cache/etopo/ as
 *   <key>.json        — grid metadata + provenance
 *   <key>.f32         — raw Float32Array bed elevations (row-major)
 * so simulations are reproducible without the network.
 */

import { promises as fs } from 'fs';
import path from 'path';
import type { BathymetryGrid, TsunamiProvenance } from './types';

const OPeNDAP_BASE = 'https://www.ngdc.noaa.gov/thredds/dodsC/global/ETOPO1_Bed_g_gmt4.nc.ascii';

/** ETOPO1 grid geometry (verified empirically against the MAPS vectors). */
const LAT_ROWS = 10801; // -90 .. +90 at 1'
const LON_COLS = 21601; // -180 .. +180 at 1'

export interface EtopoRegionRequest {
  /** [minLon, minLat, maxLon, maxLat] in degrees */
  bbox: [number, number, number, number];
  /** cells per ETOPO1 sample: 1, 2 or 4 arc-minutes */
  resArcmin: number;
}

function latRow(lat: number): number {
  return Math.round((lat + 90) * 60);
}
function lonCol(lon: number): number {
  return Math.round((lon + 180) * 60);
}

/** Snap a bbox to the resArcmin lattice so cached regions are reusable. */
function snapBbox(bbox: [number, number, number, number], res: number): [number, number, number, number] {
  const step = res / 60;
  const snapDown = (v: number) => Math.floor(v / step) * step;
  const snapUp = (v: number) => Math.ceil(v / step) * step;
  return [snapDown(bbox[0]), snapDown(bbox[1]), snapUp(bbox[2]), snapUp(bbox[3])];
}

function cacheKey(bbox: [number, number, number, number], res: number): string {
  const b = snapBbox(bbox, res);
  return `etopo1_bed_${b[1].toFixed(3)}N${b[0].toFixed(3)}E_${b[3].toFixed(3)}N${b[2].toFixed(3)}E_${res}am`;
}

function cacheDir(): string {
  return path.join(process.cwd(), 'data', 'cache', 'etopo');
}

/**
 * Parse the OPeNDAP ascii response for a 2-D z[lat][lon] subset request.
 * Format (verified):
 *   z.z[ny][nx]
 *   [0], v, v, v, ...
 *   [1], v, v, v, ...
 *   z.lat[ny] = v, v, ...   z.lon[nx] = v, v, ...
 */
export function parseOpendapAscii(text: string): { z: Float32Array; lat: Float64Array; lon: Float64Array; ny: number; nx: number } {
  const lines = text.split('\n');
  let ny = 0;
  let nx = 0;
  for (const l of lines) {
    const m = /^z\.z\[(\d+)\]\[(\d+)\]/.exec(l.trim());
    if (m) {
      ny = Number(m[1]);
      nx = Number(m[2]);
      break;
    }
  }
  if (!ny || !nx) throw new Error('ETOPO1 response missing z.z[ny][nx] header');
  const z = new Float32Array(ny * nx);
  let row = -1;
  let col = 0;
  let latLine: Float64Array | null = null;
  let lonLine: Float64Array | null = null;
  // MAPS vectors (verified format): the header "z.lat[ny]" sits on its own
  // line and the comma-separated values follow on the NEXT non-empty line.
  for (let li = 0; li < lines.length; li++) {
    const l = lines[li].trim();
    if (l.startsWith('z.lat')) {
      const vals = lines[li + 1] ?? '';
      latLine = Float64Array.from(vals.split(',').map((x) => Number(x.trim())));
      li++;
      continue;
    }
    if (l.startsWith('z.lon')) {
      const vals = lines[li + 1] ?? '';
      lonLine = Float64Array.from(vals.split(',').map((x) => Number(x.trim())));
      li++;
      continue;
    }
    const rm = /^\[(\d+)\],(.*)$/.exec(l);
    if (rm) {
      row = Number(rm[1]);
      if (row < 0 || row >= ny) continue;
      col = 0;
      for (const tok of rm[2].split(',')) {
        const v = Number(tok.trim());
        if (Number.isFinite(v)) z[row * nx + col] = v;
        col++;
        if (col >= nx) break;
      }
    }
  }
  if (row !== ny - 1) throw new Error(`ETOPO1 parse incomplete: last row ${row} != ${ny - 1}`);
  if (!latLine || !lonLine) throw new Error('ETOPO1 parse missing MAPS vectors');
  return { z, lat: latLine, lon: lonLine, ny, nx };
}

async function fetchWithTimeout(url: string, timeoutMs: number): Promise<string> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) throw new Error(`NCEI THREDDS returned HTTP ${res.status}`);
    return await res.text();
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Fetch (or load from cache) an ETOPO1 Bedrock subset on the requested
 * resolution lattice. Throws with a structured error on network/API failure —
 * callers must surface it, never fake bathymetry.
 */
export async function getEtopoRegion(req: EtopoRegionRequest): Promise<BathymetryGrid> {
  const res = req.resArcmin;
  if (![1, 2, 4].includes(res)) throw new Error(`resArcmin must be 1, 2 or 4 (got ${res})`);
  const bbox = snapBbox(req.bbox, res);

  const key = cacheKey(bbox, res);
  const metaPath = path.join(cacheDir(), `${key}.json`);
  const dataPath = path.join(cacheDir(), `${key}.f32`);
  try {
    const meta = JSON.parse(await fs.readFile(metaPath, 'utf-8')) as {
      resArcmin: number; lat0: number; lon0: number; nx: number; ny: number;
      provenance: TsunamiProvenance;
    };
    const buf = await fs.readFile(dataPath);
    const bed = new Float32Array(buf.buffer, buf.byteOffset, buf.byteLength / 4);
    if (bed.length !== meta.nx * meta.ny) throw new Error('ETOPO1 cache length mismatch');
    return { ...meta, bed };
  } catch {
    // cache miss — fetch live
  }

  const r0 = latRow(bbox[1]);
  const r1 = latRow(bbox[3]);
  const c0 = lonCol(bbox[0]);
  const c1 = lonCol(bbox[2]);
  if (r0 < 0 || r1 > LAT_ROWS - 1 || c0 < 0 || c1 > LON_COLS - 1) {
    throw new Error(`ETOPO1 region out of range: rows ${r0}..${r1}, cols ${c0}..${c1}`);
  }
  if (r1 <= r0 || c1 <= c0) throw new Error('ETOPO1 empty region');

  // Stride: resArcmin arc-minutes per solver cell.
  const url = `${OPeNDAP_BASE}?z[${r0}:${res}:${r1}][${c0}:${res}:${c1}]`;
  const text = await fetchWithTimeout(url, 120_000);
  const parsed = parseOpendapAscii(text);

  const grid: BathymetryGrid = {
    resArcmin: res,
    lat0: parsed.lat[0],
    lon0: parsed.lon[0],
    nx: parsed.nx,
    ny: parsed.ny,
    bed: parsed.z,
    provenance: {
      datasetId: key,
      provider: 'NOAA NCEI (THREDDS/OPeNDAP)',
      productId: 'ETOPO1_Bed_g_gmt4.nc',
      productType: 'Global 1-arc-minute bedrock relief grid',
      sourceUrl: url,
      retrievedAt: new Date().toISOString(),
      observationStart: 'compiled 2008–2009 source surveys',
      observationEnd: 'static product',
      units: 'metres, positive up',
      crs: 'EPSG:4326 (WGS84), cell-centred at exact arc-minutes',
      verticalDatum: 'MSL (per ETOPO1 product documentation)',
      processing: [
        `subset ${bbox.join(', ')} at ${res}' stride via OPeNDAP ascii`,
        'no vertical transformation applied (MSL assumed for both bathymetry and still sea level)',
      ],
      dataClass: 'measured',
      qualityNotes: [
        'ETOPO1 blends ship soundings and satellite-derived gravity; local errors of tens of metres are possible',
        `${res}' cells cannot resolve coastal flooding; land inundation outputs carry this caveat`,
      ],
      license: 'public domain (U.S. Government work)',
    },
  };

  try {
    await fs.mkdir(cacheDir(), { recursive: true });
    await fs.writeFile(metaPath, JSON.stringify({ ...grid, bed: undefined }));
    await fs.writeFile(dataPath, Buffer.from(grid.bed.buffer, grid.bed.byteOffset, grid.bed.byteLength));
  } catch {
    // cache write failure is non-fatal
  }
  return grid;
}
