import fs from 'fs';
import path from 'path';
import { NextResponse } from 'next/server';

/**
 * GET /api/external/browse?granule=<granuleId>
 * Serves official NISAR GUNW browse imagery (LATLON rendering) that was
 * downloaded from the ASF Earthdata Cloud (open access) into
 * data/nisar-browse/. The filename is strictly validated to prevent path
 * traversal.
 */
const BROWSE_ROOT = path.join(process.cwd(), 'data', 'nisar-browse');
const GRANULE_RE = /^NISAR_L2_PR_GUNW_[A-Za-z0-9_]+$/;

export async function GET(req: Request) {
  const granule = new URL(req.url).searchParams.get('granule') ?? '';
  if (!GRANULE_RE.test(granule)) {
    return NextResponse.json({ ok: false, error: 'Invalid granule identifier' }, { status: 400 });
  }
  const file = path.join(BROWSE_ROOT, `${granule}_LATLON.png`);
  if (!file.startsWith(BROWSE_ROOT)) {
    return NextResponse.json({ ok: false, error: 'Invalid path' }, { status: 400 });
  }
  try {
    const buf = fs.readFileSync(file);
    return new NextResponse(new Uint8Array(buf), {
      headers: {
        'Content-Type': 'image/png',
        'Cache-Control': 'public, max-age=86400',
        'X-Source': 'NASA ASF Earthdata Cloud - official NISAR SDS browse imagery (open access)',
      },
    });
  } catch (err) {
    return NextResponse.json(
      {
        ok: false,
        error:
          `Browse image for this granule has not been cached locally (resolved: ${file}, reason: ${(err as Error).message.slice(0, 80)}). It can be fetched from ` +
          'https://nisar.asf.earthdatacloud.nasa.gov/BROWSE/NISAR_L2_GUNW_PROVISIONAL_V1/ (open access).',
      },
      { status: 404 },
    );
  }
}

export const dynamic = 'force-dynamic';
