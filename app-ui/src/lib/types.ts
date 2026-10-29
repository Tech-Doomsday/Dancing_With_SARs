/** API response types — mirrors the engine's FastAPI payloads only.
 *  Zero science: these are transport shapes (docs/IMPLEMENTATION_PLAN.md 10). */

export interface Health {
  ok: boolean;
  data_dir: string;
  data_dir_exists: boolean;
  bundles_dir: string;
  bundles_dir_exists: boolean;
  note: string;
  scans_enabled?: boolean;
  earthdata_credentials?: "present" | "absent";
  cache_write?: { status: "enabled" | "disabled"; reason: string | null };
  granule_search?: { status: "enabled" | "disabled"; reason: string | null };
}

/** §5.2: the engine's answer for the MGRS tile that contains a point. */
export interface TileInfo {
  id: string;
  epsg: number;
  size_m: number;
  bbox_lonlat: [number, number, number, number];
  center_lonlat: [number, number];
  polygon_lonlat: [number, number][];
}

/** §5.4: one detected change, with geometry the engine computed. */
export interface Detection {
  id: string;
  label: string;
  kind: string;
  layer: string | null;
  centroid_lonlat: [number, number];
  bbox_lonlat: [number, number, number, number];
  area_km2: number;
  polarity: "toward" | "away" | "mixed" | null;
  metrics: Record<string, { value: number; units: string }>;
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
  /** §5.3: which kind of run this is ("scan" = started from a location) */
  kind?: "scan" | "bundle";
  tile?: string;
  bundle_id?: string;
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
  /** 0002 location columns (present when the engine wrote them) */
  center_lon?: number | null;
  center_lat?: number | null;
  bbox_lonlat?: [number, number, number, number] | null;
}

/** The owner-curated featured area (DEC4). The tile is engine-resolved; entries start empty. */
export interface FeaturedArea {
  slug: string;
  title: string;
  blurb: string | null;
  tile: string;
  center_lon: number;
  center_lat: number;
  zoom: number | null;
  sort_order: number;
  created_at: string;
}

/** A cached verdict with its full result row (the PipelineResult the engine produced). */
export interface CachedVerdict extends CachedSummary {
  result: PipelineResult;
}

export interface LayerCategory {
  value: number;
  label: string;
  tone: "positive" | "negative" | "neutral";
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
  /** [W, S, E, N] core-grid corners, server-computed (plan 6.4, flat form) */
  bounds_lonlat?: [number, number, number, number] | null;
  /** categorical <u1 layers: the engine's own labels for the swatches */
  categories?: LayerCategory[] | null;
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


// ---------------------------------------------------------------------------------------------
// §5.5 granule availability (the Data tab) — mirrors the engine's Resolved shape exactly;
// field names are the engine's (PairGroup / EpochGroup), not the UI's.
// ---------------------------------------------------------------------------------------------

/** One GUNW pair group: ref/secondary acquisition times, the temporal baseline in days, and the
 *  footprint of the group's FIRST granule (documented approximation the engine reports). */
export interface GunwPairGroup {
  ref_time: string | null;
  sec_time: string | null;
  dt_days: number | null;
  flipped: boolean;
  resolved: boolean;
  granule_ids: string[];
  frame: number | null;
  footprint: [number, number][] | null;
}

/** One GCOV epoch: a near-instantaneous acquisition, not a span. */
export interface GcovEpochGroup {
  time: string | null;
  granule_ids: string[];
  frame: number | null;
  footprint: [number, number][] | null;
}

/** One stack = one (direction, relative orbit), exactly the way P01 groups them. */
export interface GranuleStack {
  direction: string | null;
  track: number | null;
  gunw: GunwPairGroup[];
  gcov: GcovEpochGroup[];
  goff?: GunwPairGroup[];
}

export interface GranuleSearchResponse {
  ok: true;
  tile: TileInfo;
  window: { t0: string; t1: string };
  stacks: Record<string, GranuleStack>;
  search_errors: Record<string, string>;
  search_degraded: boolean;
  dropped_older: number;
  flags: { code: string; severity: string; step: string; message: string }[];
}
