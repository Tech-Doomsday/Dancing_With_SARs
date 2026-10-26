import { NextResponse } from 'next/server';
import { loadEngineCatalog } from '@/lib/engine/registry';

/**
 * GET /api/engine/pipelines — the real pipeline catalog of the Python
 * nisar-hazard-engine, read live from engine/pipelines/<name>/manifest.yaml.
 *
 * This mirrors the payload the engine's own registry describes for the
 * planned FastAPI `GET /pipelines` (engine/registry.py::describe), extended
 * with the manifest's full content and the registry's implemented-test
 * (verdict.py defining run()). When the app runs outside the repository,
 * or the engine source is absent, the route degrades honestly instead of
 * inventing a catalog.
 */
export const dynamic = 'force-dynamic';

export async function GET() {
  const catalog = loadEngineCatalog();
  if (!catalog) {
    return NextResponse.json({
      ok: false,
      error: 'engine-source-not-found',
      message:
        'The nisar-hazard-engine source tree (engine/pipelines/) was not found next to this app, so the real pipeline catalog cannot be shown.',
      hint:
        'Run the app from inside the repository (app/ of the nisar-hazard-engine checkout), or set ENGINE_ROOT to the engine repository root.',
    } satisfies import('@/lib/engine/types').EngineUnavailablePayload);
  }
  return NextResponse.json({
    ok: true,
    engineRoot: catalog.engineRoot,
    pipelineCount: catalog.pipelineCount,
    implementedCount: catalog.implementedCount,
    pipelines: catalog.pipelines,
    stages: catalog.stages,
  } satisfies import('@/lib/engine/types').EngineCatalogPayload);
}
