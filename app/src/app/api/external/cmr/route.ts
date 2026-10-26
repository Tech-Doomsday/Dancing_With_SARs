import { NextResponse } from 'next/server';
import { searchNisarGunw, bracketsEvent } from '@/lib/data/cmr';

/**
 * GET /api/external/cmr — live NISAR GUNW availability search.
 * Query: bbox=minLon,minLat,maxLon,maxLat&eventTime=ISO (optional)
 *       &start=ISO&end=ISO (optional temporal filter)
 */
export async function GET(req: Request) {
  const url = new URL(req.url);
  const bboxParam = url.searchParams.get('bbox');
  const eventTime = url.searchParams.get('eventTime') ?? undefined;
  const start = url.searchParams.get('start') ?? undefined;
  const end = url.searchParams.get('end') ?? undefined;

  let bbox: [number, number, number, number];
  try {
    const parts = (bboxParam ?? '-83.5,6.0,-78.0,11.0').split(',').map(Number);
    if (parts.length !== 4 || parts.some((p) => !Number.isFinite(p))) throw new Error('bad bbox');
    bbox = parts as [number, number, number, number];
  } catch {
    return NextResponse.json(
      { ok: false, error: 'bbox must be minLon,minLat,maxLon,maxLat' },
      { status: 400 },
    );
  }

  try {
    const { granules, hits } = await searchNisarGunw(bbox, start && end ? { start, end } : undefined, {
      pageSize: 100,
    });
    const withFlags = granules.map((g) => ({
      ...g,
      bracketsEvent: eventTime ? bracketsEvent(g, eventTime) : false,
    }));
    return NextResponse.json({
      ok: true,
      query: { bbox, eventTime, start, end },
      hits,
      count: granules.length,
      granules: withFlags,
      source: 'NASA CMR (cmr.earthdata.nasa.gov), live query',
      queriedAt: new Date().toISOString(),
    });
  } catch (err) {
    return NextResponse.json(
      {
        ok: false,
        error: `CMR query failed: ${(err as Error).message}`,
        source: 'NASA CMR (cmr.earthdata.nasa.gov)',
      },
      { status: 502 },
    );
  }
}

export const dynamic = 'force-dynamic';
