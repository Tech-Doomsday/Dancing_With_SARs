import { NextResponse } from 'next/server';
import { loadEngineStatus } from '@/lib/engine/registry';

/**
 * GET /api/engine/status — honest, filesystem-only facts about the Python
 * engine's runtime state: where its repository was found, whether its
 * (git-ignored) data directory exists, and whether the retrieval poller has
 * ever produced its SQLite state or bundle store. Presence-only on purpose:
 * this route never implies scans have run or verdicts exist.
 */
export const dynamic = 'force-dynamic';

export async function GET() {
  const status = loadEngineStatus();
  if (!status) {
    return NextResponse.json({
      ok: false,
      message:
        'The nisar-hazard-engine source tree was not found next to this app. Set ENGINE_ROOT or run the app from inside the repository.',
    });
  }
  return NextResponse.json({ ok: true, status });
}
