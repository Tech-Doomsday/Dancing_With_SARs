/**
 * Shared, client-safe types for the engine-introspection feature.
 *
 * The nisar-hazard-engine (the Python half of this repository) publishes its
 * capability surface through per-pipeline `manifest.yaml` files under
 * `engine/pipelines/`. The web app reads those REAL files server-side
 * (see `src/lib/engine/registry.ts`) and serves them to this UI — nothing
 * here is hardcoded: if a manifest changes in the engine, the overview
 * changes with it.
 */

/** One engine pipeline manifest — field names mirror engine/registry.py's `Manifest`. */
export interface EnginePipeline {
  /** Pipeline code, e.g. "3.01". */
  code: string;
  /** Folder name, e.g. "earthquake_coseismic". */
  name: string;
  /** Sensor, e.g. "NISAR" or "Sentinel-1". */
  sensor: string;
  /** Model family from the manifest, e.g. "physics (Okada85) + optimizer". */
  modelType: string;
  /** Where the pipeline applies (routing rule). */
  applies: string;
  /** What shared measurements it consumes. */
  uses: string;
  usesCodes: string[];
  /** Pipeline-local preprocessing. */
  ownPrep: string;
  /** Pipeline-local external fetches. */
  ownFetch: string;
  fetchCodes: string[];
  /** Method steps, verbatim from the manifest. */
  method: string[];
  /** Output contract, verbatim. */
  output: string;
  /** Minimum epochs required (null = single pair is enough). */
  minEpochs: number | null;
  /** Status vocabulary declared by the manifest. */
  statusValues: string[];
  /**
   * True when the pipeline's verdict.py defines a `run()` entry point —
   * the engine registry's own implemented-test. A pipeline without run()
   * yields status NOT_MODELLED (engine/registry.py).
   */
  implemented: boolean;
}

/** Implementation reality of the engine's workflow stages, derived from source. */
export interface EngineStage {
  /** Stage number 1–5. */
  n: number;
  title: string;
  /** Honest, derived-from-source implementation state. */
  implemented: boolean;
  /** What the stage does (one line, from the engine README). */
  blurb: string;
}

export interface EngineStatus {
  /** Absolute path of the engine repository root that was introspected. */
  engineRoot: string;
  /** Resolved engine data dir (NISAR_DATA_DIR or <repo>/data). */
  dataDir: string;
  dataDirExists: boolean;
  /** SQLite retrieval state (data/state/retrieval.sqlite) — present once the poller has run. */
  retrievalDbExists: boolean;
  /** Content-addressed bundle store (data/bundles) — present once a bundle was persisted. */
  bundlesDirExists: boolean;
}

/** Response of GET /api/engine/pipelines. */
export interface EngineCatalogPayload {
  ok: true;
  engineRoot: string;
  pipelineCount: number;
  implementedCount: number;
  pipelines: EnginePipeline[];
  stages: EngineStage[];
}

/** Honest degradation payload when the engine source tree cannot be found. */
export interface EngineUnavailablePayload {
  ok: false;
  error: 'engine-source-not-found';
  message: string;
  hint: string;
}

export type EnginePipelinesResponse = EngineCatalogPayload | EngineUnavailablePayload;

/** Response of GET /api/engine/status. */
export interface EngineStatusResponse {
  ok: boolean;
  status?: EngineStatus;
  message?: string;
}
