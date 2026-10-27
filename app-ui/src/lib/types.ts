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
  kind: string;
  text: string;
  [k: string]: unknown;
}

export interface PipelineResult {
  pipeline: string;
  tile: string;
  epoch: string | null;
  raw_p: number | null;
  status: string;
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

export interface RunState {
  id: string;
  status: "queued" | "running" | "ok" | "error";
  progress: Record<string, unknown>;
  error: string | null;
  results: PipelineResult[];
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
