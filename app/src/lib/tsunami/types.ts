/**
 * Tsunami domain types — MOTUS tsunami capability.
 *
 * Design rules (mirrors the InSAR side of the app):
 *  - Every external datum carries a Provenance record (provider, url, retrieved
 *    time, CRS, datum, processing, quality).
 *  - Measured observations, simulated outputs and curated citations are kept in
 *    distinct types and never conflated.
 *  - Missing data is an explicit state, not a default number.
 */

/**
 * Provenance for tsunami-side data (earthquakes, bathymetry, ocean sensors).
 * Captures everything the honesty contract needs: provider, identifier,
 * retrieval time, observation window, units, CRS + vertical datum, processing
 * steps and quality — per-dataset, never global.
 */
export interface TsunamiProvenance {
  datasetId: string;
  provider: string;
  productId: string;
  productType: string;
  sourceUrl: string;
  /** ISO-8601 UTC when this server retrieved the data */
  retrievedAt: string;
  /** observation window covered by the data (ISO-8601 UTC) */
  observationStart: string;
  observationEnd: string;
  units: string;
  crs: string;
  verticalDatum: string;
  /** processing steps applied here, in order */
  processing: string[];
  dataClass: 'measured' | 'modelled' | 'curated-link';
  qualityNotes: string[];
  license?: string;
}

/* ------------------------------------------------------------------ */
/* Grids                                                               */
/* ------------------------------------------------------------------ */

/**
 * Regular lat/lon grid. Row 0 = southernmost, column 0 = westernmost,
 * cell-centred registration at exact arc-minute multiples (ETOPO1 native).
 * bed elevations are metres, POSITIVE UP, vertical datum MSL (ETOPO1).
 */
export interface BathymetryGrid {
  /** cell size in arc minutes (1, 2 or 4) */
  resArcmin: number;
  /** latitude of row 0 centre (deg) */
  lat0: number;
  /** longitude of col 0 centre (deg) */
  lon0: number;
  /** number of columns (lon direction) */
  nx: number;
  /** number of rows (lat direction) */
  ny: number;
  /** bed elevation [m, positive up] (row-major, j*nx+i) */
  bed: Float32Array;
  provenance: TsunamiProvenance;
}

/** Uniform rectangular source deformation field on the simulation grid. */
export interface SourceField {
  /** combined vertical seafloor displacement incl. slope correction [m] */
  dz: Float32Array;
  /** initial free-surface elevation after Kajiura filtering [m] */
  eta0: Float32Array;
  /** max |dz| (m) */
  maxAbsDz: number;
  /** description of the source model used (for the UI) */
  sourceKind: 'usgs-finite-fault' | 'usgs-mt-uniform' | 'manual';
  /** summed seismic moment (N·m) when the source provides it */
  momentNM: number | null;
  /** per-subfault or single-fault description */
  detail: string;
  /** processing notes for the run record */
  notes: string[];
}

/* ------------------------------------------------------------------ */
/* Events                                                             */
/* ------------------------------------------------------------------ */

export interface TsunamiEvent {
  /** canonical id used by this app (e.g. 'tohoku-2011') */
  id: string;
  /** USGS ComCat event id */
  usgsId: string;
  title: string;
  blurb: string;
  /** ISO origin time (UTC) */
  time: string;
  magnitude: number;
  /** hypocentre */
  lat: number;
  lon: number;
  depthKm: number;
  place: string;
  /** moment-tensor nodal plane if known (live-fetched when possible) */
  nodalPlane?: { strike: number; dip: number; rake: number };
  /** does USGS publish a finite-fault model for this event? */
  hasFiniteFault: boolean;
  /** curated official reference (never auto-fetched as "confirmation") */
  officialUrl: string | null;
  officialSource: string | null;
  /** curated validation note (what this event is used for) */
  note: string;
}

/* ------------------------------------------------------------------ */
/* Observations                                                        */
/* ------------------------------------------------------------------ */

export interface DartStation {
  id: string;
  lat: number;
  lon: number;
  name: string;
  region: string;
}

export type ObsAvailability =
  | { state: 'available'; note?: string }
  | { state: 'missing'; reason: string };

export interface TimeSeries {
  /** epoch milliseconds, ascending */
  t: Float64Array;
  /** raw measured value (bottom-pressure height for DART, m) */
  v: Float32Array;
}

