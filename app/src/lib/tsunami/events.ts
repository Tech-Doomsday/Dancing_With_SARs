/**
 * Curated real tsunami-relevant earthquake events.
 *
 * Every entry is a REAL USGS ComCat event; metadata is refreshed live via the
 * FDSN API (with this registry as fallback) and the moment tensor is fetched
 * from the event detail product when available. Nothing here is synthetic.
 *
 * Station selection per event: DART stations within 2000 km (near-field to
 * regional), with per-station data availability discovered by actually
 * fetching the record (see dart.ts).
 */

import type { TsunamiEvent } from './types';

/**
 * Curated registry. `officialUrl` cites an authoritative page for the event —
 * rendered as a citation only, never as a machine-asserted confirmation.
 */
export const TSUNAMI_EVENTS: TsunamiEvent[] = [
  {
    id: 'tohoku-2011',
    usgsId: 'official20110311054624120_30',
    title: 'Tōhoku, Japan — M 9.1 megathrust (11 Mar 2011)',
    blurb:
      'The best-observed tsunami in history. USGS publishes a finite-fault slip inversion; four DART stations recorded the waves. This is the flagship validation event: simulated arrivals and amplitudes are compared with the archived records.',
    time: '2011-03-11T05:46:24.000Z',
    magnitude: 9.1,
    lat: 38.297,
    lon: 142.373,
    depthKm: 29,
    place: '2011 Great Tōhoku earthquake, NE Honshu, Japan',
    hasFiniteFault: true,
    officialUrl: 'https://www.ngdc.noaa.gov/hazard/tsunamieventdetail.jsp?id=2011+%E2%80%94+Tohoku',
    officialSource: 'NOAA NCEI Global Tsunami Database (Tohoku 2011)',
    note: 'Hindcast validation event — DART 21418 recorded a first-peak residual of ≈1.8 m; the model is compared against the archived records.',
  },
  {
    id: 'maule-2010',
    usgsId: 'official20100227063411530_30',
    title: 'Maule, Chile — M 8.8 megathrust (27 Feb 2010)',
    blurb:
      'A South-American megathrust with a USGS finite-fault model and DART coverage across the SE Pacific. Second validation event, including the buoy-redeployment caveat (positions are current, not 2010 deployments).',
    time: '2010-02-27T06:34:11.000Z',
    magnitude: 8.8,
    lat: -36.122,
    lon: -72.898,
    depthKm: 22,
    place: 'Offshore Maule, Chile',
    hasFiniteFault: true,
    officialUrl: 'https://www.ngdc.noaa.gov/hazard/tsunamieventdetail.jsp?id=2010+%E2%80%94+Maule',
    officialSource: 'NOAA NCEI Global Tsunami Database (Maule 2010)',
    note: 'Hindcast validation event — station positions carry a redeployment caveat documented per station.',
  },
  {
    id: 'panama-2026',
    usgsId: 'us6000u18k',
    title: 'Panama — M 7.7 strike-slip (9 Oct 2026)',
    blurb:
      'The same earthquake this app already tracks with radar evidence. Strike-slip mechanisms are usually weakly tsunamigenic — the honest question is exactly the one this workflow answers: what do the real DART records show, and does a physics simulation agree?',
    time: '2026-10-09T17:56:06.036Z',
    magnitude: 7.7,
    lat: 7.5868,
    lon: -80.769,
    depthKm: 12.647,
    place: '12 km WSW of Pitaloza Arriba, Panama',
    nodalPlane: { strike: 25.21, dip: 74.77, rake: -155.3 },
    hasFiniteFault: false,
    officialUrl: 'https://earthquake.usgs.gov/earthquakes/eventpage/us6000u18k',
    officialSource: 'USGS event page (us6000u18k)',
    note: 'Live evidence event — realtime DART records (≈45-day archive) are checked for a signal; simulation uses the MT plane + scaling (labelled approximation).',
  },
];

export function eventById(id: string): TsunamiEvent | undefined {
  return TSUNAMI_EVENTS.find((e) => e.id === id);
}

/** Result of live USGS enrichment. */
export interface EnrichedEvent extends TsunamiEvent {
  live: boolean;
  liveNote: string;
}

/**
 * Live enrichment: re-query the USGS DETAIL endpoint (the list endpoint omits
 * moment-tensor products — see lib/data/usgs.ts) for current magnitude/depth/
 * mechanism, merging into the curated record. Falls back to the curated
 * values with an explicit note when the catalog is unreachable.
 */
export async function enrichEvents(events: TsunamiEvent[]): Promise<EnrichedEvent[]> {
  const out: EnrichedEvent[] = [];
  for (const ev of events) {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 30_000);
      let detail: {
        properties?: {
          mag?: number | null;
          place?: string;
          products?: Record<string, Array<{ properties?: Record<string, string> }>>;
        };
        geometry?: { coordinates?: [number, number, number] };
      };
      try {
        const res = await fetch(
          `https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&eventid=${ev.usgsId}`,
          { signal: ctrl.signal },
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        detail = await res.json();
      } finally {
        clearTimeout(timer);
      }
      const [lon, lat, depthKm] = detail.geometry?.coordinates ?? [];
      const mt = detail.properties?.products?.['moment-tensor']?.[0]?.properties;
      let np = ev.nodalPlane;
      if (mt) {
        const s = parseFloat(mt['nodal-plane-1-strike']);
        const d = parseFloat(mt['nodal-plane-1-dip']);
        const r = parseFloat(mt['nodal-plane-1-rake']);
        if (Number.isFinite(s) && Number.isFinite(d) && Number.isFinite(r)) {
          np = { strike: s, dip: d, rake: r };
        }
      }
      const mag = detail.properties?.mag;
      out.push({
        ...ev,
        magnitude: typeof mag === 'number' && Number.isFinite(mag) ? mag : ev.magnitude,
        depthKm: typeof depthKm === 'number' && Number.isFinite(depthKm) ? depthKm : ev.depthKm,
        lat: typeof lat === 'number' && Number.isFinite(lat) ? lat : ev.lat,
        lon: typeof lon === 'number' && Number.isFinite(lon) ? lon : ev.lon,
        nodalPlane: np,
        live: true,
        liveNote: `USGS ComCat live detail (id ${ev.usgsId})${np && np !== ev.nodalPlane ? ' incl. moment-tensor plane' : ''}`,
      });
    } catch (err) {
      out.push({
        ...ev,
        live: false,
        liveNote: `USGS live query failed (${(err as Error).message.slice(0, 80)}) — showing the curated record`,
      });
    }
  }
  return out;
}
