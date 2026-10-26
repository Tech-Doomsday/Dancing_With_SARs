import { NextResponse } from 'next/server';
import { db } from '@/lib/db';

/**
 * GET /api/analyses/[id] — retrieve a stored run.
 * Results are deterministic given dataset + parameters, so a cache miss after
 * a server restart returns the persisted summary (full grids are recomputable
 * by re-running).
 */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    const run = await db.analysisRun.findUnique({ where: { id } });
    if (!run) {
      return NextResponse.json({ ok: false, error: `Unknown run: ${id}` }, { status: 404 });
    }
    return NextResponse.json({
      ok: true,
      run: {
        id: run.id,
        datasetId: run.datasetId,
        status: run.status,
        params: JSON.parse(run.paramsJson),
        summary: run.summaryJson ? JSON.parse(run.summaryJson) : null,
        error: run.error,
        createdAt: run.createdAt,
        durationMs: run.durationMs,
      },
    });
  } catch (err) {
    return NextResponse.json({ ok: false, error: (err as Error).message }, { status: 500 });
  }
}

export const dynamic = 'force-dynamic';
