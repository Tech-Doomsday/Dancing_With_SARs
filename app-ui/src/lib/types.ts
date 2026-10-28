/** API response types — mirrors the engine's FastAPI payloads only.
 *  Zero science: these are transport shapes (docs/IMPLEMENTATION_PLAN.md 10). */

export interface Health {
  ok: boolean;
  data_dir: string;
  data_dir_exists: boolean;
  bundles_dir: string;
  bundles_dir_exists: boolean;
  note: string;
}

export interface PipelineInfo {
  name: string;
  code: string;
  sensor: string;
  applies: string;
  uses_codes: string[];
  fetch_codes: string[];
  min_epochs: number | null;
  status_values: string[];
  implemented: boolean;
  execution?: string;
  needs_sar?: boolean;
}

export interface BundleListItem {
  bundle_id: string;
  tile: string | null;
  epsg: number | null;
  center_lonlat: [number, number] | null;
  bbox_lonlat: [number, number, number, number] | null;
  epoch: string | null;
  status: string | null;
  created_at: string | null;
}

export interface RunSummary {
  id: string;
  status: string;
}

export interface EvidenceItem {
  id?: string;
  kind: string;
  /** one self-contained sentence — the engine's field is `claim` (never `text`) */
  claim: string;
  support?: Record<string, unknown> | null;
  level?: string;
  source_ref?: string | null;
  [k: string]: unknown;
}

export interface PipelineResult {
  pipeline: string;
  tile: string;
  epoch: string | null;
  raw_p: number | null;
  status: string;
  summary?: string;
  evidence: EvidenceItem[];
  layers: string[];
  data_used: string[];
  references_used: string[];
  runtime_s: number;
  warnings: string[];
  min_epochs_met: boolean | null;
  bundle_id: string | null;
  detail: Record<string, unknown>;
}

/** What the engine reports about mirroring a finished run into the Supabase cache. */
export interface CacheReport {
  enabled: boolean;
  written: number;
  skipped: { pipeline: string | null; reason: string }[];
  failed: string[];
  /** why the cache is off, when enabled is false */
  reason?: string;
}

export interface RunState {
  id: string;
  status: "queued" | "running" | "ok" | "error";
  progress: Record<string, unknown>;
  error: string | null;
  results: PipelineResult[];
  /** absent on runs loaded from the store after an engine restart */
  cache?: CacheReport;
}

/** One cached verdict, light columns only (no result body, no layer grids). */
export interface CachedSummary {
  tile: string;
  pipeline: string;
  bundle_id: string;
  epoch: string | null;
  status: string;
  tier: string | null;
  raw_p: number | null;
  summary: string | null;
  run_id: string | null;
  computed_at: string;
  first_computed_at: string;
}

/** A cached verdict with its full result row (the PipelineResult the engine produced). */
export interface CachedVerdict extends CachedSummary {
  result: PipelineResult;
}

export interface LayerPayload {
  name: string;
  dtype: "<f4" | "<u1";
  shape: number[];
  data_b64: string;
  units: string;
  colormap: string;
  vmin: number | null;
  vmax: number | null;
  note: string;
  /** [W, S, E, N] core-grid corners, server-computed (plan 6.4) */
  bounds_lonlat?: [number, number, number, number] | null;
}

export interface LayerResponse {
  ok: true;
  layer: LayerPayload;
  pipeline: string;
}

export interface EventItem {
  id: string;
  magnitude: number | null;
  place: string;
  time: string;
  lat: number;
  lon: number;
  depth_km: number | null;
}
