/**
 * Simulation job runner.
 *
 * Runs the full scientific pipeline for one event:
 *   USGS source → ETOPO1 bathymetry → Okada+Tanioka-Seno+Kajiura initial
 *   condition → NSWE solver (chunked so the dev-server event loop stays
 *   responsive) → travel-time field → gauge/comparison assembly → evidence.
 *
 * Job state lives in a module-global map (dev server = one process); run
 * metadata is ALSO persisted to SQLite (TsunamiRun) for audit, and result
 * grids are cached on disk under data/derived/tsunami/<runId>/ so finished
 * runs survive server restarts and can be retrieved without recomputation.
 *
 * The runner never fakes progress: status is 'running' until the solver
 * actually finishes, 'error' with the message when anything throws.
 */

import { promises as fs } from 'fs';
import path from 'path';
import { db } from '@/lib/db';
import { getEtopoRegion } from './etopo';
import { getDartSeries, stationsWithin, stationById } from './dart';
import {
  buildSourceField,
  fetchUsgsFiniteFault,
  uniformFaultFromScaling,
  sourceProvenance,
  DEFAULT_SOURCE_OPTIONS,
  type Subfault,
} from './source';
import { NsweSolver, type GaugeSpec } from './fv-solver';
import { travelTimeField, travelTimeAt } from './traveltime';
import { classifyEvidence, screenSeismic } from './evidence';
import { eventById } from './events';
import type {
  DetectionResult,
  GaugeSeries,
  RunStatus,
  SimParams,
  StationComparison,
  TsunamiRunSummary,
} from './types';
import { DEFAULT_SIM_PARAMS } from './types';
import { detectTsunamiSignal } from './detection';

export interface RunTransport {
  /** b64-encoded Float32 grids, dimensions from summary.grid */
  maxEtaB64: string;
  arrivalB64: string;
  /** initial seafloor displacement (m) */
  dzB64: string;
  /** bed elevation (m, positive up) */
  bedB64: string;
  /** travel-time field (s, NaN on land/unreachable) */
  travelB64: string;
  /** detection results for the evidence panel (series downsampled) */
  detections: DetectionTransport[];
}

/** JSON-safe detection payload (series as plain arrays). */
export interface DetectionTransport extends Omit<DetectionResult, 't' | 'resid'> {
  t: number[];
  resid: number[];
}

export interface RunState {
  id: string;
  eventId: string;
  status: RunStatus;
  params: SimParams;
  progress: { tSim: number; tTarget: number; steps: number; maxVel: number };
  error: string | null;
  startedAt: number;
  finishedAt: number | null;
  summary: TsunamiRunSummary | null;
  transport: RunTransport | null;
  evidence: ReturnType<typeof classifyEvidence> | null;
  detections: DetectionResult[];
}

const globalRuns = globalThis as unknown as {
  __motusTsunamiRuns?: Map<string, RunState>;
};
if (!globalRuns.__motusTsunamiRuns) globalRuns.__motusTsunamiRuns = new Map();
const runs = globalRuns.__motusTsunamiRuns;

const MAX_COMPLETED_IN_MEMORY = 4;

function resultsDir(runId: string): string {
  return path.join(process.cwd(), 'data', 'derived', 'tsunami', runId);
}

function b64(arr: Float32Array): string {
  return Buffer.from(arr.buffer, arr.byteOffset, arr.byteLength).toString('base64');
}

/** Downsample a detection series for transport (≤ 400 points). */
function downsampleDetection(d: DetectionResult): DetectionTransport {
  const n = d.t.length;
  if (n <= 400) {
    return { ...d, t: Array.from(d.t), resid: Array.from(d.resid) };
  }
  const stride = Math.ceil(n / 400);
  const t: number[] = [];
  const r: number[] = [];
  for (let m = 0; m < n; m += stride) {
    t.push(d.t[m]);
    r.push(d.resid[m]);
  }
  return { ...d, t, resid: r };
}

