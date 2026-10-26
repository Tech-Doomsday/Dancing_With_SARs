/**
 * Shared scientific types for the MOTUS Earth-change intelligence pipeline.
 *
 * All rasters are row-major Float32Arrays with shape [height, width].
 * Grid axes follow the NISAR GUNW product convention:
 *   - xCoordinates increase eastward
 *   - yCoordinates decrease with row index (row 0 is the northern edge,
 *     ySpacing < 0), matching the native product layout.
 */

/** Grid georeferencing metadata (map-projection coordinates). */
export interface GridMeta {
  /** Number of columns */
  width: number;
  /** Number of rows */
  height: number;
  /** Easting of the first column centre (projection units, metres) */
  xStart: number;
  /** Column spacing in metres (positive) */
  xSpacing: number;
  /** Northing of the first row centre (projection units, metres) */
  yStart: number;
  /** Row spacing in metres (negative: rows advance southward, NISAR convention) */
  ySpacing: number;
  /** EPSG code of the projection (e.g. 32611 = WGS84 / UTM zone 11N) */
  epsg: number;
}

/** A float raster on a georeferenced grid. NaN marks no-data. */
export interface Raster {
  meta: GridMeta;
  data: Float32Array;
}

/** Radar observing geometry for one interferometric pair. */
export interface RadarGeometry {
  /** Radar wavelength in metres (L band ~0.236 m for NISAR L-SAR) */
  wavelength: number;
  /** Local incidence angle in degrees (surface-to-LOS) */
  incidenceAngleDeg: number;
  /** Heading angle of the satellite ground track in degrees clockwise from north */
  headingDeg: number;
  /** Ascending or descending pass */
  passDirection: 'ascending' | 'descending';
  /** Look direction (NISAR L-SAR is right-looking) */
  lookDirection: 'right' | 'left';
}

/** Provenance record for any dataset used by the system. */
export interface ProvenanceRecord {
  /** Short machine key of the dataset */
  datasetId: string;
  /** Data provider organisation */
  provider: string;
  /** Official product identifier / granule ID */
  productId: string;
  /** Product type, e.g. "NISAR L2 GUNW" */
  productType: string;
  /** Platform/sensor, e.g. "ALOS PALSAR (processed by NISAR SDS)" */
  platform: string;
  /** Acquisition time of the reference (first) scene, ISO-8601 UTC */
  referenceTime: string;
  /** Acquisition time of the secondary (second) scene, ISO-8601 UTC */
  secondaryTime: string;
  /** Temporal baseline in days */
  temporalBaselineDays: number;
  /** Processing time of the product, ISO-8601 UTC if known */
  processingTime?: string;
  /** Processing software + version, if known */
  processingSoftware?: string;
  /** Whether the observation is real (measured), synthetic (modelled), or derived */
  dataClass: 'real' | 'synthetic' | 'derived';
  /** Source URL or catalog reference for the original product */
  sourceUrl: string;
  /** DOI if available */
  doi?: string;
  /** Human-readable notes on access restrictions */
  accessNotes?: string;
  /** Quality flags copied from the product (applied corrections, fill fractions) */
  qualityNotes?: string[];
}

/** Analysis-ready dataset loaded by the pipeline. */
export interface ScienceDataset {
  id: string;
  slug: string;
  title: string;
  /** One-line plain-language summary shown in the UI */
  blurb: string;
  /** 'quantitative': full phase→displacement pipeline available.
   *  'evidence': real metadata + official browse imagery only (product
   *  download requires credentials); attribution layer still runs. */
  mode: 'quantitative' | 'evidence';
  provenance: ProvenanceRecord;
  geometry: RadarGeometry;
  /** Unwrapped interferometric phase in radians (NaN = no data) */
  phase?: Raster;
  /** Coherence magnitude 0..1 (NaN = no data) */
  coherence?: Raster;
  /** Connected-component labels from unwrapping (0 = invalid) */
  components?: Uint16Array;
  /** Number of independent looks used to form the interferogram */
  numberOfLooks: number;
  /**
   * Ionospheric phase-screen statistics from the product (all in radians).
   *
   * SEMANTICS (verified against the ASF NISAR GUNW guide, retrieved
   * 2026-10-21): the screen is supplied by the producer but NOT applied to
   * the stored unwrappedPhase. Therefore:
   *   - screenVariation*: the spatial variation of the unapplied screen —
   *     the ionospheric signal actually present in the measurement (after
   *     constant referencing);
   *   - uncertaintyMedian: the error of the screen estimate — how poorly
   *     the true ionosphere is known even if a user applied the screen.
   * The pipeline raises the detection floor to at least the larger of the
   * two (converted to metres of LOS range change).
   */
  ionoScreen?: {
    /** Robust (MAD-based) sigma of the screen itself, radians */
    variationMadRad: number;
    /** Standard deviation of the screen itself, radians */
    variationStdRad: number;
    /** Median |screen uncertainty layer|, radians */
    uncertaintyMedianRad: number;
  };
  /** Footprint polygon [lon, lat][] in WGS84 for map display */
  footprintPolygon: [number, number][];
  /** Approximate dataset centre in WGS84 for map focus */
  center?: { lat: number; lon: number };
  /** True when this dataset is a synthetic validation fixture */
  isSynthetic?: boolean;
  /** For synthetic datasets: analytic ground-truth LOS displacement (m) */
  truth?: Raster;
  /** Model prediction (Okada forward from USGS source parameters), labelled */
  prediction?: Raster;
  /**
   * Dataset-suggested analysis parameter overrides used as the starting
   * point for a run (the UI exposes them as pre-selected defaults; every
   * value remains user-adjustable). Example: a heavily decorrelated scene
   * suggests a lower coherence threshold than the global default.
   */
  suggestedParams?: Partial<AnalysisParams>;
}

