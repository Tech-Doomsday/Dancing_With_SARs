import { NextResponse } from 'next/server';
import { TSUNAMI_EVENTS, enrichEvents } from '@/lib/tsunami/events';

/**
 * GET /api/tsunami/events — curated real events with live USGS enrichment.
 * Falls back to the curated records (flagged) when USGS is unreachable.
 */

const globalCache = globalThis as unknown as {
  __motusTsunamiEvents?: { at: number; data: unknown };
};
const TTL = 15 * 60 * 1000;

export async function GET() {
  const cached = globalCache.__motusTsunamiEvents;
  if (cached && Date.now() - cached.at < TTL) {
    return NextResponse.json({ ok: true, events: cached.data });
  }
  try {
    const events = await enrichEvents(TSUNAMI_EVENTS);
    globalCache.__motusTsunamiEvents = { at: Date.now(), data: events };
    return NextResponse.json({ ok: true, events });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: `Event enrichment failed: ${(err as Error).message}` },
      { status: 502 },
    );
  }
}

export const dynamic = 'force-dynamic';
