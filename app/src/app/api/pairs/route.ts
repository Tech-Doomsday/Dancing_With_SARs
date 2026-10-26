import { NextResponse } from 'next/server';
import { z } from 'zod';
import { findPairs } from '@/lib/data/pairs';

const bodySchema = z.object({
  lat: z.number().min(-85).max(85),
  lon: z.number().min(-180).max(180),
  placeLabel: z.string().min(1).max(120),
  daysBack: z.number().int().min(14).max(365).optional(),
});

/**
 * POST /api/pairs — "find me a good radar pair" search.
 *
 * Queries the LIVE NASA CMR catalog for NISAR GUNW pairs over the chosen
 * point, then the LIVE USGS catalog for earthquake context inside each pair's
 * window. Ranking is metadata-only and says so in the response note.
 */
export async function POST(req: Request) {
  let body: z.infer<typeof bodySchema>;
  try {
    body = bodySchema.parse(await req.json());
  } catch (err) {
    return NextResponse.json({ ok: false, error: `Invalid request: ${(err as Error).message}` }, { status: 400 });
  }

  try {
    const result = await findPairs({
      lat: body.lat,
      lon: body.lon,
      placeLabel: body.placeLabel,
      daysBack: body.daysBack ?? 120,
    });
    return NextResponse.json(result);
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: `Catalog search failed: ${(err as Error).message}` },
      { status: 502 },
    );
  }
}

export const dynamic = 'force-dynamic';
export const maxDuration = 60;