/** Parameters controlling one pipeline run. */
export interface AnalysisParams {
  /** Coherence threshold below which pixels are masked */
  coherenceThreshold: number;
  /** Reference-area strategy for removing the arbitrary 2πk offset */
  referenceStrategy: 'farField' | 'fullFrameMedian';
  /** Fraction of frame margin treated as far field for 'farField' strategy */
  farFieldMargin: number;
  /** Remove a planar ramp fitted over the reference region (advanced) */
  detrend: boolean;
  /** Minimum |displacement| considered detectable (metres) */
  detectionFloor: number;
  /** Significance multiplier on reference sigma */
  significanceK: number;
}

export const DEFAULT_PARAMS: AnalysisParams = {
  coherenceThreshold: 0.5,
  referenceStrategy: 'farField',
  farFieldMargin: 0.12,
  detrend: false,
  detectionFloor: 0.01,
  significanceK: 3,
};

/** Per-pixel transport grid sent to the browser. */
export interface TransportGrid {
  width: number;
  height: number;
  /** Displacement in metres, base64 little-endian Float32 (NaN = masked) */
  displacement: string;
  /** Coherence 0..1, base64 Float32 */
  coherence: string;
  /** 1 = valid measurement, 0 = masked, base64 Uint8 */
  mask: string;
  /** One-sigma per-pixel uncertainty in metres, base64 Float32 */
  sigma: string;
  /**
   * Detected-change mask (2026-10-24): 1 = the exact server-side detection
   * rule (|d| > max(k·σ, floor)) passes for the majority of contributing
   * full-resolution pixels, 0 = not detected. Base64 Uint8. Optional because
   * prediction/velocity transports carry no detection rule — the client
   * decodes a zero-filled grid when absent.
   */
  detected?: string;
  meta: GridMeta;
}

