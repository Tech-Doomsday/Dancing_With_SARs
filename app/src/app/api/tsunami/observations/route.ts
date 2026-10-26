import { NextResponse } from 'next/server';
import { eventById } from '@/lib/tsunami/events';
import { getDartSeries, stationsWithin } from '@/lib/tsunami/dart';
import { detectTsunamiSignal } from '@/lib/tsunami/detection';
import type { DetectionResult } from '@/lib/tsunami/types';

/**
 * GET /api/tsunami/observations?event=<id>[&station=<dartId>]
 *
 * Fetches (with disk cache) the real DART records near the event, runs the
 * de-tiding + detection, and returns per-station results with provenance.
 * Failures surface as availability:'missing' entries — never as fake data.
 */

interface DetectionPayload extends Omit<DetectionResult, 't' | 'resid'> {
  t: number[];
  resid: number[];
}

export async function GET(req: Request) {
  const url = new URL(req.url);
  const eventId = url.searchParams.get('event');
  const stationParam = url.searchParams.get('station');
  if (!eventId) {
    return NextResponse.json({ ok: false, error: 'missing ?event=' }, { status: 400 });
  }
  const ev = eventById(eventId);
  if (!ev) {
    return NextResponse.json({ ok: false, error: `Unknown event ${eventId}` }, { status: 404 });
  }
  const originMs = Date.parse(ev.time);

  const stationIds: string[] = stationParam
    ? [stationParam]
    : stationsWithin(ev.lat, ev.lon, 2500)
        .slice(0, 5)
        .map((s) => s.id);

  const detections: DetectionPayload[] = [];
  for (const id of stationIds) {
    try {
      const { series, provenance } = await getDartSeries(id, originMs);
      const det = detectTsunamiSignal(id, series, originMs, provenance);
      const n = det.t.length;
      const stride = Math.max(1, Math.ceil(n / 400));
      const t: number[] = [];
      const r: number[] = [];
      for (let m = 0; m < n; m += stride) {
        t.push(det.t[m]);
        r.push(det.resid[m]);
      }
      const { t: _tDrop, resid: _rDrop, ...rest } = det;
      detections.push({ ...rest, t, resid: r });
    } catch (err) {
      const { ...base } = {
        stationId: id,
        sigmaNoise: NaN,
        triggerMs: null,
        arrivalMin: null,
        preOriginExcursion: false,
        maxAmplitudeM: null,
        dominantPeriodMin: null,
        nSamples: 0,
        caveats: [],
        availability: { state: 'missing' as const, reason: (err as Error).message.slice(0, 200) },
      };
      detections.push({
        ...base,
        provenance: {
          datasetId: `dart-${id}`,
          provider: 'NOAA National Data Buoy Center',
          productId: `DART ${id}`,
          productType: 'Deep-ocean bottom-pressure recorder',
          sourceUrl: `https://www.ndbc.noaa.gov/station_page.php?station=${id}`,
          retrievedAt: new Date().toISOString(),
          observationStart: 'n/a',
          observationEnd: 'n/a',
          units: 'n/a',
          crs: 'WGS84',
          verticalDatum: 'n/a',
          processing: [],
          dataClass: 'measured' as const,
          qualityNotes: [],
        },
        t: [],
        resid: [],
      });
    }
  }
  return NextResponse.json({ ok: true, eventId, detections });
}

export const dynamic = 'force-dynamic';
