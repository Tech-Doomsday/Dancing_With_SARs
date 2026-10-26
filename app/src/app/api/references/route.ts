import { NextResponse } from 'next/server';
import { REFERENCES } from '@/lib/data/references';
import { db } from '@/lib/db';

/**
 * GET /api/references — the scientific reference registry.
 * The filesystem registry (src/lib/data/references.ts) is authoritative;
 * records are mirrored into the database for audit (first call seeds).
 */
export async function GET() {
  try {
    const count = await db.reference.count();
    if (count === 0) {
      await db.reference.createMany({
        data: REFERENCES.map((r) => ({
          key: r.key,
          title: r.title,
          authors: r.authors,
          year: r.year,
          venue: r.venue,
          url: r.url,
          doi: r.doi,
          type: r.type,
          supportsJson: JSON.stringify(r.supports),
          note: r.note,
          checkedAt: r.checkedAt,
        })),
      });
    }
    return NextResponse.json({ ok: true, references: REFERENCES });
  } catch (err) {
    // The registry is still served even if the database mirror fails.
    return NextResponse.json({
      ok: true,
      references: REFERENCES,
      warning: `Database mirror unavailable: ${(err as Error).message}`,
    });
  }
}

export const dynamic = 'force-dynamic';