/** Summary statistics of the measured change. */
export interface ChangeSummary {
  /** Pixels passing quality mask */
  validPixelCount: number;
  validFraction: number;
  /** Coherence statistics over valid pixels */
  coherenceMean: number;
  coherenceMedian: number;
  /** Area of one pixel (km²) */
  pixelAreaKm2: number;
  /** Total analysed area (km²) */
  analysedAreaKm2: number;
  /** Reference offset removed (metres of LOS range) */
  referenceOffsetM: number;
  /** Robust sigma of the reference region (metres) */
  referenceSigmaM: number;
  /** Area where |d| exceeds detection threshold (km²) */
  affectedAreaKm2: number;
  /**
   * Systematic floor from the product's ionospheric screen statistics
   * (metres of LOS range change). Because the screen is supplied but NOT
   * applied by the producer, this is the LARGER of (a) the unapplied
   * screen's own spatial variation and (b) the screen estimate's
   * uncertainty — i.e. a bound on the unresolved ionospheric contamination
   * of the apparent LOS range change.
   */
  systematicIonoFloorM: number;
  /** Which of the two screen statistics set the floor ('variation'|'uncertainty'|'none') */
  ionoFloorBasis: 'variation' | 'uncertainty' | 'none';
  /**
   * Machine-readable correction status of the consumed layers. The phase
   * corrections recorded here are the authoritative product semantics
   * (ASF GUNW guide): flattening applied; ionosphere/troposphere NOT
   * removed from the interferometric data.
   */
  correctionStatus: {
    flatteningApplied: boolean;
    ionosphericPhaseCorrectionApplied: false;
    troposphericPhaseCorrectionApplied: false;
    note: string;
  };
  /** Affected area split by sign */
  towardAreaKm2: number;
  awayAreaKm2: number;
  /** Pixels failing the quality mask (everything not valid). */
  maskedPixelCount: number;
  /** Valid pixels classified as detected. */
  detectedPixelCount: number;
  /** Median detection threshold over valid pixels (m). */
  thresholdMedianM: number;
  /** Connected clusters of detected pixels (4-neighbourhood, >= 4 px). */
  clusters: import('./detection').ClusterStats;
  /** Exploratory correlated-noise null-scene screen (never a calibrated FAR). */
  nullScreen: import('./detection').NullSceneResult;
  /** Extreme values (metres; + = away from radar, − = toward) */
  maxAwayM: number;
  maxTowardM: number;
  /** Median one-sigma uncertainty over the anomaly (metres) */
  medianSigmaAnomalyM: number;
  /** Median one-sigma uncertainty over all valid pixels (metres) */
  medianSigmaM: number;
  /** Centroid of the |d|>threshold anomaly in projection coords (metres) */
  anomalyCentroid?: { x: number; y: number };
  /** Anomaly orientation (principal axis, degrees from east) and elongation */
  anomalyOrientationDeg?: number;
  anomalyElongation?: number;
  /** Displacement-weighted integral of d over the anomaly (m·km²) */
  displacementIntegralMKm2: number;
  /** Number of unwrapping connected components with >=1% of valid pixels */
  connectedComponentCount: number;
}

/** Result of the full pipeline for one dataset. */
export interface AnalysisResult {
  datasetId: string;
  params: AnalysisParams;
  summary: ChangeSummary;
  /** Plain-language narrative blocks */
  narrative: {
    whatChanged: string;
    howMuch: string;
    reliability: string;
  };
  /** Attribution layer output */
  attribution: AttributionOutput;
  /** Transport grids for the browser */
  transport: TransportGrid;
  /** Validation block when ground truth exists (synthetic fixtures) */
  validation?: {
    maeM: number;
    rmseM: number;
    biasM: number;
    n: number;
    note: string;
  };
  /** Warnings emitted by pipeline stages */
  warnings: string[];
  /** Wall-clock processing time (ms) */
  processingMs: number;
}

/** One candidate physical explanation with evidence-linked status. */
export interface Hypothesis {
  id: string;
  label: string;
  /** plain-language description of the mechanism */
  description: string;
  status: 'supported' | 'consistent' | 'plausible' | 'not-supported' | 'insufficient-evidence';
  /** Quantitative and qualitative evidence items */
  evidence: string[];
  /** What additional observation would discriminate this hypothesis */
  discriminatingObservation?: string;
}

export interface AttributionOutput {
  /** Ordered best-first */
  hypotheses: Hypothesis[];
  /** USGS events inside the acquisition window and near the footprint (ranked) */
  events: UsgsEventLite[];
  /** How the primary event was chosen; alternatives + exclusion reasons. */
  eventSelection: {
    criteria: import('./attribution').EventSelection['criteria'];
    excluded: Array<{ id: string; reason: string }>;
    alternatives: Array<{ id: string; magnitude: number; place: string; time: string }>;
  };
  /** Forward-model comparison when an event exists and geometry is known */
  forwardModelCheck?: {
    eventId: string;
    /** True when the check could not run (no mechanism, unsupported CRS…). */
    skipped: boolean;
    skipReason?: string;
    amplitudeRatio: number;
    patternCorrelation: number;
    predictedPeakM: number;
    measuredPeakM: number;
    /** Epicentre and its projection into the raster CRS (when the check ran). */
    epicentre?: { lat: number; lon: number; projectedX: number; projectedY: number; epsg: number };
    note: string;
  };
  /** The exploratory screening values used (visible, reproducible). */
  heuristics: import('./attribution').AttributionHeuristics;
  methodNote: string;
}

export interface UsgsEventLite {
  id: string;
  magnitude: number;
  magType?: string;
  place: string;
  time: string;
  lat: number;
  lon: number;
  depthKm: number;
  url: string;
  /** Focal mechanism nodal plane (strike/dip/rake in degrees) if published */
  nodalPlane?: { strike: number; dip: number; rake: number };
  momentTensorSource?: string;
}

