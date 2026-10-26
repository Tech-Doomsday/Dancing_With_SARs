/**
 * POST /api/v1/velocity — plan §12 API spec: "GeoTIFF URL & Line-of-Sight
 * Velocity Raster Metadata" for a polygon + minimum coherence. Runs the SBAS
 * pipeline over the demonstration stack (the only ingested stack in this
 * deployment) and returns the velocity raster as a transport grid plus the
 * GeoTIFF via the existing client-side exporter (the UI renders/downloads
 * it); the response carries the full velocity metadata the spec asks for.
 */
import { NextResponse } from 'next/server';
import { z } from 'zod';
import { runSbasPipeline } from '@/lib/science/sbas-pipeline';
import { STACK_ID } from '@/lib/science/stacks';
import { DEFAULT_PARAMS } from '@/lib/science/types';

const bodySchema = z.object({
  datasetId: z.string().optional().default(STACK_ID),
  polygon: z
    .array(z.tuple([z.number(), z.number()]))
    .min(3)
    .optional()
    .describe('WGS84 [lon, lat][] selection polygon (informational: the single ingested stack covers the frame)'),
  min_coherence: z.number().min(0).max(1).optional().describe('Coherence threshold (stack: also the per-ifg gate)'),
});

export async function POST(req: Request) {
  let body: z.infer<typeof bodySchema>;
  try {
    body = bodySchema.parse(await req.json());
  } catch (err) {
    return NextResponse.json({ ok: false, error: `Invalid request: ${(err as Error).message}` }, { status: 400 });
  }
  if (body.datasetId !== STACK_ID) {
    return NextResponse.json(
      {
        ok: false,
        error:
          `Only the demonstration stack (${STACK_ID}) is ingested in this deployment; ` +
          'real GUNW stack ingestion requires Earthdata credentials (see docs/data-sources.md).',
      },
      { status: 409 },
    );
  }

  try {
    const result = runSbasPipeline(
      STACK_ID,
      { ...DEFAULT_PARAMS, coherenceThreshold: body.min_coherence ?? DEFAULT_PARAMS.coherenceThreshold },
      [],
    );
    return NextResponse.json({
      ok: true,
      spec: '/api/v1/velocity (plan §12): LOS velocity raster + metadata',
      datasetId: STACK_ID,
      minCoherence: body.min_coherence ?? DEFAULT_PARAMS.coherenceThreshold,
      polygon: body.polygon,
      meta: result.velocity.meta,
      // The velocity transport grid (displacement slot = m/yr, + = away from
      // the satellite; coherence slot = temporal coherence; sigma = 1σ/yr).
      velocity: result.velocity,
      summary: result.summary,
      corrections: result.corrections,
      narrative: result.narrative,
      geotiffNote:
        'The GeoTIFF is exported client-side from this exact grid (standards-compliant writer, ' +
        'independently verified — see the export buttons in the UI); no server-side file is created.',
    });
  } catch (err) {
    return NextResponse.json({ ok: false, error: (err as Error).message }, { status: 500 });
  }
}