async function loadDetections(eventId: string): Promise<DetectionResult[]> {
  const ev = eventById(eventId);
  if (!ev) return [];
  const originMs = Date.parse(ev.time);
  const stations = stationsWithin(ev.lat, ev.lon, 2500).slice(0, 5);
  const out: DetectionResult[] = [];
  for (const st of stations) {
    try {
      const { series, provenance, kind } = await getDartSeries(st.id, originMs);
      out.push(detectTsunamiSignal(st.id, series, originMs, provenance));
      void kind;
    } catch (err) {
      out.push({
        stationId: st.id,
        provenance: {
          datasetId: `dart-${st.id}`,
          provider: 'NOAA National Data Buoy Center',
          productId: `DART ${st.id} (${st.name})`,
          productType: 'Deep-ocean bottom-pressure recorder',
          sourceUrl: `https://www.ndbc.noaa.gov/station_page.php?station=${st.id}`,
          retrievedAt: new Date().toISOString(),
          observationStart: 'n/a',
          observationEnd: 'n/a',
          units: 'n/a',
          crs: `station position ${st.lat.toFixed(3)}, ${st.lon.toFixed(3)} (WGS84)`,
          verticalDatum: 'n/a',
          processing: [],
          dataClass: 'measured',
          qualityNotes: [],
        },
        t: new Float64Array(0),
        resid: new Float32Array(0),
        sigmaNoise: NaN,
        triggerMs: null,
        arrivalMin: null,
        preOriginExcursion: false,
        maxAmplitudeM: null,
        dominantPeriodMin: null,
        nSamples: 0,
        caveats: [],
        availability: { state: 'missing', reason: (err as Error).message.slice(0, 160) },
      });
    }
  }
  return out;
}

/**
 * Create + start a run. Returns the run id immediately; the caller polls
 * getRunState().
 */
