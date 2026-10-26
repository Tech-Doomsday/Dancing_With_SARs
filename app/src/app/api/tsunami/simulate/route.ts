import { NextResponse } from 'next/server';
import { z } from 'zod';
import { startRun } from '@/lib/tsunami/runner';
import { eventById } from '@/lib/tsunami/events';

/**
 * POST /api/tsunami/simulate  { eventId, params? }
 * Starts a background simulation and returns { runId } immediately.
 * Poll GET /api/tsunami/runs/[id] for status/progress/results.
 */
const bodySchema = z.object({
  eventId: z.string().min(1),
  params: z
    .object({
      resArcmin: z.union([z.literal(1), z.literal(2), z.literal(4)]),
      simHours: z.number().min(0.5).max(8),
      manningN: z.number().min(0).max(0.2),
      order: z.union([z.literal(1), z.literal(2)]),
      arrivalThresholdM: z.number().min(0.001).max(1),
      domainHalfDeg: z.number().min(1).max(8),
    })
    .partial()
    .optional(),
});

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: 'invalid JSON body' }, { status: 400 });
  }
  const parsed = bodySchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ ok: false, error: `invalid request: ${parsed.error.message.slice(0, 300)}` }, { status: 400 });
  }
  const ev = eventById(parsed.data.eventId);
  if (!ev) {
    return NextResponse.json({ ok: false, error: `Unknown event ${parsed.data.eventId}` }, { status: 404 });
  }
  try {
    const runId = startRun(parsed.data.eventId, parsed.data.params ?? {});
    return NextResponse.json({ ok: true, runId });
  } catch (err) {
    return NextResponse.json({ ok: false, error: (err as Error).message }, { status: 500 });
  }
}

export const dynamic = 'force-dynamic';
