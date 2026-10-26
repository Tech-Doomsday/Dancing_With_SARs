import { NextResponse } from 'next/server';
import { getRunState } from '@/lib/tsunami/runner';

/**
 * GET /api/tsunami/runs/[id] — run status + progress; full summary,
 * transport grids and evidence when finished.
 *
 * Transport grids are base64 Float32 arrays sized summary.grid.nx×ny:
 *   maxEta (m), arrival (s, NaN = never), dz (m), bed (m), travel (s).
 */
export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const run = getRunState(id);
  if (!run) {
    return NextResponse.json({ ok: false, error: `Run ${id} not found (it may have been evicted — start a new one)` }, { status: 404 });
  }
  const base = {
    id: run.id,
    eventId: run.eventId,
    status: run.status,
    params: run.params,
    progress: run.progress,
    error: run.error,
    startedAt: run.startedAt,
    finishedAt: run.finishedAt,
  };
  if (run.status !== 'ok') {
    return NextResponse.json({ ok: true, run: base });
  }
  return NextResponse.json({
    ok: true,
    run: {
      ...base,
      summary: run.summary,
      evidence: run.evidence,
      transport: run.transport,
    },
  });
}

export const dynamic = 'force-dynamic';
