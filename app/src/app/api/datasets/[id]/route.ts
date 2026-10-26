import { NextResponse } from 'next/server';
import {
  getDataset,
  getEvidenceExtras,
  getDatasetAvailability,
  getRecordedRun,
  listDatasets,
  getStackDatasetInfo,
} from '@/lib/data/datasets';
import { STACK_ID } from '@/lib/science/stacks';

/**
 * GET /api/datasets/[id] — full dataset metadata + provenance.
 *
 * Known dataset ids resolve to 200 even when the local scientific data is
 * absent: the response then carries `availability` with a structured state
 * and the registry's fallback description, so the UI can explain exactly
 * what is missing and how to obtain it. Only truly unknown ids 404.
 */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  try {
    const availability = getDatasetAvailability(id);
    const known =
      availability.state === 'available' ||
      listDatasets().some((d) => d.id === id);
    if (!known) {
      return NextResponse.json({ ok: false, error: `Unknown dataset: ${id}` }, { status: 404 });
    }

    // ---- SBAS stack dataset: descriptive info without running the pipeline.
    if (id === STACK_ID) {
      const info = getStackDatasetInfo();
      return NextResponse.json({
        ok: true,
        dataset: {
          id: info.id,
          slug: 'central-valley-sbas-2026',
          title: info.title,
          blurb: info.blurb,
          mode: 'quantitative' as const,
          isSynthetic: true,
          provenance: {
            datasetId: info.id,
            provider: 'MOTUS synthetic stack generator (system plan §10 case-study analog)',
            dataClass: 'synthetic' as const,
            productType: 'Synthetic GUNW-stack fixture (21 epochs / 24 interferograms)',
            platform: 'NISAR L-SAR geometry analog (L band, ascending Track 42 analog)',
            productId: 'stack-central-valley-2026',
            referenceTime: info.epochs[0],
            secondaryTime: info.epochs[info.epochs.length - 1],
            temporalBaselineDays: 252,
            sourceUrl: 'https://github.com/CodexUjayer/dancing-with-sars (src/lib/science/stacks.ts)',
            accessNotes: 'Generated deterministically in-process; always available offline.',
            qualityNotes: info.warnings,
          },
          geometry: {
            wavelength: 0.2360571,
            incidenceAngleDeg: 34,
            headingDeg: -12.5,
            passDirection: 'ascending' as const,
            lookDirection: 'right' as const,
          },
          footprintPolygon: info.footprintPolygon,
          center: info.center,
          grid: { width: 288, height: 220, epsg: 32611 },
          numberOfLooks: 119,
          availability,
          stack: {
            epochs: info.epochs,
            ifgCount: info.ifgCount,
            event: info.event,
          },
        },
        warnings: info.warnings,
      });
    }

    const ds = getDataset(id);
    if (!ds) {
      // Configured but the local data is missing/incomplete/invalid. When a
      // REAL recorded analysis exists (recorded_run.json), it is included so
      // the UI can offer it as an honestly-labelled alternative to re-running.
      const entry = listDatasets().find((d) => d.id === id)!;
      const recordedRun = getRecordedRun(id);
      return NextResponse.json({
        ok: true,
        dataset: {
          id: entry.id,
          slug: entry.slug,
          title: entry.title,
          blurb: entry.blurb,
          mode: entry.mode,
          isSynthetic: entry.isSynthetic,
          provenance: recordedRun
            ? {
                datasetId: entry.id,
                provider: recordedRun.provenance.provider,
                dataClass: 'real' as const,
                productType: recordedRun.provenance.productType,
                platform: recordedRun.provenance.platform,
                productId: recordedRun.provenance.productId,
                referenceTime: recordedRun.provenance.referenceTime,
                secondaryTime: recordedRun.provenance.secondaryTime,
                temporalBaselineDays: recordedRun.provenance.temporalBaselineDays,
                doi: recordedRun.provenance.doi,
                sourceUrl: recordedRun.provenance.sourceUrl,
                qualityNotes: recordedRun.provenance.qualityNotes,
              }
            : {
                datasetId: entry.id,
                provider: entry.sensor,
                dataClass: entry.dataClass,
              },
          geometry: recordedRun ? recordedRun.geometry : undefined,
          footprintPolygon: recordedRun ? recordedRun.footprintPolygon : undefined,
          center: recordedRun ? recordedRun.center : undefined,
          availability: entry.availability,
        },
        availability: entry.availability,
        recordedRun,
      });
    }

    const extras = getEvidenceExtras(id);
    const granules = (extras.granules as Array<Record<string, unknown>> | undefined) ?? [];
    const event = extras.event as Record<string, unknown> | undefined;
    return NextResponse.json({
      ok: true,
      dataset: {
        id: ds.id,
        slug: ds.slug,
        title: ds.title,
        blurb: ds.blurb,
        mode: ds.mode,
        isSynthetic: !!ds.isSynthetic,
        provenance: ds.provenance,
        geometry: ds.geometry,
        footprintPolygon: ds.footprintPolygon,
        center: ds.center,
        grid: ds.phase
          ? { width: ds.phase.meta.width, height: ds.phase.meta.height, epsg: ds.phase.meta.epsg }
          : undefined,
        numberOfLooks: ds.numberOfLooks,
        availability,
      },
      event,
      granules: granules.map((g) => ({
        ...g,
        localBrowseUrl: `/api/external/browse?granule=${encodeURIComponent(String(g.granuleId))}`,
      })),
      granuleSelection: extras.granuleSelection,
      browseCount: extras.browseFiles?.length ?? 0,
      availability,
    });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: `Dataset load failed: ${(err as Error).message}` },
      { status: 500 },
    );
  }
}

export const dynamic = 'force-dynamic';
