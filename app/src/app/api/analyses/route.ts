import { NextResponse } from 'next/server';
import { z } from 'zod';
import {
  getDataset,
  getEvidenceExtras,
  getDatasetAvailability,
  listDatasets,
} from '@/lib/data/datasets';
import { runPipeline } from '@/lib/science/pipeline';
import { DEFAULT_PARAMS } from '@/lib/science/types';
import type { AnalysisResult, TransportGrid, UsgsEventLite } from '@/lib/science/types';
import { queryUsgsEvents, filterEventsInWindow, loadStaticEvent, mergeCachedMechanism } from '@/lib/data/usgs';
import { buildPredictionOnGrid } from '@/lib/science/attribution';
import { runSbasPipeline } from '@/lib/science/sbas-pipeline';
import { STACK_ID, centralValleyStack as centralValleyStackRef } from '@/lib/science/stacks';
import type { SbasResult } from '@/lib/science/types';
import { latLonToUtm, utmZoneToEpsg, utmZone } from '@/lib/science/utm';
import { db } from '@/lib/db';

const paramsSchema = z
  .object({
    coherenceThreshold: z.number().min(0).max(1).optional(),
    referenceStrategy: z.enum(['farField', 'fullFrameMedian']).optional(),
    farFieldMargin: z.number().min(0.02).max(0.4).optional(),
    detrend: z.boolean().optional(),
    detectionFloor: z.number().min(0.001).max(0.2).optional(),
    significanceK: z.number().min(1).max(6).optional(),
  })
  .optional();

const bodySchema = z.object({
  datasetId: z.string().min(1),
  params: paramsSchema,
});

/** In-memory result cache (pipeline is deterministic per dataset+params). */
const globalCache = globalThis as unknown as { __motusResults?: Map<string, AnalysisResult | unknown> };
if (!globalCache.__motusResults) globalCache.__motusResults = new Map();
const resultCache = globalCache.__motusResults;
/** Cache bound: each entry holds ~10 MB of transport rasters; this host has
 *  a small memory budget, so evict oldest entries beyond this limit. */
const RESULT_CACHE_MAX = 10;
function rememberResult(key: string, value: unknown): void {
  resultCache.set(key, value);
  while (resultCache.size > RESULT_CACHE_MAX) {
    const oldest = resultCache.keys().next().value as string;
    resultCache.delete(oldest);
  }
}

function cacheKey(datasetId: string, params: unknown): string {
  return `${datasetId}::${JSON.stringify(params)}`;
}

function b64(arr: Float32Array | Uint8Array): string {
  const u8 = new Uint8Array(arr.buffer, arr.byteOffset, arr.byteLength);
  return Buffer.from(u8).toString('base64');
}

/** Cheap stack access for the window bounds (memoized fixture). */
function centralValleyStack0() {
  return centralValleyStackRef();
}

/** Footprint bbox + margin in degrees. */
function bboxFromPolygon(poly: [number, number][], marginDeg = 0.5): [number, number, number, number] {
  const lons = poly.map((p) => p[0]);
  const lats = poly.map((p) => p[1]);
  return [
    Math.min(...lons) - marginDeg,
    Math.min(...lats) - marginDeg,
    Math.max(...lons) + marginDeg,
    Math.max(...lats) + marginDeg,
  ];
}

async function fetchEventsForDataset(
  datasetId: string,
  referenceTime: string,
  secondaryTime: string,
  center: { lat: number; lon: number } | undefined,
  footprint: [number, number][],
  warnings: string[],
): Promise<UsgsEventLite[]> {
  try {
    const bbox = bboxFromPolygon(footprint, 0.5);
    const events = await queryUsgsEvents({
      startTime: new Date(Date.parse(referenceTime) - 86400000).toISOString(),
      endTime: new Date(Date.parse(secondaryTime) + 86400000).toISOString(),
      boundingBox: bbox,
      minMagnitude: 4.0,
      limit: 200,
      // Magnitude-descending: with a limit in play, time-ascending order
      // silently cut the Panama M7.7 mainshock behind its aftershock swarm
      // (2026-10-24 fix). The biggest event must always survive the cut.
      orderBy: 'magnitude',
    });
    const centerRef = center ?? { lat: 0, lon: 0 };
    return filterEventsInWindow(events, referenceTime, secondaryTime, centerRef.lat, centerRef.lon, 400);
  } catch (err) {
    warnings.push(
      `USGS catalog query failed (${(err as Error).message}); attribution proceeds without independent seismic evidence.`,
    );
    return [];
  }
}