export function startRun(eventId: string, paramsIn: Partial<SimParams>): string {
  const ev = eventById(eventId);
  if (!ev) throw new Error(`Unknown event ${eventId}`);
  // One simulation at a time: each 2' run holds several hundred MB of grids;
  // concurrent runs OOM-killed the dev server during validation (verified
  // 2026-10-24). The API surfaces this as an explicit retry-later error.
  for (const r of runs.values()) {
    if (r.status === 'running' || r.status === 'queued') {
      throw new Error(
        `a simulation is already running (${r.id}, ${(r.progress.tSim / 60).toFixed(0)} min of ${(
          r.progress.tTarget / 60
        ).toFixed(0)} min modelled) — wait for it to finish before starting another`,
      );
    }
  }
  const params: SimParams = { ...DEFAULT_SIM_PARAMS, ...paramsIn };
  const id = `ts-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  const state: RunState = {
    id,
    eventId,
    status: 'queued',
    params,
    progress: { tSim: 0, tTarget: params.simHours * 3600, steps: 0, maxVel: 0 },
    error: null,
    startedAt: Date.now(),
    finishedAt: null,
    summary: null,
    transport: null,
    evidence: null,
    detections: [],
  };
  runs.set(id, state);

  // Fire and forget; all outcomes recorded in state.
  void executeRun(state, ev).catch((err) => {
    state.status = 'error';
    state.error = (err as Error).message;
    state.finishedAt = Date.now();
  });
  return id;
}

async function executeRun(state: RunState, ev: NonNullable<ReturnType<typeof eventById>>): Promise<void> {
  const originMs = Date.parse(ev.time);

  // ---- Prisma audit record ----
  let dbId: string | undefined;
  try {
    const rec = await db.tsunamiRun.create({
      data: {
        eventId: ev.id,
        status: 'running',
        paramsJson: JSON.stringify(state.params),
      },
    });
    dbId = rec.id;
  } catch {
    dbId = undefined; // DB issues must not block the science
  }

  try {
    state.status = 'running';

    // ---- 1. observations (before the simulation, so evidence is independent) ----
    state.detections = await loadDetections(ev.id);

    // ---- 2. source ----
    let subfaults: Subfault[];
    let sourceKind: 'usgs-finite-fault' | 'usgs-mt-uniform';
    let momentNM: number | null = null;
    let detail: string;
    let sourceUrl: string;
    const solverNotes: string[] = [];
    let ff: Awaited<ReturnType<typeof fetchUsgsFiniteFault>> = null;
    try {
      ff = await fetchUsgsFiniteFault(ev.usgsId);
    } catch (err) {
      solverNotes.push(`finite-fault fetch failed (${(err as Error).message.slice(0, 100)}) — falling back`);
      ff = null;
    }
    if (ff) {
      subfaults = ff.subfaults;
      sourceKind = 'usgs-finite-fault';
      momentNM = ff.subfaults.reduce((s, x) => s + x.momentNM, 0) || null;
      detail = `${ff.subfaults.length} USGS finite-fault subfaults`;
      sourceUrl = ff.url;
      solverNotes.push(`source: ${detail}, ΣM0 = ${momentNM ? (momentNM / 1e21).toFixed(2) : '?'}×10²¹ N·m`);
    } else {
      if (!ev.nodalPlane) throw new Error('no finite-fault model and no moment-tensor plane available — cannot build a source');
      const uni = uniformFaultFromScaling({
        magnitude: ev.magnitude,
        depthKm: ev.depthKm,
        lat: ev.lat,
        lon: ev.lon,
        nodalPlane: ev.nodalPlane,
      });
      subfaults = [uni.fault];
      sourceKind = 'usgs-mt-uniform';
      momentNM = uni.fault.momentNM;
      detail = 'uniform plane from the moment-tensor nodal plane + Wells–Coppersmith scaling (approximation)';
      sourceUrl = `https://earthquake.usgs.gov/earthquakes/eventpage/${ev.usgsId}`;
      solverNotes.push(...uni.notes);
    }

    // ---- 3. bathymetry ----
    const half = state.params.domainHalfDeg;
    const bbox: [number, number, number, number] = [ev.lon - half * 1.4, ev.lat - half, ev.lon + half * 1.4, ev.lat + half];
    const grid = await getEtopoRegion({ bbox, resArcmin: state.params.resArcmin });

    // ---- 4. source field ----
    const source = buildSourceField(grid, subfaults, sourceKind, DEFAULT_SOURCE_OPTIONS, detail, momentNM);
    solverNotes.push(...source.notes);
    if (source.maxAbsDz < 0.01) {
      solverNotes.push(`max |seafloor displacement| = ${(source.maxAbsDz * 100).toFixed(2)} cm — negligible vertical water displacement`);
    }

    // ---- 5. travel-time field (needed to refine the seismic-phase mask) ----
    const travel = travelTimeField(grid, source.dz);

    // ---- 5b. refine detections with the model-informed mask ----
    // A long water wave cannot beat the eikonal ray time; observed triggers
    // before 0.85× that bound are seismic-phase contamination (verified on
    // Tōhoku 2011: the +3/+9 min triggers at 555 km DART 21418 vs a ~+29 min
    // water wave). The raw observations (step 1) remain available unchanged.
    for (const d of state.detections) {
      if (d.availability.state !== 'available') continue;
      const st = stationById(d.stationId);
      if (!st) continue;
      const tEik = travelTimeAt(grid, travel, st.lat, st.lon);
      if (!Number.isFinite(tEik)) continue;
      try {
        const { series, provenance } = await getDartSeries(st.id, originMs);
        const refined = detectTsunamiSignal(st.id, series, originMs, provenance, {
          maskUntilMin: 0.85 * (tEik / 60),
        });
        state.detections = state.detections.map((x) => (x.stationId === st.id ? refined : x));
      } catch {
        // keep the raw detection on any refinement failure
      }
    }

    // ---- 5c. gauges: stations inside the domain AND outside the sponge ----
    // (stations inside the boundary damping layer would read a sponge-
    // attenuated signal — not comparable; flagged instead of compared)
    const spongeCells = Math.max(
      2,
      Math.min(
        Math.round(35 / Math.sqrt(state.params.resArcmin)),
        Math.floor(0.15 * Math.min(grid.nx, grid.ny)),
      ),
    );
    const inSponge = (lat: number, lon: number): boolean => {
      const j = Math.round(((lat - grid.lat0) * 60) / grid.resArcmin);
      const i = Math.round(((lon - grid.lon0) * 60) / grid.resArcmin);
      if (j < 0 || j >= grid.ny || i < 0 || i >= grid.nx) return false; // outside entirely
      const d = Math.min(Math.min(j, grid.ny - 1 - j), Math.min(i, grid.nx - 1 - i));
      return d < spongeCells;
    };
    const gaugeSpecs: GaugeSpec[] = state.detections
      .map((d) => stationById(d.stationId))
      .filter((s): s is NonNullable<typeof s> => Boolean(s))
      .filter((s) => Math.abs(s.lat - grid.lat0) < (grid.ny - 1) * grid.resArcmin / 60 + 1 &&
        Math.abs(s.lon - grid.lon0) < (grid.nx - 1) * grid.resArcmin / 60 + 1 &&
        !inSponge(s.lat, s.lon))
      .map((s) => ({ stationId: s.id, lat: s.lat, lon: s.lon }));

    // ---- 6. solver, chunked ----
    const tEnd = state.params.simHours * 3600;
    const solver = new NsweSolver(grid, source.eta0, state.params, gaugeSpecs);
    const wallStart = Date.now();
    // step budget per chunk: keep each chunk < ~150 ms of CPU
    const chunkSteps = state.params.resArcmin >= 2 ? 40 : 20;
    for (;;) {
      const took = solver.advance(chunkSteps, tEnd);
      const pi = solver.progressInfo;
      state.progress = { tSim: pi.t, tTarget: tEnd, steps: pi.steps, maxVel: pi.maxVel };
      if (took === 0 || pi.t >= tEnd) break;
      // yield to the event loop
      await new Promise((r) => setTimeout(r, 0));
    }
    const result = solver.finish();
    const wallClockS = (Date.now() - wallStart) / 1000;

    // ---- 8. station comparisons ----
    const comparisons: StationComparison[] = state.detections.map((d) => {
      const gauge = result.gauges.find((g) => g.stationId === d.stationId);
      let simArrivalMin: number | null = null;
      let simMaxAmp: number | null = null;
      if (gauge) {
        for (let m = 0; m < gauge.tMin.length; m++) {
          const a = Math.abs(gauge.eta[m]);
          if (simArrivalMin === null && m > 0 && a > state.params.arrivalThresholdM) {
            simArrivalMin = gauge.tMin[m];
          }
          if (a > (simMaxAmp ?? 0)) simMaxAmp = a;
        }
        if (simArrivalMin === null && Math.abs(gauge.eta[0]) > state.params.arrivalThresholdM) {
          simArrivalMin = 0;
        }
      }
      const arrivalResidual =
        d.arrivalMin !== null && simArrivalMin !== null ? d.arrivalMin - simArrivalMin : null;
      const amplitudeRatio =
        d.maxAmplitudeM !== null && simMaxAmp !== null && simMaxAmp > 1e-6
          ? d.maxAmplitudeM / simMaxAmp
          : null;
      let note = '';
      const st = stationById(d.stationId);
      if (gauge === undefined) {
        note = st && inSponge(st.lat, st.lon)
          ? 'station inside the boundary damping layer — signal attenuated, not comparable'
          : 'station outside the simulation domain';
      } else if (d.triggerMs === null) {
        note = 'no observed trigger to compare';
      } else if (simArrivalMin === null) {
        note = 'simulated signal never exceeded the arrival threshold at this station';
      } else {
        const dtOk = arrivalResidual !== null && Math.abs(arrivalResidual) <= 15;
        const ampOk = amplitudeRatio !== null && amplitudeRatio >= 1 / 3 && amplitudeRatio <= 3;
        if (dtOk && ampOk) note = 'arrival and amplitude within the documented tolerances';
        else if (!dtOk && !ampOk) note = `arrival off by ${arrivalResidual?.toFixed(0)} min and amplitude ratio ${amplitudeRatio?.toFixed(2)} outside ×3`;
        else if (!dtOk) note = `arrival off by ${arrivalResidual?.toFixed(0)} min (> ±15 min)`;
        else note = `amplitude ratio ${amplitudeRatio?.toFixed(2)} outside ×3`;
      }
      return {
        stationId: d.stationId,
        obsArrivalMin: d.arrivalMin,
        obsMaxAmplitudeM: d.maxAmplitudeM,
        obsSigmaM: Number.isFinite(d.sigmaNoise) ? d.sigmaNoise : null,
        simArrivalMin,
        simMaxAmplitudeM: simMaxAmp,
        arrivalResidualMin: arrivalResidual,
        amplitudeRatio,
        consistent:
          arrivalResidual !== null && amplitudeRatio !== null
            ? Math.abs(arrivalResidual) <= 15 && amplitudeRatio >= 1 / 3 && amplitudeRatio <= 3
            : null,
        note: note || d.stationId,
      };
    });

    // ---- 9. evidence ----
    const evidence = classifyEvidence({
      event: ev,
      detections: state.detections,
      comparisons,
      screening: screenSeismic(ev),
    });

    // ---- 10. summary + transport ----
    const summary: TsunamiRunSummary = {
      eventId: ev.id,
      params: state.params,
      grid: {
        nx: grid.nx,
        ny: grid.ny,
        lat0: grid.lat0,
        lon0: grid.lon0,
        resArcmin: grid.resArcmin,
      },
      wallClockS,
      simDurationS: result.diagnostics.tSim,
      nSteps: result.diagnostics.nSteps,
      minDtS: result.diagnostics.minDt,
      maxDtS: result.diagnostics.maxDt,
      massDriftRelative: result.diagnostics.massDriftRelative,
      maxEtaM: result.diagnostics.maxEtaPeak,
      sourceMaxAbsDzM: source.maxAbsDz,
      comparisons,
      gauges: result.gauges,
      provenance: [
        grid.provenance,
        sourceProvenance(sourceKind, sourceUrl, detail),
        ...state.detections.map((d) => d.provenance),
      ],
      solverNotes: [
        ...solverNotes,
        `NSWE finite-volume solver: hydrostatic reconstruction (Audusse 2004), HLL fluxes${state.params.order === 2 ? ', MUSCL + SSP-RK2' : ', first order'}, wet/dry h_dry = 1e-4 m, CFL ≤ ${state.params.order === 2 ? 0.45 : 0.5}`,
        `Manning n = ${state.params.manningN} s·m^-1/3 (semi-implicit), Rayleigh sponge layers`,
        `mass drift over the run: ${(result.diagnostics.massDriftRelative * 100).toExponential(2)} %`,
        `grid ${grid.nx}×${grid.ny} at ${grid.resArcmin}' — cells are ${3.7 * grid.resArcmin >= 1 ? (grid.resArcmin * 1852 / 1000).toFixed(1) : ''}~${(grid.resArcmin * 1.85).toFixed(1)} km; coastal inundation is NOT resolved at this resolution`,
      ],
    };
    const transport: RunTransport = {
      maxEtaB64: b64(result.fields.maxEta),
      arrivalB64: b64(result.fields.arrival),
      dzB64: b64(source.dz),
      bedB64: b64(grid.bed),
      travelB64: b64(travel),
      detections: state.detections.map(downsampleDetection),
    };

    state.summary = summary;
    state.transport = transport;
    state.evidence = evidence;
    state.status = 'ok';
    state.finishedAt = Date.now();

    // ---- 11. persist to disk + db ----
    try {
      const dir = resultsDir(state.id);
      await fs.mkdir(dir, { recursive: true });
      await fs.writeFile(path.join(dir, 'run.json'), JSON.stringify({
        id: state.id,
        eventId: ev.id,
        params: state.params,
        summary,
        evidence,
        detections: state.detections.map(downsampleDetection),
      }));
      const grids = {
        maxEta: result.fields.maxEta,
        arrival: result.fields.arrival,
        dz: source.dz,
        bed: grid.bed,
        travel,
      };
      for (const [k, v] of Object.entries(grids)) {
        await fs.writeFile(path.join(dir, `${k}.f32`), Buffer.from(v.buffer, v.byteOffset, v.byteLength));
      }
    } catch {
      // persistence failure is non-fatal
    }
    if (dbId) {
      try {
        await db.tsunamiRun.update({
          where: { id: dbId },
          data: {
            status: 'ok',
            summaryJson: JSON.stringify({
              grid: summary.grid,
              nSteps: summary.nSteps,
              wallClockS: summary.wallClockS,
              massDriftRelative: summary.massDriftRelative,
              maxEtaM: summary.maxEtaM,
              comparisons: summary.comparisons.map((c) => ({ id: c.stationId, consistent: c.consistent })),
              evidenceLevel: evidence.level,
            }),
            finishedAt: new Date(),
            durationMs: Math.round(wallClockS * 1000),
          },
        });
      } catch {
        // non-fatal
      }
    }

    // evict old finished in-memory runs
    const finished = [...runs.values()].filter((r) => r.status === 'ok' || r.status === 'error');
    while (finished.length > MAX_COMPLETED_IN_MEMORY) {
      const oldest = finished.shift()!;
      if (oldest.id !== state.id) runs.delete(oldest.id);
    }
  } catch (err) {
    state.status = 'error';
    state.error = (err as Error).message;
    state.finishedAt = Date.now();
    if (dbId) {
      try {
        await db.tsunamiRun.update({
          where: { id: dbId },
          data: { status: 'error', error: (err as Error).message.slice(0, 500) },
        });
      } catch {
        // non-fatal
      }
    }
  }
}

export function getRunState(id: string): RunState | undefined {
  return runs.get(id);
}

export function listRuns(): Array<Pick<RunState, 'id' | 'eventId' | 'status' | 'startedAt'>> {
  return [...runs.values()].map((r) => ({ id: r.id, eventId: r.eventId, status: r.status, startedAt: r.startedAt }));
}
