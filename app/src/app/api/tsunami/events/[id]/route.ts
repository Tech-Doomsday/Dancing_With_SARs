import { NextResponse } from 'next/server';
import { eventById } from '@/lib/tsunami/events';
import { stationsWithin } from '@/lib/tsunami/dart';
import { screenSeismic } from '@/lib/tsunami/evidence';

/**
 * GET /api/tsunami/events/[id] — one event: curated + screening result +
 * the DART stations that will be checked (with distances). Data availability
 * per station is discovered by the observation endpoint when fetched.
 */
export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const ev = eventById(id);
  if (!ev) {
    return NextResponse.json({ ok: false, error: `Unknown event ${id}` }, { status: 404 });
  }
  const stations = stationsWithin(ev.lat, ev.lon, 2500).slice(0, 5);
  const screening = screenSeismic(ev);
  return NextResponse.json({
    ok: true,
    event: ev,
    screening,
    stations: stations.map((s) => ({
      id: s.id,
      lat: s.lat,
      lon: s.lon,
      name: s.name,
      region: s.region,
      distKm: Math.round(s.distKm),
    })),
  });
}

export const dynamic = 'force-dynamic';
