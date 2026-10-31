/** API response types — mirrors the engine's FastAPI payloads only.
 *  Zero science: these are transport shapes (docs/IMPLEMENTATION_PLAN.md 10). */

export interface Health {
  ok: boolean;
  data_dir: string;
  data_dir_exists: boolean;
  bundles_dir: string;
  bundles_dir_exists: boolean;
  note: string;
  /** what the retrieval worker is doing now; null when idle (never touches the worker pool) */
  busy?: { kind: string; run_id: string | null; step: string | number | null; started_at: number | null } | null;
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
  /** ASF/CMR served a short page even after a retry; the listing may miss granules (not cached). */
  search_incomplete?: boolean;
  dropped_older: number;
  flags: { code: string; severity: string; step: string; message: string }[];
}

// ---------------------------------------------------------------------------------------------
// E8 — the Granule Explorer's bbox-based /api/granules types. These sit ALONGSIDE the legacy
// per-tile GranuleSearchResponse above (the old Data tab keeps its own shape; do not merge).
// Field names are the engine's (engine/api/granules_bbox.py).
// ---------------------------------------------------------------------------------------------

/** One granule: filename-decoded fields (only where verified — see engine/common/granule_id.py),
 *  provider-side facts (ASF/CMR via GranuleRef.extra), and per-granule honesty flags. An absent
 *  fact is null — never guessed. */
export interface ExplorerGranule {
  id: string;
  product: string | null;
  stack: string;
  direction: "A" | "D" | null;
  track: number | null;
  frame: number | null;
  start: string | null;
  stop: string | null;
  ref_time: string | null;
  sec_time: string | null;
  baseline_days: number | null;
  cycle_ref: number | null;
  cycle_sec: number | null;
  polarization: string | null;
  range_bandwidth_mhz: number | null;
  collection: string | null;
  crid: string | null;
  version: string | null;
  orbit_accuracy: { asf: string | null; filename: string | null };
  size_bytes: number | null;
  footprint: [number, number][] | null;
  flags: string[];
}

/** One stack = one (direction, relative orbit), the engine's P01 grouping. Day gaps and epochs
 *  are engine-computed (DEC13); nominal_repeat_days is null unless the engine supplies it. */
export interface ExplorerStack {
  key: string;
  direction: "A" | "D" | null;
  track: number | null;
  n_granules: number;
  n_epochs: number;
  epochs: string[];
  gaps_days: number[];
  min_gap_days: number | null;
  median_gap_days: number | null;
  max_gap_days: number | null;
  nominal_repeat_days: number | null;
  first: string | null;
  last: string | null;
}

export interface GranuleExplorerResponse {
  ok: true;
  bbox: [number, number, number, number];
  window: { t0: string; t1: string };
  n_total: number;
  truncated: boolean;
  degraded: boolean;
  /** ASF/CMR reported an incomplete result — the list may miss granules and was NOT cached */
  search_incomplete?: boolean;
  search_warnings?: string[];
  search_budget: number;
  generated_at: string;
  granules: ExplorerGranule[];
  stacks: ExplorerStack[];
  groups?: BackgroundGroup[];
  epoch_merge_minutes?: number;
}

// ---------------------------------------------------------------------------------------------
// UI-build-2 §4 — acquisition groups (engine-computed; the UI only draws). One group per distinct
// acquisition; variants[] are the resolution variants (bandwidth/CRID/version/collection/pol).
// ---------------------------------------------------------------------------------------------

export interface BackgroundGroup {
  key: string;
  stack: string;
  product: string | null;
  kind: "pair" | "single" | "unknown";
  ref_time: string | null;
  sec_time: string | null;
  start: string | null;
  baseline_days: number | null;
  n_variants: number;
  grouped: boolean;
  variant_facts: string[];
  granule_ids: string[];
  flags: string[];
  variants: ExplorerGranule[];
}

/** GET /api/background/granule — decoded fields + layer inventory + the granule's group. */
export interface GranuleDetailResponse {
  ok: true;
  id: string;
  granule: ExplorerGranule;
  decoded: Record<string, unknown>;
  group: BackgroundGroup | null;
  grouped: boolean;
  inventory: {
    grids: {
      path: string; key: string; epsg: number | null;
      nx: number; ny: number; dx: number | null; dy: number | null;
      extras: { name: string; size: number }[];
      layers: { path: string; key: string; shape: number[]; dtype: string;
                chunks: number[] | null; readable: boolean; why_not: string | null;
                requested_by_scan?: boolean }[];
    }[];
    source: string;
    n_layers: number;
    scan_request_regex?: string | null;
    n_layers_requested_by_scan?: number;
  } | null;
  inventory_note: string | null;
  footprint: [number, number][] | null;
}

