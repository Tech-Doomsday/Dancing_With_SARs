/** Typed client for the engine API. All engine interaction goes through here.
 *  Zero science: transport only. */
import type {
  BundleListItem,
  Health,
  LayerResponse,
  PipelineInfo,
  RunState,
  RunSummary,
  EventItem,
  GranuleSearchResponse,
  TileInfo,
} from "./types";

import { engineBase } from "./engineUrl";

/** The engine base is read at CALL time (not module load) so a URL pasted into the nav's engine
 *  link applies immediately. Components that DISPLAY it use useEngineUrl() from ./engineUrl. */
export { engineBase };

import { engineJson, ENGINE_LONG_MS } from "./engineFetch";
export { EngineError, classifyEngineError } from "./engineFetch";

/** All engine calls go through engineFetch/engineJson (src/lib/engineFetch.ts): failures become
 *  an EngineError whose message names what failed and what to do — never a bare "Failed to fetch".
 *  HTTP errors keep the engine's own text verbatim as `message`. */
async function get<T>(path: string, opts?: { signal?: AbortSignal; timeoutMs?: number }): Promise<T> {
  return engineJson<T>(path, { signal: opts?.signal }, { timeoutMs: opts?.timeoutMs });
}

async function post<T>(path: string, body: unknown): Promise<T> {
  return engineJson<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const api = {
  health: () => get<Health>("/api/health"),
  pipelines: () =>
    get<{ ok: true; pipelines: PipelineInfo[]; implemented_count: number; declared_count?: number; modelled_count?: number }>("/api/pipelines"),
  bundles: () => get<{ ok: true; bundles: BundleListItem[] }>("/api/bundles"),
  events: (lon: number, lat: number, days = 30) =>
    get<{ ok: true; events: EventItem[]; source: string }>(
      `/api/events?lon=${lon}&lat=${lat}&days=${days}`,
    ),
  startRun: (bundleId: string, pipelines: string[], params?: Record<string, number>) =>
    post<{ ok: true; run_id: string }>("/api/runs", {
      bundle_id: bundleId,
      pipelines,
      params: params ?? {},
    }),
  runs: () => get<{ ok: true; runs: RunSummary[] }>("/api/runs"),
  /** The engine returns the run state FLAT: {ok, id, status, progress, error, results}. */
  run: (id: string, opts?: { slim?: boolean }) =>
    get<RunState & { ok: true }>(`/api/runs/${id}${opts?.slim ? "?slim=1" : ""}`),
  layer: (id: string, layer: string) =>
    get<LayerResponse>(`/api/runs/${id}/layers/${layer}`),
  /** D5: the pipeline-scoped layer route — layer names collide across pipelines. */
  pipelineLayer: (id: string, pipeline: string, layer: string) =>
    get<LayerResponse>(`/api/runs/${id}/pipelines/${encodeURIComponent(pipeline)}/layers/${encodeURIComponent(layer)}`),
  /** §5.2: the engine does the tiling — the UI never computes MGRS itself. */
  tileAt: (lon: number, lat: number) =>
    get<{ ok: true; tile: TileInfo }>(
      `/api/tile-at?lon=${lon}&lat=${lat}`,
    ),
  /** §7.7 URL restore: a pasted ?tile= link gets the tile's geometry/centre from the engine. */
  tileAtId: (tile: string) =>
    get<{ ok: true; tile: TileInfo }>(
      `/api/tile-at?tile=${encodeURIComponent(tile)}`,
    ),
  /** §5.3: a scan is a run — one polling path for everything. */
  startScan: (lon: number, lat: number, opts?: { as_of?: string; lookback_days?: number }) =>
    post<{ ok: true; run_id: string; tile: TileInfo }>("/api/scans", {
      lon,
      lat,
      ...(opts?.as_of ? { as_of: opts.as_of } : {}),
      ...(opts?.lookback_days ? { lookback_days: opts.lookback_days } : {}),
    }),
  /** §5.5: granule availability (metadata-only; live-engine-only, DEC5). */
  granules: (lon: number, lat: number, opts?: { product?: string; from?: string; to?: string }, signal?: AbortSignal) =>
    get<GranuleSearchResponse & { ok: true; pending?: boolean; job_id?: string }>(
      `/api/granules?lon=${lon}&lat=${lat}`
      + (opts?.product ? `&product=${encodeURIComponent(opts.product)}` : "")
      + (opts?.from ? `&from=${encodeURIComponent(opts.from)}` : "")
      + (opts?.to ? `&to=${encodeURIComponent(opts.to)}` : ""),
      { signal },
    ).then((r) => resolvePending(r, signal)),
};

/** The engine never holds a request open past ~25 s: a slow granule search answers 202 + job id and the
 *  result is polled here (every 2 s, up to `maxWaitMs`). A fast search is a plain 200 and returns at once. */
async function resolvePending<T>(first: T & { pending?: boolean; job_id?: string }, signal?: AbortSignal,
                                 maxWaitMs = 10 * 60_000): Promise<T> {
  let cur = first;
  const t0 = Date.now();
  while (cur && cur.pending && cur.job_id) {
    if (signal?.aborted) throw new DOMException("aborted", "AbortError");
    if (Date.now() - t0 > maxWaitMs) throw new Error("The granule search is still running on the engine after 10 minutes — try again later.");
    await new Promise((r) => setTimeout(r, 2000));
    cur = await get<T & { pending?: boolean; job_id?: string }>(`/api/granules/jobs/${encodeURIComponent(cur.job_id)}`, { signal });
  }
  return cur as T;
}

/** Self-scheduling poll: next request only after the previous settles (>=1.5 s).
 *  Poll errors are SURFACED through onError (the old loop swallowed them, which left
 *  the run page stuck on "Loading run…" when the API said 404/500). */
export function pollWhile<T>(
  fn: () => Promise<{ done: boolean; value: T }>,
  onTick: (value: T) => void,
  minIntervalMs = 1500,
  onError?: (message: string) => void,
): () => void {
  let stopped = false;
  const tick = async () => {
    while (!stopped) {
      const t0 = Date.now();
      try {
        const { done, value } = await fn();
        if (stopped) return;
        onTick(value);
        onError?.("");
        if (done) return;
      } catch (e) {
        if (stopped) return;
        onError?.(e instanceof Error ? e.message : String(e));
      }
      const wait = Math.max(minIntervalMs - (Date.now() - t0), minIntervalMs);
      await new Promise((r) => setTimeout(r, wait));
    }
  };
  void tick();
  return () => {
    stopped = true;
  };
}

// api object closed above; the Explorer's bbox search lives here as its own export (E8).
export interface GranuleBboxParams {
  west: number;
  south: number;
  east: number;
  north: number;
  level?: string;          // comma list from GUNW|GCOV|GOFF
  from?: string;
  to?: string;
  max?: number;
  fresh?: boolean;
}

/** E8: granule availability for a BOX (the Granule Explorer). Errors keep the engine's own text. */
export function granulesBbox(
  p: GranuleBboxParams,
  signal?: AbortSignal,
): Promise<import("./types").GranuleExplorerResponse & { ok: true }> {
  const q = new URLSearchParams({
    west: String(p.west),
    south: String(p.south),
    east: String(p.east),
    north: String(p.north),
  });
  if (p.level) q.set("level", p.level);
  if (p.from) q.set("from", p.from);
  if (p.to) q.set("to", p.to);
  if (p.max) q.set("max", String(p.max));
  if (p.fresh) q.set("fresh", "1");
  return get<import("./types").GranuleExplorerResponse & { ok: true; pending?: boolean; job_id?: string }>(
    `/api/granules?${q.toString()}`,
    { signal },
  ).then((r) => resolvePending(r, signal));
}