/* ==================================================================== */
/* Time-series stack types (system plan §6/§12: SBAS engine inputs)      */
/* ==================================================================== */

/** One acquisition epoch of a GUNW stack. */
export interface StackEpoch {
  index: number;
  /** ISO-8601 acquisition time. */
  time: string;
}

/** One interferogram of the SBAS network. */
export interface StackIfgMeta {
  /** Index of the reference (earlier) epoch. */
  refIndex: number;
  /** Index of the secondary (later) epoch. */
  secIndex: number;
  /** Temporal baseline in days. */
  temporalBaselineDays: number;
}

/** Causal hypothesis classes of the Bayesian engine (plan §8). */
export type CausalClass = 'quake' | 'subsid' | 'slide';

/** Stage-3 feature vector for the Bayesian engine (plan §8 Stage 3). */
export interface CausalFeatures {
  /** Spatial displacement gradient across the anomaly (m/km). */
  gradS: number;
  /** Linear-trend R² of the displacement time series (temporal linearity). */
  tempLinearity: number;
  /** Pearson correlation between displacement and DEM elevation. */
  demCorr: number;
  /** Polarimetric cross-ratio shift (GCOV); NaN when GCOV not ingested. */
  polShift?: number;
}

/** Stage-5 priors from geographic context (plan §8 Stage 5). */
export interface CausalPriors {
  /** Proximity to mapped fault traces (0..1). */
  quake: number;
  /** Proximity to hydrogeological basin boundaries (0..1). */
  subsid: number;
  /** Proximity to steep slopes (0..1). */
  slide: number;
}

/** Stage-4 forward-model residuals for the consistency terms (plan §8). */
export interface ForwardModelResiduals {
  /** Okada H1 residual RMSE (m) and tolerance (m). */
  quakeRmseM: number;
  quakeSigmaM: number;
  /** Linear+seasonal H2 residual RMSE (m) and tolerance (m). */
  trendRmseM: number;
  trendSigmaM: number;
  /** Slope-parallel H3 residual RMSE (m) and tolerance (m). */
  slopeRmseM: number;
  slopeSigmaM: number;
}

/** A synthetic-stack catalog event (exercise fixture for the engine). */
export interface StackCatalogEvent {
  id: string;
  label: string;
  magnitude: number;
  place: string;
  time: string;
  lat: number;
  lon: number;
  depthKm: number;
  url: string;
  nodalPlane: { strike: number; dip: number; rake: number };
  /** True when this is a synthetic fixture event, not a real catalog entry. */
  synthetic: boolean;
}

/** GNSS validation station (plan §9: NGL continuous network). */
export interface GnssStation {
  id: string;
  lat: number;
  lon: number;
  /** Published 3D velocity components (mm/yr, ENU convention). */
  eastMmPerYr: number;
  northMmPerYr: number;
  upMmPerYr: number;
  /** 1σ per component (mm/yr). */
  sigmaMm: number;
  /** Honest labelling of the velocity source. */
  source: string;
}

/** Result of projecting one station into the radar LOS. */
export interface GnssLosComparison {
  stationId: string;
  lat: number;
  lon: number;
  /** GNSS 3D velocity projected to LOS (mm/yr; + = away from satellite). */
  gnssLosMmPerYr: number;
  /** InSAR LOS velocity sampled at the station (mm/yr). */
  insarLosMmPerYr: number;
  /** GNSS − InSAR (mm/yr). */
  diffMmPerYr: number;
  /** Nearest-pixel distance (km). */
  distanceKm: number;
}

export interface GnssValidation {
  /** Per-station comparisons (plan §11 protocol). */
  stations: GnssLosComparison[];
  /** RMSE of InSAR vs GNSS LOS velocities (mm/yr). */
  rmseMmPerYr: number;
  /** Mean (InSAR − GNSS) bias (mm/yr). */
  biasMmPerYr: number;
  /** Number of stations used. */
  n: number;
  note: string;
}

/** Phase-budget ledger entry (plan §6 decomposition, corrected terms). */
export interface PhaseBudgetEntry {
  key: string;
  label: string;
  /** RMS magnitude of the term over valid pixels (m of LOS range). */
  rmsM: number;
  note: string;
}

export interface PhaseBudget {
  /** Interferometric-phase terms quantified for the stack (plan §6). */
  entries: PhaseBudgetEntry[];
  methodNote: string;
}

/* ==================================================================== */
/* SBAS pipeline result (system plan §12 pipeline, §13 stages)           */
/* ==================================================================== */

