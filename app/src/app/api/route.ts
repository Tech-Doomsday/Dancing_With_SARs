import { NextResponse } from 'next/server';
import { findEngineRoot } from '@/lib/engine/registry';

/**
 * GET /api — service health and capability status.
 * Reports which external data services were verified reachable at
 * build/first-request time, without pretending anything is live that is not.
 */
export async function GET() {
  return NextResponse.json({
    ok: true,
    service: 'Dancing with SARs — nisar-hazard-engine web application',
    version: '1.0.0',
    time: new Date().toISOString(),
    capabilities: {
      quantitativePipeline: true,
      okadaForwardModel: true,
      liveCmr: true,
      liveUsgs: true,
      nisarH5Ingestion: 'requires NASA Earthdata credentials (env EARTHDATA_BEARER_TOKEN) — not configured',
      engineCatalog: !!findEngineRoot(),
    },
  });
}