export interface PreviewMeta {
  id: string;
  layer: string;
  cmap: string;
  lo: number;
  hi: number;
  shown_stride: number;
  shown_shape: [number, number];
  epsg: number | null;
  clip_bbox_lonlat: [number, number, number, number] | null;
  requested_bbox: [number, number, number, number];
  slice_note?: string | null;
  stats: { shape: number[]; dtype: string; min: number | null; max: number | null;
           mean: number | null; p2: number | null; p50: number | null; p98: number | null;
           nan_fraction: number; n_bytes: number };
  wire_bytes?: Record<string, number>;
  clip_source?: string;
  max_px_cap?: number;
  cache_hit: boolean;
  generated_at?: string;
}

export interface StackPairResponse {
  ok: true;
  id: string;
  product: string;
  bbox_used: [number, number, number, number];
  epoch_merge_minutes: number;
  ref: { epoch: string; granules: ExplorerGranule[] };
  sec: { epoch: string; granules: ExplorerGranule[] };
  note: string;
}

/** §5 — the preprocessing payload (traced run or cached-bundle records). */
export interface PreprocessingResponse {
  ok: true;
  run: { id: string | null; status: string | null; tile: string | null;
         kind: string | null; bundle_id: string | null };
  trace: PreprocessingTrace | null;
  trace_arrays?: boolean;
  empty_reason?: string;
  note?: string;
}

export interface TraceArrayMeta {
  aid: string;
  path: string;
  label: string;
  group: string;
  step: string;
  first_step: string;
  shape: number[];
  dtype: string;
  kind: string;
  stats: Record<string, unknown>;
  stride: number;
  epsg: number | null;
  extent: number[] | null;
  note?: string | null;
  fill?: number | null;
  categories?: Record<string, number> | null;
  thumb_shape?: number[];
}

export interface PreprocessingTrace {
  steps: {
    code: string; name: string; status: string; seconds: number; message: string;
    new_flags: { code: string; severity: string; message: string }[];
    new_assumptions: string[];
    n_arrays_total: number;
    reads?: string[];
    arrays: TraceArrayMeta[];
  }[];
  arrays: Record<string, TraceArrayMeta>;
  n_arrays?: number;
  bundle_summary?: Record<string, unknown>;
  handoff?: HandoffFeed;
  flags?: { severity: string; step: string; code: string; message: string }[];
  assumptions?: string[];
  source?: string;
}

export interface HandoffRow {
  name: string;
  code: string;
  sensor: string;
  min_epochs: number | null;
  routing: { route: string; reason?: string; reasons?: string[] } | Record<string, unknown> | null;
  registry_status: string;
  reasons: string[];
  feed: string;
  uses: { code: string; name: string; state: string; why: string }[];
  own_fetch: { code: string; name: string; state: string; why: string }[];
  missing_uses: string[];
  own_missing: string[];
  implemented: boolean;
  verdict_state: string;
}

export interface HandoffFeed {
  rows: HandoffRow[];
  tally: Record<string, number>;
  n_implemented: number;
}

/** §6 — one pipeline's input/process/output for one run. */
export interface PipelineDetailResponse {
  ok: true;
  run: { id: string; status: string | null; tile: string | null; kind: string | null;
         bundle_id: string | null };
  manifest: {
    name: string; code: string; sensor: string; applies: string;
    uses_codes: string[]; fetch_codes: string[]; min_epochs: number | null;
    status_values: string[]; execution: string;
    method: string[]; own_prep: string[]; own_fetch: string[]; assumptions: string[];
  };
  routing: Record<string, unknown> | null;
  feed: HandoffRow | null;
  bundle_summary: Record<string, unknown> | null;
  bundle_error: string | null;
  result: PipelineResult;
  result_missing: null;
  process: {
    runtime_s: number | null;
    diagnostics: Record<string, unknown> | null;
    detail_steps: unknown;
    warnings: string[];
    versions: Record<string, unknown>;
  };
}
