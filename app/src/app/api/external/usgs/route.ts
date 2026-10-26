import { NextResponse } from 'next/server';
import { queryUsgsEvents } from '@/lib/data/usgs';

/**
 * GET /api/external/usgs — live USGS ComCat query.
 * Query: start=ISO&end=ISO&bbox=minLon,minLat,maxLon,maxLat&minmag=4.5
 */
export async function GET(req: Request) {
  const url = new URL(req.url);
  const start = url.searchParams.get('start');
  const end = url.searchParams.get('end');
  if (!start || !end) {
    return NextResponse.json({ ok: false, error: 'start and end are required ISO dates' }, { status: 400 });
  }
  const bboxParam = url.searchParams.get('bbox');
  let bbox: [number, number, number, number] | undefined;
  if (bboxParam) {
    const parts = bboxParam.split(',').map(Number);
    if (parts.length !== 4) {
      return NextResponse.json({ ok: false, error: 'bbox must be minLon,minLat,maxLon,maxLat' }, { status: 400 });
    }
    bbox = parts as [number, number, number, number];
  }
  const minmag = Number(url.searchParams.get('minmag') ?? 4.5);

  try {
    const events = await queryUsgsEvents({ startTime: start, endTime: end, boundingBox: bbox, minMagnitude: minmag, limit: 60 });
    return NextResponse.json({
      ok: true,
      count: events.length,
      events,
      source: 'USGS ComCat FDSN API (earthquake.usgs.gov), live query',
      queriedAt: new Date().toISOString(),
    });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: `USGS query failed: ${(err as Error).message}`, source: 'USGS ComCat FDSN API' },
      { status: 502 },
    );
  }
}

export const dynamic = 'force-dynamic';
