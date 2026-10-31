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
import { engineFetch, EngineError, messageFor } from "./engineError";
export { EngineError };

/** The engine base is read at CALL time (not module load) so a URL pasted into the nav's engine
 *  link applies immediately. Components that DISPLAY it use useEngineUrl() from ./engineUrl. */
export { engineBase };

/** FastAPI wraps HTTPException payloads under `detail`; the engine's own error
 *  convention nests {ok: false, error} there. Unwrap every shape in one place. */
function extractError(body: unknown, res: Response): string {
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    if (typeof b.error === "string") return b.error;
    if (b.detail && typeof b.detail === "object") {
      const d = b.detail as Record<string, unknown>;
      if (typeof d.error === "string") return d.error;
    }
    if (typeof b.detail === "string") return b.detail;
  }
  return `${res.status} ${res.statusText}`;
}

async function get<T>(path: string, opts?: { signal?: AbortSignal }): Promise<T> {
  const res = await engineFetch(engineBase(), path, { cache: "no-store", signal: opts?.signal });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new EngineError("http", messageFor("http", { base: engineBase(), detail: extractError(body, res) }), { status: res.status });
  }
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await engineFetch(engineBase(), path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new EngineError("http", messageFor("http", { base: engineBase(), detail: extractError(json, res) }), { status: res.status });
  return json as T;
}

export const api = {
  health: () => get<Health>("/api/health"),
  pipelines: () =>
    get<{ ok: true; pipelines: PipelineInfo[]; implemented_count: number }>("/api/pipelines"),
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
    get<GranuleSearchResponse & { ok: true }>(
      `/api/granules?lon=${lon}&lat=${lat}`
      + (opts?.product ? `&product=${encodeURIComponent(opts.product)}` : "")
      + (opts?.from ? `&from=${encodeURIComponent(opts.from)}` : "")
      + (opts?.to ? `&to=${encodeURIComponent(opts.to)}` : ""),
      { signal },
    ),
};

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
  return get<import("./types").GranuleExplorerResponse & { ok: true }>(
    `/api/granules?${q.toString()}`,
    { signal },
  );
}

// ---- Stage 3: Retrieve (POST/GET/cancel). Every call answers at once; the UI polls. ----
export type RetrievalGranuleState = "queued" | "fetching" | "stored" | "skipped" | "failed" | "cancelled";
export interface RetrievalGranule {
  granule_id: string;
  product: string;
  start: string | null;
  track: number | null;
  direction: string | null;
  state: RetrievalGranuleState;
  source: string | null;      // how the clip was obtained (net / clip-cache ...), as the engine reports it
  seconds: number | null;
  error: string | null;
  clip_key: string | null;
  bytes_done: number | null;  // null = not measured (never estimated)
  bytes_planned: number | null;
}
export interface Retrieval {
  ok: true;
  id: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  stage: string;
  tile: string | null;
  bbox: [number, number, number, number];
  products: string[];
  lookback_days: number;
  max_granules: number | null;
  started_at: number;
  finished_at: number | null;
  error: string | null;
  flags: string[];
  granules: RetrievalGranule[];
  counts: Record<RetrievalGranuleState, number>;
  total: number;
}
export interface RetrievalRequest {
  bbox: [number, number, number, number];
  products: string[];
  lookback_days: number;
  max_granules?: number;
}
export const retrievals = {
  start: (body: RetrievalRequest) =>
    post<{ ok: true; retrieval_id: string; tile: string | null; bbox: number[] }>("/api/retrievals", body),
  get: (id: string, signal?: AbortSignal) => get<Retrieval>(`/api/retrievals/${encodeURIComponent(id)}`, { signal }),
  cancel: (id: string) => post<{ ok: true; message: string }>(`/api/retrievals/${encodeURIComponent(id)}/cancel`, {}),
};
