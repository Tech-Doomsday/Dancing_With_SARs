/**
 * Client-side types mirroring the API contract.
 * Heavy science types are imported from @/lib/science/types where needed.
 */

import type {
  AnalysisResult,
  ProvenanceRecord,
  RadarGeometry,
  SbasResult,
  TransportGrid,
  UsgsEventLite,
} from '@/lib/science/types';

/** Structured availability of a dataset's local scientific data. */
export interface DatasetAvailability {
  state: 'available' | 'missing' | 'incomplete' | 'invalid';
  missingFiles: string[];
  invalidFiles: Array<{ file: string; reason: string }>;
  explanation: string;
  /** Pointer to a real recorded analysis that exists despite missing data. */
  recorded?: { recordedAt: string; note: string };
}

export interface DatasetListEntry {
  id: string;
  slug: string;
  title: string;
  blurb: string;
  mode: 'quantitative' | 'evidence';
  dataClass: 'real' | 'synthetic' | 'derived';
  isSynthetic: boolean;
  sensor: string;
  referenceTime: string;
  secondaryTime: string;
  temporalBaselineDays: number;
  center?: { lat: number; lon: number };
  /** Availability of the local scientific data (never fabricated). */
  availability: DatasetAvailability;
  /** Scene-adapted starting parameters suggested by the dataset registry. */
  suggestedParams?: {
    coherenceThreshold?: number;
    detectionFloor?: number;
    significanceK?: number;
  };
}

export interface PanamaGranule {
  granuleId: string;
  timeStart: string;
  timeEnd: string;
  polygon: string;
  sizeMB: number;
  dataUrl?: string;
  browseUrl?: string;
  conceptId: string;
  localBrowseUrl: string;
  bracketsEvent?: boolean;
}

export interface DatasetDetail {
  id: string;
  slug: string;
  title: string;
  blurb: string;
  mode: 'quantitative' | 'evidence';
  isSynthetic: boolean;
  provenance: ProvenanceRecord;
  geometry?: RadarGeometry;
  footprintPolygon?: [number, number][];
  center?: { lat: number; lon: number };
  grid?: { width: number; height: number; epsg: number };
  numberOfLooks?: number;
  /** Availability of the local scientific data (never fabricated). */
  availability: DatasetAvailability;
}

export interface EvidenceAnalysisResult {
  kind: 'evidence';
  datasetId: string;
  event: UsgsEventLite | null;
  granules: PanamaGranule[];
  liveEvents: UsgsEventLite[];
  liveStatus: string;
  prediction?: TransportGrid;
  narrative: { whatChanged: string; howMuch: string; reliability: string };
  warnings: string[];
  processingMs: number;
  runId?: string;
}

export type AnalysisPayload =
  | (AnalysisResult & { runId?: string })
  | EvidenceAnalysisResult
  | RecordedRunResult
  | (SbasResult & { runId?: string });

export function isEvidenceResult(r: AnalysisPayload): r is EvidenceAnalysisResult {
  return (r as EvidenceAnalysisResult).kind === 'evidence';
}

export function isSbasResult(r: AnalysisPayload): r is SbasResult & { runId?: string } {
  return (r as SbasResult).kind === 'sbas';
}

/** A real analysis recorded from a past run of the actual product (Mojave). */
export interface RecordedRunResult {
  kind: 'recorded';
  datasetId: string;
  recordedAt: string;
  note: string;
  provenance: {
    provider: string;
    productId: string;
    productType: string;
    platform: string;
    doi?: string;
    referenceTime: string;
    secondaryTime: string;
    temporalBaselineDays: number;
    sourceUrl: string;
    dataClass: 'real';
    qualityNotes?: string[];
  };
  geometry: {
    wavelength: number;
    incidenceAngleDeg: number;
    headingDeg: number;
    passDirection: 'ascending' | 'descending';
    lookDirection: 'right' | 'left';
    numberOfLooks: number;
  };
  footprintPolygon?: [number, number][];
  center?: { lat: number; lon: number };
  params: Record<string, unknown>;
  summary: {
    validPixelCount: number;
    validFraction: number;
    analysedAreaKm2: number;
    coherenceMedian: number;
    affectedAreaKm2: number;
    systematicIonoFloorM: number;
    medianSigmaM: number;
    connectedComponentCount: number;
    [k: string]: unknown;
  };
  measuredFindings?: {
    maxAbsLosM: number;
    zeroPhaseFraction?: number;
    distribution?: Array<{ label: string; fraction: number }>;
    coherenceGte01Fraction?: number;
    ccGateExcludedFraction?: number;
    ionoScreenStdRad?: number;
    ionoScreenUncertaintyMedianRad?: number;
    [k: string]: unknown;
  };
  event?: {
    id: string;
    magnitude: number;
    magType?: string;
    place: string;
    time: string;
    lat: number;
    lon: number;
    depthKm: number;
    url: string;
    insideAcquisitionWindow?: boolean;
    note?: string;
  };
  narrative: { verdict?: string; whatChanged: string; howMuch: string; reliability: string };
  attributionSummary?: { earthquake?: string; nullHypothesis?: string; methodNote?: string };
  warnings: string[];
}

export function isRecordedResult(r: AnalysisPayload): r is RecordedRunResult {
  return (r as RecordedRunResult).kind === 'recorded';
}

export interface HealthPayload {
  ok: boolean;
  service: string;
  version: string;
  time: string;
  capabilities: {
    quantitativePipeline: boolean;
    okadaForwardModel: boolean;
    liveCmr: boolean;
    liveUsgs: boolean;
    nisarH5Ingestion: string;
    /** True when the Python engine source tree was found next to the app. */
    engineCatalog?: boolean;
  };
}

export interface CmrLiveResult {
  ok: boolean;
  hits?: number;
  count?: number;
  granules?: PanamaGranule[];
  source?: string;
  queriedAt?: string;
  error?: string;
}
