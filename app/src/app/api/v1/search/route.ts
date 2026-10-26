/**
 * GET /api/v1/search — plan §12 API spec: "List of available NISAR
 * GUNW/GCOV granules" for a bbox + date range, via the live NASA CMR
 * (provider ASF) with the same semantics as the pair finder.
 */
import { NextResponse } from 'next/server';
import { searchNisarGunw } from '@/lib/data/cmr';

export async function GET(req: Request) {
  const url = new URL(req.url);
  const bboxRaw = url.searchParams.get('bbox');
  const start = url.searchParams.get('start_date');
  const end = url.searchParams.get('end_date');

  if (!bboxRaw) {
    return NextResponse.json(
      { ok: false, error: 'bbox=W,S,E,N (degrees) is required' },
      { status: 400 },
    );
  }
  const bbox = bboxRaw.split(',').map(Number);
  if (bbox.length !== 4 || bbox.some((v) => !Number.isFinite(v))) {
    return NextResponse.json({ ok: false, error: 'bbox must be four numbers W,S,E,N' }, { status: 400 });
  }
  const [west, south, east, north] = bbox;
  if (!(west < east && south < north)) {
    return NextResponse.json({ ok: false, error: 'bbox must satisfy W<E and S<N' }, { status: 400 });
  }

  try {
    const temporal =
      start && end
        ? { start: new Date(start).toISOString(), end: new Date(end).toISOString() }
        : start
          ? { start: new Date(start).toISOString(), end: new Date().toISOString() }
          : end
            ? { start: new Date(0).toISOString(), end: new Date(end).toISOString() }
            : undefined;
    const res = await searchNisarGunw([west, south, east, north], temporal, { pageSize: 120 });
    return NextResponse.json({
      ok: true,
      spec: '/api/v1/search (plan §12): bbox + date range → available NISAR GUNW granule metadata',
      count: res.granules.length,
      hits: res.hits ?? undefined,
      granules: res.granules.map((g) => ({
        granuleId: g.granuleId,
        timeStart: g.timeStart,
        timeEnd: g.timeEnd,
        polygon: g.polygon,
        sizeMB: g.sizeMB,
        conceptId: g.conceptId,
        dataUrl: g.dataUrl ?? null,
        browseUrl: g.browseUrl ?? null,
      })),
    });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: `CMR query failed: ${(err as Error).message}` },
      { status: 502 },
    );
  }
}