/** De-tided residual + detection result for one station/event. */
export interface DetectionResult {
  stationId: string;
  /** provenance of the underlying observation */
  provenance: TsunamiProvenance;
  /** analysis window (epoch ms) */
  t: Float64Array;
  /** de-tided residual (m) */
  resid: Float32Array;
  /** pre-event noise sigma (m) */
  sigmaNoise: number;
  /** epoch ms of the first k·sigma crossing after origin-15min, or null */
  triggerMs: number | null;
  /** minutes after the event origin (negative = before origin) */
  arrivalMin: number | null;
  /** pre-origin excursion flagged (seismic phase on the pressure record) */
  preOriginExcursion: boolean;
  /** max |residual| in [trigger, trigger+3h] (m), null if no trigger */
  maxAmplitudeM: number | null;
  /** dominant period estimate (min) from zero-crossings in the first hour */
  dominantPeriodMin: number | null;
  /** number of valid samples in the analysis window */
  nSamples: number;
  /** any caveats discovered (gaps, cadence changes) */
  caveats: string[];
  availability: ObsAvailability;
}

/* ------------------------------------------------------------------ */
/* Simulation                                                          */
/* ------------------------------------------------------------------ */

export interface SimParams {
  /** ETOPO1 stride: 1, 2 or 4 arc-minutes per solver cell */
  resArcmin: number;
  /** simulated hours after origin */
  simHours: number;
  /** Manning n (s·m^-1/3); 0 disables friction */
  manningN: number;
  /** solver order: 1 = HLL forward Euler, 2 = MUSCL + SSP-RK2 */
  order: 1 | 2;
  /** model arrival threshold for the arrival-time field (m, NTHMP 0.05) */
  arrivalThresholdM: number;
  /** half-size of the square-ish domain in degrees (lat) */
  domainHalfDeg: number;
  /** absorbing (sponge) layers at open boundaries; off = reflective walls */
  spongeEnabled: boolean;
}

export const DEFAULT_SIM_PARAMS: SimParams = {
  resArcmin: 2,
  simHours: 4,
  manningN: 0.025,
  order: 2,
  arrivalThresholdM: 0.05,
  // 5° keeps the primary near-field DART stations of the curated events
  // inside the domain and outside the boundary sponge
  domainHalfDeg: 5,
  spongeEnabled: true,
};

export type RunStatus = 'queued' | 'running' | 'ok' | 'error';

export interface GaugeSeries {
  stationId: string;
  lat: number;
  lon: number;
  /** minutes after origin */
  tMin: Float32Array;
  /** modelled free-surface elevation (m) */
  eta: Float32Array;
}

export interface StationComparison {
  stationId: string;
  /** observed values (null = no detection) */
  obsArrivalMin: number | null;
  obsMaxAmplitudeM: number | null;
  obsSigmaM: number | null;
  /** simulated values (null = station outside domain / never above threshold) */
  simArrivalMin: number | null;
  simMaxAmplitudeM: number | null;
  /** diagnostics */
  arrivalResidualMin: number | null;
  amplitudeRatio: number | null;
  consistent: boolean | null;
  note: string;
}

export interface TsunamiRunSummary {
  eventId: string;
  params: SimParams;
  grid: { nx: number; ny: number; lat0: number; lon0: number; resArcmin: number };
  /** simulation wall + model time */
  wallClockS: number;
  simDurationS: number;
  nSteps: number;
  minDtS: number;
  maxDtS: number;
  /** relative mass drift over the whole run */
  massDriftRelative: number;
  maxEtaM: number;
  sourceMaxAbsDzM: number;
  comparisons: StationComparison[];
  gauges: GaugeSeries[];
  provenance: TsunamiProvenance[];
  solverNotes: string[];
}

/* ------------------------------------------------------------------ */
/* Evidence classification                                             */
/* ------------------------------------------------------------------ */

export type EvidenceLevel =
  | 'insufficient'
  | 'none-detected'
  | 'possible'
  | 'consistent'
  | 'confirmed-official';

export interface EvidenceAssessment {
  level: EvidenceLevel;
  headline: string;
  /** human-readable criteria that fired, for the evidence panel */
  reasons: string[];
  caveats: string[];
  /** the evidence types backing this (never presented as independent) */
  contributing: Array<'seismic-screen' | 'ocean-sensor' | 'simulation' | 'official-link'>;
}
