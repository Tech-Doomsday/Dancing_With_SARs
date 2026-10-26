import { NextResponse } from 'next/server';
import { listDatasets } from '@/lib/data/datasets';

/**
 * GET /api/datasets — registry of configured datasets.
 * Never fails because of absent local data: each entry carries a structured
 * availability state ('available' | 'missing' | 'incomplete' | 'invalid')
 * with the missing-file list and an explanation.
 */
export async function GET() {
  try {
    const datasets = listDatasets();
    return NextResponse.json({ ok: true, datasets });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: `Dataset registry failed: ${(err as Error).message}` },
      { status: 500 },
    );
  }
}

export const dynamic = 'force-dynamic';