export async function POST(req: Request) {
  let body: z.infer<typeof bodySchema>;
  try {
    body = bodySchema.parse(await req.json());
  } catch (err) {
    return NextResponse.json({ ok: false, error: `Invalid request: ${(err as Error).message}` }, { status: 400 });
  }

  const { datasetId } = body;

  // ---- SBAS stack datasets run the time-series pipeline (no local-file
  // gate: the fixture is generated deterministically in-process).
  if (datasetId === STACK_ID) {
    const params = { ...DEFAULT_PARAMS, ...(body.params ?? {}) };
    const key = cacheKey(datasetId, params);
    if (resultCache.has(key)) {
      const cached = resultCache.get(key) as { runId?: string } & Record<string, unknown>;
      return NextResponse.json({ ok: true, cached: true, runId: cached.runId, result: cached });
    }
    const warnings: string[] = [];
    let realEvents: UsgsEventLite[] = [];
    try {
      const stack0 = centralValleyStack0();
      const all = await queryUsgsEvents({
        startTime: stack0.epochs[0].time,
        endTime: stack0.epochs[stack0.epochs.length - 1].time,
        boundingBox: [-122, 34, -118, 39],
        minMagnitude: 4.0,
        limit: 50,
      });
      realEvents = all;
    } catch {
      warnings.push(
        'Live USGS context query failed; the stack analysis proceeded with the synthetic catalog event only.',
      );
    }
    try {
      const result = runSbasPipeline(datasetId, params, realEvents);
      result.warnings.push(...warnings);
      const run = await db.analysisRun.create({
        data: {
          datasetId,
          status: 'ok',
          paramsJson: JSON.stringify(params),
          summaryJson: JSON.stringify(result.summary),
          durationMs: result.processingMs,
        },
      });
      (result as { runId?: string }).runId = run.id;
      rememberResult(key, result as unknown as AnalysisResult);
      return NextResponse.json({ ok: true, runId: run.id, result });
    } catch (err) {
      return NextResponse.json({ ok: false, error: (err as Error).message }, { status: 500 });
    }
  }

  const ds = getDataset(datasetId);
  if (!ds) {
    // Distinguish "unknown dataset" (404) from "known dataset whose local
    // scientific data has not been obtained" (409 with a structured reason).
    const availability = getDatasetAvailability(datasetId);
    const known = listDatasets().some((d) => d.id === datasetId);
    if (!known) {
      return NextResponse.json({ ok: false, error: `Unknown dataset: ${datasetId}` }, { status: 404 });
    }
    return NextResponse.json(
      {
        ok: false,
        code: 'dataset-unavailable',
        error:
          `The local scientific data for this dataset is ${availability.state}: ${availability.explanation}`,
        availability,
      },
      { status: 409 },
    );
  }

  // Parameter precedence: request body > dataset-suggested defaults > global defaults.
  // (Scene-adapted suggestions, e.g. a lower coherence threshold for a
  // decorrelated ionosphere-dominated pair, are visible and adjustable in the UI.)
  const params = { ...DEFAULT_PARAMS, ...(ds.suggestedParams ?? {}), ...(body.params ?? {}) };
  const key = cacheKey(datasetId, params);
  if (resultCache.has(key)) {
    const cached = resultCache.get(key) as { runId?: string } & Record<string, unknown>;
    return NextResponse.json({ ok: true, cached: true, runId: cached.runId, result: cached });
  }

  const t0 = Date.now();

  try {
    if (ds.mode === 'quantitative') {
      const warnings: string[] = [];
      const events = ds.isSynthetic
        ? []
        : await fetchEventsForDataset(
            datasetId,
            ds.provenance.referenceTime,
            ds.provenance.secondaryTime,
            ds.center,
            ds.footprintPolygon,
            warnings,
          );
      const result = runPipeline(ds, params, events);
      result.warnings.push(...warnings);

      const run = await db.analysisRun.create({
        data: {
          datasetId,
          status: 'ok',
          paramsJson: JSON.stringify(params),
          summaryJson: JSON.stringify(result.summary),
          durationMs: result.processingMs,
        },
      });
      (result as { runId?: string }).runId = run.id;
      rememberResult(key, result);
      return NextResponse.json({ ok: true, runId: run.id, result });
    }

    // ---- Evidence mode (Panama) ------------------------------------------
    const extras = getEvidenceExtras(datasetId);
    const event = (extras.event as unknown as UsgsEventLite | undefined) ?? undefined;
    interface EvidenceGranule {
      granuleId: string;
      timeStart: string;
      timeEnd: string;
      polygon: string;
      sizeMB: number;
      dataUrl?: string;
      browseUrl?: string;
      conceptId: string;
      [k: string]: unknown;
    }
    const granules = ((extras.granules as Array<EvidenceGranule>) ?? []).map((g) => ({
      ...g,
      localBrowseUrl: `/api/external/browse?granule=${encodeURIComponent(g.granuleId)}`,
    }));

    // Live USGS context query around the event (sequence + verification).
    const liveEvents: UsgsEventLite[] = [];
    let liveStatus = 'unavailable';
    try {
      const evTime = event?.time ?? ds.provenance.referenceTime;
      const all = await queryUsgsEvents({
        startTime: new Date(Date.parse(evTime) - 86400000).toISOString(),
        endTime: new Date(Date.parse(evTime) + 3 * 86400000).toISOString(),
        boundingBox: [-84, 5, -76, 11],
        minMagnitude: 4.0,
        limit: 200,
        orderBy: 'magnitude',
      });
      // The cached detailed record carries the moment-tensor product that
      // the FDSN listing omits — merge it into the matching live event so
      // mechanism-dependent displays/checks work on live data too.
      liveEvents.push(...mergeCachedMechanism(all, event));
      liveStatus = 'live';
    } catch {
      liveStatus = 'failed (network) — cached event record shown';
    }

    // Forward-model prediction on a Panama-local UTM grid.
    let prediction: TransportGrid | undefined;
    if (event) {
      const utm = latLonToUtm(event.lat, event.lon);
      const epsg = utmZoneToEpsg(utmZone(event.lon), event.lat < 0 ? 'S' : 'N');
      const size = 400;
      const spacing = 500; // metres
      const meta = {
        width: size,
        height: size,
        xStart: utm.easting - (size * spacing) / 2,
        xSpacing: spacing,
        yStart: utm.northing + (size * spacing) / 2,
        ySpacing: -spacing,
        epsg,
      };
      const raster = buildPredictionOnGrid(meta, event, ds.geometry);
      if (!('error' in raster)) {
        const n = size * size;
        const maskArr = new Uint8Array(n);
        for (let i = 0; i < n; i++) maskArr[i] = Number.isNaN(raster.data[i]) ? 0 : 1;
        prediction = {
          width: size,
          height: size,
          displacement: b64(raster.data),
          coherence: b64(new Float32Array(n).fill(NaN)),
          mask: b64(maskArr),
          sigma: b64(new Float32Array(n).fill(NaN)),
          meta,
        };
      }
    }

    const result = {
      kind: 'evidence' as const,
      datasetId,
      event,
      granules,
      liveEvents,
      liveStatus,
      prediction,
      narrative: {
        whatChanged:
          `On ${event ? event.time.slice(0, 16).replace('T', ' ') : 'unknown'} UTC a magnitude ${event?.magnitude ?? '?'} earthquake struck ${event?.place ?? 'the region'}. ` +
          `${granules.length} NISAR GUNW pairs catalogued in NASA CMR have acquisition intervals that span the event ` +
          `(e.g. ${granules[0] ? String(granules[0].granuleId).slice(0, 40) + '…' : 'n/a'}). Whether these products ` +
          `contain a measurable earthquake-related deformation signal has not yet been established: confirming it ` +
          `requires downloading and processing the full science products.`,
        howMuch:
          `The measured deformation cannot be quantified in this deployment: downloading the full NISAR HDF5 products ` +
          `requires NASA Earthdata credentials which are not configured here. The identical measurement pipeline is ` +
          `demonstrated on the open NISAR sample product and on synthetic fixtures with analytic ground truth. ` +
          (prediction
            ? `The forward model below predicts peak LOS motion on the order of ${(decodePeak(prediction) * 100).toFixed(0)} cm from the published USGS mechanism — this is a model prediction, not a measurement.`
            : ''),
        reliability:
          `Granule metadata and availability are real and live-verified against NASA CMR. Browse imagery is the official ` +
          `SDS rendering. The forward model carries the substantial uncertainty of empirical magnitude-area scaling ` +
          `(Wells & Coppersmith 1994) and an assumed 2:1 rupture aspect ratio. No claim is made about the measured ` +
          `displacement field itself.`,
      },
      warnings: [
        'Evidence mode: quantitative processing of this pair requires an Earthdata-authenticated product download.',
      ],
      processingMs: Date.now() - t0,
    };

    const run = await db.analysisRun.create({
      data: {
        datasetId,
        status: 'ok',
        paramsJson: JSON.stringify({ ...params, mode: 'evidence' }),
        summaryJson: null,
        durationMs: result.processingMs,
      },
    });
    (result as { runId?: string }).runId = run.id;
    resultCache.set(key, result);
    return NextResponse.json({ ok: true, runId: run.id, result });
  } catch (err) {
    await db.analysisRun
      .create({
        data: {
          datasetId,
          status: 'error',
          paramsJson: JSON.stringify(params),
          error: (err as Error).message.slice(0, 500),
          durationMs: Date.now() - t0,
        },
      })
      .catch(() => undefined);
    return NextResponse.json({ ok: false, error: (err as Error).message }, { status: 422 });
  }
}

export const dynamic = 'force-dynamic';
export const maxDuration = 60;

/** Helper: decode the prediction grid to find its peak magnitude. */
function decodePeak(tg: TransportGrid): number {
  const buf = Buffer.from(tg.displacement, 'base64');
  const view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  let mx = 0;
  for (let i = 0; i < buf.byteLength; i += 4) {
    const v = view.getFloat32(i, true);
    if (Number.isFinite(v) && Math.abs(v) > Math.abs(mx)) mx = v;
  }
  return Math.abs(mx) || 0.001;
}