/** One PELT change point with interpretation. */
export interface ChangePointMark {
  /** Epoch index AFTER which the change occurs (series is per-epoch). */
  afterEpochIndex: number;
  /** ISO time of the epoch before the change. */
  time: string;
  /** Step magnitude across the change (m). */
  stepM: number;
  note: string;
}

/** A time series extracted for one location of interest. */
export interface KeySeries {
  id: string;
  label: string;
  /** Grid pixel indices. */
  i: number;
  j: number;
  /** Epoch times (ISO). */
  times: string[];
  /** Cumulative LOS displacement per epoch (m; + = away from satellite). */
  displacementM: number[];
  /** 1σ per epoch (m). */
  sigmaM: number[];
  /** PELT change points. */
  changePoints: ChangePointMark[];
  /** Linear-trend R² (temporal linearity feature). */
  linearR2: number;
  /** Mean velocity from the regression (m/yr). */
  velocityMPerYr: number;
  note: string;
}

/** One attributed anomaly cluster (Bayesian engine output + geometry). */
export interface AnomalyAttribution {
  /** Stable anomaly id (used by POST /api/v1/attribute). */
  id: string;
  label: string;
  /** Pixel count and area. */
  pixelCount: number;
  areaKm2: number;
  /** Centroid + bounding box in grid pixels. */
  centroid: { i: number; j: number };
  bbox: { i0: number; j0: number; i1: number; j1: number };
  /** Peak |velocity| inside the cluster (m/yr). */
  peakVelocityMPerYr: number;
  /** Stage-3 features. */
  features: CausalFeatures;
  /** Stage-5 priors used. */
  priors: CausalPriors;
  /** Stage-4/5 forward residuals (when computable). */
  residuals?: ForwardModelResiduals;
  /** The Bayesian engine output. */
  bayes: import('./bayes').BayesianAttribution;
}

/** Velocity-field summary statistics. */
export interface SbasSummary {
  /** Pixels passing the quality gates. */
  validPixelCount: number;
  validFraction: number;
  /** Temporal-coherence gate retention (plan §11 benchmark). */
  temporalCoherenceRetainedFraction: number;
  /** Area of one pixel (km²). */
  pixelAreaKm2: number;
  analysedAreaKm2: number;
  /** Velocity extremes over valid pixels (m/yr; + = away). */
  maxVelocityMPerYr: number;
  minVelocityMPerYr: number;
  medianVelocityMPerYr: number;
  medianVelocitySigmaMPerYr: number;
  /** Area moving faster than the rate threshold toward/away (km²). */
  fastAreaKm2: number;
  /** Rate threshold used (m/yr). */
  rateThresholdMPerYr: number;
  /** Median temporal coherence over valid pixels. */
  medianTemporalCoherence: number;
  /** Number of epochs / interferograms inverted. */
  epochCount: number;
  ifgCount: number;
  /** Regularization λ chosen by the L-curve. */
  lambda: number;
  lambdaCriterion: string;
}

/** Full SBAS pipeline result (mirrors AnalysisResult for stack datasets). */
export interface SbasResult {
  kind: 'sbas';
  datasetId: string;
  /** Epoch times (ISO) — the cumulative-cube axis. */
  epochs: string[];
  params: AnalysisParams;
  summary: SbasSummary;
  /** Velocity transport (displacement slot = m/yr; coherence slot = temporal coherence). */
  velocity: TransportGrid;
  /** Cumulative displacement per epoch, downsampled for transport. */
  cumulative: {
    meta: GridMeta;
    /** Base64 Float32 per epoch (NaN = masked). */
    grids: string[];
    /** Base64 Uint8 shared valid-mask. */
    mask: string;
  };
  /** Time series at key locations (anomaly centroids, reference, extremes). */
  keySeries: KeySeries[];
  /** Bayesian attribution per anomaly cluster. */
  anomalies: AnomalyAttribution[];
  /** Phase-budget correction ledger (plan §6). */
  corrections: PhaseBudget;
  /** GNSS cross-validation (plan §11). */
  gnss: GnssValidation;
  /** Brier score calibration over labeled zones (plan §7/§11). */
  brier?: { score: number; n: number; note: string };
  /** Validation vs analytic truth when the stack provides it. */
  truthValidation?: { velocityRmseMPerYr: number; note: string };
  /** Catalog events relevant to the stack window (earthquake focus). */
  events: StackCatalogEvent[];
  narrative: { verdict: string; whatChanged: string; howMuch: string; reliability: string };
  warnings: string[];
  processingMs: number;
}
