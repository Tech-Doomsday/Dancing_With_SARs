'use client';

/**
 * Earthquake Analysis — the MOTUS module of the Dancing with SARs web app.
 *
 * Origin: the "MOTUS — Earth-change intelligence" application built for the
 * NASA Space Apps Challenge 2026 (challenge: "Dancing with the SARs"), now
 * integrated as the interactive counterpart of the nisar-hazard-engine's
 * earthquake_coseismic (3.01) and tsunami_inundation (3.14) pipelines.
 *
 * The section follows one simple story —
 *   Step 1 · Choose an area  →  Step 2 · Analyze it  →  Step 3 · Understand the result
 * plus two tools that make it real: a guided tour of the three flagship stories,
 * and a live "find a good radar pair" search over any place on Earth.
 *
 * Every screen answers: what am I looking at, what should I do next, what
 * does the result mean, and what should I *not* conclude from it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { motion } from 'framer-motion';
import { Loader2, Play, RotateCcw, ChevronDown, Check, MapPin, AlertTriangle, Sparkles, Download, FileSpreadsheet, Image as ImageIcon, Mountain } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { QuantitativeResults, EvidenceResults, RecordedResults, SbasResults } from '@/components/app/results-panel';
import { PairFinder } from '@/components/app/pair-finder';

/** Tsunami capability (2026-10-24): real DART records + NSWE simulation.
 * Loaded lazily — the radar workspace stays the primary flow. */
const TsunamiPanel = dynamic(
  () => import('@/components/app/tsunami-panel').then((m) => m.default),
  { ssr: false, loading: () => (
    <div className="mt-12 h-24 animate-pulse rounded-xl border border-[#EFEAE2] bg-white" aria-label="Loading tsunami tools" />
  ) },
);
import { TOUR_STORIES, StoryPicker, TourCard, type TourAction } from '@/components/app/tour';
import {
  DataClassBadge,
  MicroLabel,
  ReadinessBadge,
  readinessOf,
  WhatIsThis,
  fmtDate,
  fmtDateTime,
} from '@/components/app/ui-bits';
import type {
  AnalysisPayload,
  DatasetDetail,
  DatasetListEntry,
  HealthPayload,
  PanamaGranule,
  RecordedRunResult,
} from '@/components/app/types';
import { isEvidenceResult, isRecordedResult, isSbasResult } from '@/components/app/types';
import type { UsgsEventLite } from '@/lib/science/types';
import type { LayerKind } from '@/components/app/raster-render';
import { renderOverlay } from '@/components/app/raster-render';
import { decodeTransport, b64ToFloat32, b64ToUint8, type DecodedGrids } from '@/components/app/decode';
import { toGeoTiff, toCsv, downloadBlob, fileStem } from '@/components/app/export';
import { TokenPasteBox } from '@/components/app/token-paste-box';

const MapPanel = dynamic(() => import('@/components/app/map-panel'), {
  ssr: false,
  loading: () => (
    <div className="flex min-h-[420px] items-center justify-center rounded-xl border border-[#E7E2DA] bg-white">
      <Loader2 className="h-5 w-5 animate-spin text-[#B8AFA2]" aria-label="Loading map" />
    </div>
  ),
});

interface AdvancedParams {
  coherenceThreshold: number;
  detectionFloor: number;
  significanceK: number;
  referenceStrategy: 'farField' | 'fullFrameMedian';
  detrend: boolean;
}

const DEFAULT_ADVANCED: AdvancedParams = {
  coherenceThreshold: 0.5,
  detectionFloor: 0.01,
  significanceK: 3,
  referenceStrategy: 'farField',
  detrend: false,
};

/**
 * Display / default-selection rank: a real, ready, measurable dataset first,
 * then the real evidence view, then a real recorded result, then demos, then
 * unavailable data. Stable within each class.
 */
function readinessRank(d: DatasetListEntry): number {
  if (d.availability.state !== 'available') return d.availability.recorded ? 1.5 : 3;
  if (d.mode === 'evidence') return 1;
  return 0;
}

/**
 * Real processing stages, mirroring src/lib/science/pipeline.ts (Stages A–E).
 * The server runs them in a single request, so while a run is in flight the
 * list is shown as "in progress" as a whole — no per-stage completion is
 * faked. Checkmarks appear only after the result has actually returned.
 */
function processingStages(mode: 'quantitative' | 'evidence'): Array<{ label: string; detail: string }> {
  if (mode === 'evidence') {
    return [
      { label: 'Reading the real catalog records', detail: 'Loading the cached NASA and USGS records for the event and the radar data that covers it.' },
      { label: 'Checking the latest data availability', detail: 'Asking the USGS earthquake catalog for the latest picture of the event sequence.' },
      { label: 'Computing the earthquake model prediction', detail: 'Calculating the ground movement a standard earthquake model expects, from the published fault information.' },
    ];
  }
  return [
    { label: 'Checking the radar data', detail: 'Reading the measurement files and setting aside pixels whose signal is too unreliable to measure.' },
    { label: 'Measuring changes in the radar signal', detail: 'Comparing the two radar images and converting the difference into apparent movement toward or away from the satellite.' },
    { label: 'Estimating measurement uncertainty', detail: 'Estimating how much of the apparent change could come from measurement noise and uncorrected atmospheric effects.' },
    { label: 'Checking whether the pattern is a possible real change', detail: 'Flagging areas where the apparent change clearly exceeds the uncertainty, and testing the rule against simulated no-change scenes.' },
  ];
}

function layerLabel(l: LayerKind, isPrediction: boolean, isSbas = false): string {
  if (l === 'velocity') return 'Yearly motion';
  if (l === 'displacement') {
    if (isPrediction) return 'Model prediction';
    if (isSbas) return 'Cumulative movement';
    return 'Where the ground moved';
  }
  if (l === 'detected') return 'Detected change';
  if (l === 'phase') return 'Radar phase (advanced)';
  if (l === 'coherence') return 'Signal quality';
  if (l === 'sigma') return 'Uncertainty';
  return 'Measured areas';
}

function layerInterpretation(l: LayerKind, isPrediction: boolean, isSbas = false): string {
  if (l === 'velocity') {
    return 'Colors show the average line-of-sight velocity of each patch of ground across the whole stack (positive = moving away from the satellite). Coseismic steps have been separated out by the PELT refit, so this map shows sustained motion only.';
  }
  if (l === 'displacement') {
    if (isPrediction) {
      return 'Colors show where a standard earthquake model predicts the ground should have moved — brown = toward the satellite (mostly rising), green = away (mostly sinking). This is a calculation, not a measurement of this event.';
    }
    if (isSbas) {
      return 'Colors show the cumulative apparent line-of-sight movement from the first acquisition up to the selected epoch (drag the time slider). The subsidence bowls deepen steadily; the coseismic step appears at the event epoch.';
    }
    return 'Colors show how far each patch of ground appears to have moved toward or away from the satellite between the two radar images: brown = toward the satellite (mostly rising), green = away (mostly sinking). No color = could not be measured.';
  }
  if (l === 'detected') {
    return 'Only the areas where the measured change is bigger than the uncertainty are colored — a real change with high confidence. Brown = the ground rose, green = it sank. Everything else is left uncolored on purpose.';
  }
  if (l === 'phase') {
    return 'The raw radar measurement drawn as color fringes, like contour lines on a hiking map — each full color cycle represents one half-wavelength of ground movement. The plain movement map above is computed from this. Advanced view: you don\u2019t need it to understand the result.';
  }
  if (l === 'coherence') {
    return isSbas
      ? 'Darker areas kept a consistent radar phase relationship across all interferograms (temporal coherence γ_t); lighter areas failed the gate and are excluded from the time-series inversion.'
      : 'Darker areas have a stronger, more reliable radar signal; lighter areas are noisier. Very light areas were excluded from the analysis.';
  }
  if (l === 'sigma') return 'Darker areas have more uncertain measurements. Changes smaller than this uncertainty cannot be distinguished from noise.';
  return 'Gray shows where a valid measurement exists. Transparent areas were excluded (no reliable signal).';
}

/** Animated integer counter for the hero stats strip. */
function CountUp({ to, duration = 900 }: { to: number; duration?: number }) {
  const [v, setV] = useState(0);
  useEffect(() => {
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / duration);
      setV(Math.round(to * (1 - Math.pow(1 - p, 3))));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [to, duration]);
  return <>{v.toLocaleString()}</>;
}

export interface EarthquakeAnalysisProps {
  /** App-level health payload (fetched once by the page shell). */
  health: HealthPayload | null;
  /** When true, scroll to the tsunami panel once after mount (deep-link from the engine overview). */
  focusTsunami?: boolean;
  /** Called after focusTsunami has been handled, so the shell can clear the flag. */
  onTsunamiFocusHandled?: () => void;
}

export function EarthquakeAnalysis({ health, focusTsunami = false, onTsunamiFocusHandled }: EarthquakeAnalysisProps) {
  const [datasets, setDatasets] = useState<DatasetListEntry[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DatasetDetail | null>(null);
  const [detailEvent, setDetailEvent] = useState<unknown>(null);
  const [detailGranules, setDetailGranules] = useState<PanamaGranule[]>([]);
  const [recordedRun, setRecordedRun] = useState<RecordedRunResult | null>(null);
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<AnalysisPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState<AdvancedParams>(DEFAULT_ADVANCED);
  const [layer, setLayer] = useState<LayerKind>('displacement');
  const [opacity, setOpacity] = useState(0.85);
  const [mapMode, setMapMode] = useState<'swipe' | 'single'>('swipe');
  const [grids, setGrids] = useState<DecodedGrids | null>(null);
  const [gridsMeta, setGridsMeta] = useState<Parameters<typeof import('@/components/app/raster-render').renderOverlay>[1] | undefined>(undefined);
  const [granulePolys, setGranulePolys] = useState<Array<{ granule: PanamaGranule; poly: [number, number][] }>>([]);
  const [sbasEpoch, setSbasEpoch] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Guided tour state.
  const [tour, setTour] = useState<{ storyIdx: number; step: number } | null>(null);
  const [storyPickerOpen, setStoryPickerOpen] = useState(false);
  const [pendingTourAction, setPendingTourAction] = useState<TourAction | null>(null);
  const [tourBusy, setTourBusy] = useState(false);

  /** Display order: real & ready first, demos next, unavailable last (stable). */
  const sortedDatasets = useMemo(() => {
    if (!datasets) return null;
    return [...datasets].sort((a, b) => readinessRank(a) - readinessRank(b));
  }, [datasets]);

  useEffect(() => {
    fetch('/api/datasets')
      .then((r) => r.json())
      .then((d: { ok: boolean; datasets: DatasetListEntry[] }) => {
        if (d.ok) {
          setDatasets(d.datasets);
          // Default to the first dataset the user can actually analyze — a
          // real, ready, quantitative dataset when one exists.
          const ranked = [...d.datasets].sort((a, b) => readinessRank(a) - readinessRank(b));
          setSelectedId(ranked[0]?.id ?? null);
        }
      })
      .catch(() => setDatasets([]));
  }, []);

  // Load dataset detail on selection; adopt the dataset's suggested starting
  // parameters; capture the recorded run when one exists (Mojave).
  useEffect(() => {
    if (!selectedId) return;
    setResult(null);
    setError(null);
    setGrids(null);
    setGridsMeta(undefined);
    setGranulePolys([]);
    setDetail(null);
    setRecordedRun(null);
    setLayer('displacement');
    setMapMode('swipe');
    const suggestedCoh = datasets?.find((d) => d.id === selectedId)?.suggestedParams?.coherenceThreshold;
    setAdvanced((a) => ({
      ...a,
      coherenceThreshold: suggestedCoh ?? DEFAULT_ADVANCED.coherenceThreshold,
    }));
    fetch(`/api/datasets/${selectedId}`)
      .then((r) => r.json())
      .then((d: {
        ok: boolean;
        dataset?: DatasetDetail;
        event?: unknown;
        granules?: PanamaGranule[];
        recordedRun?: RecordedRunResult;
      }) => {
        if (d.ok && d.dataset) {
          setDetail(d.dataset);
          setDetailEvent(d.event ?? null);
          setDetailGranules(d.granules ?? []);
          if (d.recordedRun) setRecordedRun({ ...d.recordedRun, kind: 'recorded' as const });
        }
      })
      .catch((e) => setError((e as Error).message));
  }, [selectedId, datasets]);

  /** Show the recorded (real, past) analysis of a dataset whose source data is gone. */
  const showRecorded = useCallback(() => {
    if (!recordedRun) return;
    setResult(null);
    setError(null);
    setGrids(null);
    setGridsMeta(undefined);
    setLayer('displacement');
    setResult(recordedRun);
  }, [recordedRun]);

  const runAnalysis = useCallback(async () => {
    if (!selectedId || running) return;
    setRunning(true);
    setError(null);
    setResult(null);
    setElapsed(0);
    timerRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);
    try {
      const res = await fetch('/api/analyses', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          datasetId: selectedId,
          params: {
            coherenceThreshold: advanced.coherenceThreshold,
            detectionFloor: advanced.detectionFloor,
            significanceK: advanced.significanceK,
            referenceStrategy: advanced.referenceStrategy,
            detrend: advanced.detrend,
          },
        }),
      });
      const data = (await res.json()) as { ok: boolean; result?: AnalysisPayload; error?: string };
      if (!data.ok || !data.result) {
        setError(data.error ?? 'The analysis failed for an unknown reason.');
      } else {
        const r = data.result;
        setResult(r);
        setSbasEpoch(0);
        if (isSbasResult(r)) {
          setGrids(decodeTransport(r.velocity));
          setGridsMeta(r.velocity.meta);
          setLayer('velocity');
        } else if (isEvidenceResult(r)) {
          setGrids(r.prediction ? decodeTransport(r.prediction) : null);
          setGridsMeta(r.prediction?.meta);
          setLayer('displacement');
        } else if (!isRecordedResult(r)) {
          setGrids(decodeTransport(r.transport));
          setGridsMeta(r.transport.meta);
          setLayer('displacement');
        }
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      if (timerRef.current) clearInterval(timerRef.current);
      setRunning(false);
    }
  }, [selectedId, running, advanced]);

  useEffect(() => () => { if (timerRef.current) clearInterval(timerRef.current); }, []);

  // SBAS epoch slider: swap the displayed grid between the velocity field
  // (layer 'velocity') and the cumulative displacement at the selected epoch
  // (layer 'displacement'). Decoding is cheap (~150×110 float32 per epoch).
  const sbasResult = result && isSbasResult(result) ? result : null;
  useEffect(() => {
    if (!sbasResult) return;
    if (layer === 'velocity') {
      setGrids(decodeTransport(sbasResult.velocity));
      setGridsMeta(sbasResult.velocity.meta);
      return;
    }
    if (layer === 'displacement') {
      const e = Math.max(0, Math.min(sbasEpoch, sbasResult.cumulative.grids.length - 1));
      const data = b64ToFloat32(sbasResult.cumulative.grids[e]);
      const mask = b64ToUint8(sbasResult.cumulative.mask);
      const n = sbasResult.cumulative.meta.width * sbasResult.cumulative.meta.height;
      for (let i = 0; i < n; i++) if (!mask[i]) data[i] = NaN;
      const vel = decodeTransport(sbasResult.velocity);
      setGrids({
        width: sbasResult.cumulative.meta.width,
        height: sbasResult.cumulative.meta.height,
        displacement: data,
        coherence: vel.coherence,
        mask,
        sigma: vel.sigma,
        // No detection rule on cumulative stack epochs — zero-filled.
        detected: new Uint8Array(n),
      });
      setGridsMeta(sbasResult.cumulative.meta);
    }
  }, [sbasEpoch, layer, sbasResult]);

  /* ---------------- Guided tour ---------------- */

  const enterTourStep = useCallback((storyIdx: number, step: number) => {
    setTour({ storyIdx, step });
    const st = TOUR_STORIES[storyIdx].steps[step];
    const el = document.getElementById(st.target);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: st.target === 'hero' ? 'start' : 'center' });
      el.classList.remove('motus-tour-highlight');
      void el.offsetWidth; // restart the animation
      el.classList.add('motus-tour-highlight');
      setTimeout(() => el.classList.remove('motus-tour-highlight'), 3600);
    }
    if (st.action.type === 'select') {
      setSelectedId(st.action.datasetId);
      setResult(null);
    } else if (st.action.type === 'run') {
      setPendingTourAction({ type: 'run' });
    } else if (st.action.type === 'recorded') {
      setPendingTourAction({ type: 'recorded' });
    }
  }, []);

  // Execute pending tour actions once the detail / recorded run has loaded.
  useEffect(() => {
    if (!pendingTourAction || !detail) return;
    if (pendingTourAction.type === 'run') {
      if (detail.availability.state === 'available' && !running && !result) {
        setPendingTourAction(null);
        setTourBusy(true);
        void runAnalysis().finally(() => setTourBusy(false));
      }
    } else if (pendingTourAction.type === 'recorded') {
      if (recordedRun) {
        setPendingTourAction(null);
        showRecorded();
      }
    }
  }, [pendingTourAction, detail, recordedRun, running, result, runAnalysis, showRecorded]);

  /* ---------------- Map events + helpers ---------------- */

  const eventsForMap = useMemo(() => {
    const fromResult = (() => {
      if (!result) return [];
      if (isRecordedResult(result)) return result.event ? [result.event] : [];
      if (isEvidenceResult(result)) {
        return result.event ? [result.event, ...result.liveEvents.filter((e) => e.id !== result.event?.id)] : result.liveEvents;
      }
      if (isSbasResult(result)) return result.events as unknown as UsgsEventLite[];
      return result.attribution.events;
    })();
    // For evidence datasets the primary event is known before any analysis
    // runs (it is part of the dataset record) — show it on selection.
    if (detail?.mode === 'evidence' && detailEvent && !fromResult.some((e) => e.id === (detailEvent as { id: string }).id)) {
      return [detailEvent as never, ...fromResult];
    }
    return fromResult;
  }, [result, detail, detailEvent]);

  const primaryEvent = useMemo(() => {
    if (result && isRecordedResult(result)) return result.event ?? null;
    if (result && isEvidenceResult(result)) return result.event;
    return detailEvent as { id: string } | null ?? null;
  }, [result, detailEvent]);

  const selectedEntry = datasets?.find((d) => d.id === selectedId) ?? null;
  const selectedReadiness = selectedEntry
    ? readinessOf(selectedEntry)
    : detail
      ? readinessOf({ dataClass: detail.provenance.dataClass, mode: detail.mode, availability: detail.availability })
      : null;
  const isPredictionLayer =
    !!result && isEvidenceResult(result) && !!result.prediction && layer === 'displacement';
  const stages = detail ? processingStages(detail.mode) : processingStages('quantitative');
  const anyReady = (sortedDatasets ?? []).some((d) => d.availability.state === 'available');
  const realCount = (datasets ?? []).filter((d) => d.dataClass === 'real').length;
  const liveCatalogs = !!health && (health.capabilities.liveUsgs || health.capabilities.liveCmr);

  /* ---------------- Before/After swipe derivation ---------------- */

  /** Radar wavelength from the dataset geometry (phase layer needs it). */
  const wavelengthM = detail?.geometry?.wavelength;

  /** Acquisition dates that bracket the measured change (enables the divider). */
  const beforeAfter = useMemo(() => {
    if (!detail || !grids) return null;
    if (result && isSbasResult(result)) return null; // time-series stack: no single before/after pair
    const { referenceTime, secondaryTime } = detail.provenance;
    if (!referenceTime || !secondaryTime) return null;
    return {
      refTime: referenceTime,
      secTime: secondaryTime,
      prediction: detail.mode === 'evidence',
    };
  }, [detail, grids, result]);

  const swipeDays = useMemo(
    () =>
      beforeAfter
        ? Math.round(
            Math.abs(new Date(beforeAfter.secTime).getTime() - new Date(beforeAfter.refTime).getTime()) / 86400000,
          )
        : null,
    [beforeAfter],
  );

  /** The strongest earthquake that struck between the two radar passes. */
  const windowQuake = useMemo(() => {
    if (!beforeAfter || eventsForMap.length === 0) return null;
    const t0 = new Date(beforeAfter.refTime).getTime();
    const t1 = new Date(beforeAfter.secTime).getTime();
    let best: (typeof eventsForMap)[number] | null = null;
    for (const ev of eventsForMap) {
      const t = Date.parse(ev.time);
      if (!(t > t0 && t < t1)) continue;
      if (!best || ev.magnitude > best.magnitude) best = ev;
    }
    return best;
  }, [beforeAfter, eventsForMap]);

  /** Layer tabs offered for the current result kind. */
  const layerOptions = useMemo(() => {
    if (sbasResult) return ['velocity', 'displacement', 'coherence', 'sigma', 'coverage'] as LayerKind[];
    if (beforeAfter?.prediction) return ['displacement', 'coherence', 'sigma', 'coverage'] as LayerKind[];
    return ['displacement', 'detected', 'phase', 'coherence', 'sigma', 'coverage'] as LayerKind[];
  }, [sbasResult, beforeAfter]);

  /* ---------------- Exports (from the decoded grids, client-side) ---------------- */

  const currentLayerGrid = useCallback((): { data: Float32Array; name: string } | null => {
    if (!grids || !gridsMeta) return null;
    if (layer === 'coherence') return { data: grids.coherence, name: 'coherence' };
    if (layer === 'sigma') return { data: grids.sigma, name: 'uncertainty' };
    if (layer === 'velocity') return { data: grids.displacement, name: 'los-velocity-m-per-yr' };
    if (layer === 'detected') {
      const d = new Float32Array(grids.detected.length);
      for (let i = 0; i < grids.detected.length; i++) d[i] = grids.detected[i] ? 1 : NaN;
      return { data: d, name: 'detected-change-mask' };
    }
    if (layer === 'phase') return { data: grids.displacement, name: 'los-phase-equivalent' };
    if (layer === 'coverage') {
      const m = new Float32Array(grids.mask.length);
      for (let i = 0; i < grids.mask.length; i++) m[i] = grids.mask[i] ? 1 : NaN;
      return { data: m, name: 'coverage' };
    }
    return { data: grids.displacement, name: 'apparent-los-displacement' };
  }, [grids, gridsMeta, layer]);

  const exportGeotiff = useCallback(() => {
    const g = currentLayerGrid();
    if (!g || !gridsMeta) return;
    try {
      const blob = toGeoTiff(g.data, gridsMeta, `MOTUS ${detail?.title ?? ''} — ${g.name}`);
      downloadBlob(blob, `${fileStem(selectedId ?? 'dataset', g.name, isPredictionLayer)}.tif`);
    } catch (e) {
      console.error('[motus-export] geotiff failed', e);
    }
  }, [currentLayerGrid, gridsMeta, detail, selectedId, isPredictionLayer]);

  const exportCsv = useCallback(() => {
    const g = currentLayerGrid();
    if (!g || !grids || !gridsMeta) return;
    try {
      const blob = toCsv(g.data, grids.mask, gridsMeta, g.name);
      downloadBlob(blob, `${fileStem(selectedId ?? 'dataset', g.name, isPredictionLayer)}.csv`);
    } catch (e) {
      console.error('[motus-export] csv failed', e);
    }
  }, [currentLayerGrid, grids, gridsMeta, selectedId, isPredictionLayer]);

  const exportPng = useCallback(() => {
    if (!grids || !gridsMeta) return;
    try {
      const ov = renderOverlay(grids, gridsMeta, layer, undefined, 1024, wavelengthM);
      if (!ov?.dataUrl) return;
      const a = document.createElement('a');
      a.href = ov.dataUrl;
      a.download = `${fileStem(selectedId ?? 'dataset', layer, isPredictionLayer)}.png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (e) {
      console.error('[motus-export] png failed', e);
    }
  }, [grids, gridsMeta, layer, selectedId, isPredictionLayer, wavelengthM]);

  const scrollToChoose = () => {
    document.getElementById('choose-area')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // Deep-link from the engine overview (tsunami_inundation pipeline card):
  // scroll to the tsunami panel once, after the section has mounted.
  useEffect(() => {
    if (!focusTsunami) return;
    const t = setTimeout(() => {
      document.getElementById('tsunami-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      onTsunamiFocusHandled?.();
    }, 150);
    return () => clearTimeout(t);
  }, [focusTsunami, onTsunamiFocusHandled]);

  return (
    <>
      <main className="mx-auto w-full max-w-[1240px] flex-1 px-4 py-8 sm:px-6 sm:py-10">
        {/* ---------- Hero: what is this, what can I do here ---------- */}
        <section id="hero" aria-label="Introduction" className="mb-10 max-w-3xl scroll-mt-20">
          <p className="mb-2 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-[#B08D57]">
            Earthquake analysis · MOTUS module · engine pipelines 3.01 &amp; 3.14
          </p>
          <motion.h1
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, ease: 'easeOut' }}
            className="text-[26px] font-semibold leading-8 tracking-[-0.01em] text-[#1C1A17] sm:text-[30px] sm:leading-9"
          >
            Did the ground move? Check it with space radar.
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: 0.06, ease: 'easeOut' }}
            className="mt-3 max-w-2xl text-[14px] leading-6 text-[#4A443C]"
          >
            MOTUS compares radar images of the same place taken days apart, looking for
            earthquake-sized ground movement. It cross-checks any pattern against real earthquake
            records and physics models — and tells you honestly when it can&rsquo;t tell.
            It is the interactive counterpart of the engine&rsquo;s{' '}
            <span className="font-mono text-[12.5px]">earthquake_coseismic</span> and{' '}
            <span className="font-mono text-[12.5px]">tsunami_inundation</span> pipelines.
          </motion.p>
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: 0.12, ease: 'easeOut' }}
            className="mt-5 flex flex-wrap items-center gap-3"
          >
            <Button
              onClick={scrollToChoose}
              className="h-11 gap-2 rounded-lg bg-[#1C1A17] px-5 text-[13.5px] font-medium text-white hover:bg-[#33302B]"
            >
              <Play className="h-4 w-4" aria-hidden />
              Start an analysis
            </Button>
            <Button
              onClick={() => setStoryPickerOpen(true)}
              className="h-11 gap-2 rounded-lg border border-[#D8D2C6] bg-white px-5 text-[13.5px] font-medium text-[#1C1A17] hover:bg-[#F7F4EE]"
            >
              <Sparkles className="h-4 w-4 text-[#B08D57]" aria-hidden />
              Take the guided tour
            </Button>
            <span className="text-[12px] text-[#8A837B]">
              3 real-data stories · about 3 minutes · drives the real page
            </span>
          </motion.div>

          {/* live stats strip */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.5, delay: 0.2 }}
            className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-[#EFEAE2] bg-white px-4 py-3"
            aria-label="Live service status"
          >
            <span className="flex items-baseline gap-1.5 text-[12px] text-[#4A443C]">
              <span className="font-mono text-[15px] font-semibold tabular-nums text-[#1C1A17]">
                {datasets ? <CountUp to={datasets.length} /> : '—'}
              </span>
              study areas
            </span>
            <span className="flex items-baseline gap-1.5 text-[12px] text-[#4A443C]">
              <span className="font-mono text-[15px] font-semibold tabular-nums text-[#1C1A17]">
                {datasets ? <CountUp to={realCount} /> : '—'}
              </span>
              built on real NASA data
            </span>
            <span className="flex items-center gap-1.5 text-[12px] text-[#4A443C]">
              <span
                className={`motus-live-dot inline-block h-2 w-2 rounded-full ${liveCatalogs ? 'bg-[#2C5F58]' : 'bg-[#B08D57]'}`}
                aria-hidden
              />
              {health === null ? 'checking NASA catalogs…' : liveCatalogs ? 'Live NASA catalogs connected' : 'NASA catalogs: cached records'}
            </span>
            <span className="ml-auto hidden items-center gap-1.5 text-[11px] text-[#8A837B] sm:flex">
              <Mountain className="h-3.5 w-3.5 text-[#B08D57]" aria-hidden />
              flagship story: a real NISAR pair that honestly found nothing
            </span>
          </motion.div>

          <ol className="mt-7 grid gap-2 text-[12px] text-[#6F6862] sm:grid-cols-3 sm:gap-4" aria-label="How to use MOTUS">
            {[
              ['1', 'Choose an area', 'Pick a study area below. Every area is real NASA data — labels say whether it is ready to analyze or unavailable.'],
              ['2', 'Analyze it', 'One click runs the full analysis. Advanced settings stay out of the way until you want them.'],
              ['3', 'Understand the result', 'What was found, how certain it is, what could explain it, and what it does not prove.'],
            ].map(([n, title, desc]) => (
              <li key={n} className="flex gap-2.5 rounded-xl border border-[#EFEAE2] bg-white p-3.5">
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#1C1A17] font-mono text-[10px] font-semibold text-white" aria-hidden>
                  {n}
                </span>
                <span>
                  <span className="block text-[12.5px] font-semibold text-[#1C1A17]">{title}</span>
                  <span className="mt-0.5 block leading-4 text-[#8A837B]">{desc}</span>
                </span>
              </li>
            ))}
          </ol>
        </section>

        {/* ---------- Step 1 · Choose an area ---------- */}
        <section id="choose-area" aria-label="Step 1 — choose an area" className="mb-8 scroll-mt-20">
          <StepHeader
            n={1}
            title="Choose an area"
            note="Each card explains what the data are and whether you can run an analysis right now."
          />
          {datasets === null ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 6 }, (_, i) => (
                <div key={i} className="h-[150px] animate-pulse rounded-xl border border-[#EFEAE2] bg-white" />
              ))}
            </div>
          ) : datasets.length === 0 ? (
            <div className="rounded-xl border border-[#E7D9B8] bg-[#FCF8EE] p-5 text-[13px] leading-5 text-[#7A6438]" role="alert">
              The dataset list could not be loaded. Please reload the page and try again.
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {sortedDatasets!.map((d) => {
                const ready = readinessOf(d);
                return (
                  <button
                    key={d.id}
                    type="button"
                    onClick={() => setSelectedId(d.id)}
                    aria-pressed={d.id === selectedId}
                    className={`h-fit rounded-xl border bg-white p-4 text-left transition-all duration-150 ${
                      d.id === selectedId
                        ? 'border-[1.5px] border-[#1C1A17] shadow-[0_1px_8px_rgba(28,26,23,0.08)]'
                        : 'border-[#E7E2DA] hover:border-[#B8AFA2]'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <ReadinessBadge d={d} />
                    </div>
                    <div className="mt-2 line-clamp-2 min-h-[2.5em] text-[13px] font-medium leading-[1.25em] text-[#1C1A17]">
                      {d.title}
                    </div>
                    <p className="mt-1 line-clamp-2 text-[11.5px] leading-4 text-[#8A837B]">{ready.detail}</p>
                    <div className="mt-2 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.04em] text-[#8A837B]">
                      <MapPin className="h-3 w-3 shrink-0" aria-hidden />
                      {d.temporalBaselineDays > 0
                        ? `${d.temporalBaselineDays}-day pair · ${fmtDate(d.referenceTime)} → ${fmtDate(d.secondaryTime)}`
                        : 'time range shown after download'}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        {/* ---------- Workspace: Step 2 (run) · map · Step 3 (result) ---------- */}
        <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[minmax(0,1.85fr)_minmax(0,1fr)]">
          {/* Step 2 — Run the analysis (first on mobile, right column on desktop) */}
          <section id="step-run" aria-label="Step 2 — run the analysis" className="order-1 self-start scroll-mt-20 rounded-xl border border-[#E7E2DA] bg-white p-5 lg:order-2 lg:col-start-2 lg:row-start-1">
            <StepHeader n={2} title="Run the analysis" note="One click. The default settings are safe; technical options are hidden below." inside />
            {detail ? (
              <>
                <div className="mt-4 rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <DataClassBadge dataClass={detail.provenance.dataClass} mode={detail.mode} />
                  </div>
                  <h2 className="mt-2 text-[14.5px] font-semibold leading-5 text-[#1C1A17]">{detail.title}</h2>
                  <p className="mt-1 text-[12.5px] leading-[1.4rem] text-[#6F6862]">{detail.blurb}</p>
                </div>

                {detail.availability.state !== 'available' && !detail.availability.recorded && (
                  <div className="mt-3 rounded-lg border border-[#E5C4B7] bg-[#FBF3EF] p-3.5" role="alert">
                    <p className="flex items-center gap-1.5 text-[12.5px] font-semibold text-[#9E3B2C]">
                      <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
                      {selectedReadiness?.label} — analysis cannot run yet
                    </p>
                    <p className="mt-1.5 text-[12px] leading-[1.4rem] text-[#9E3B2C]">
                      {detail.availability.explanation}
                    </p>
                    {anyReady && (
                      <p className="mt-2 text-[12px] leading-[1.4rem] text-[#9E3B2C]">
                        Tip: choose a <strong>Ready to analyze</strong> area above — those work right now.
                      </p>
                    )}
                    <TokenPasteBox datasetTitle={detail.title} />
                  </div>
                )}

                {detail.availability.state !== 'available' && detail.availability.recorded && (
                  <div className="mt-3 rounded-lg border border-[#B08D57]/50 bg-[#F7F2E7] p-3.5" role="status">
                    <p className="text-[12.5px] font-semibold text-[#7A6438]">
                      The source data is no longer on this server — but the real analysis was recorded.
                    </p>
                    <p className="mt-1.5 text-[12px] leading-[1.4rem] text-[#7A6438]">
                      This 2.6 GB product was fully analysed here on {recordedRun?.recordedAt.slice(0, 10) ?? '2026-10-20'}.
                      You can view the recorded result — every figure comes from the real run, clearly labelled.
                      Re-running needs an Earthdata-authenticated re-download (see &ldquo;About this data&rdquo; below).
                    </p>
                    <Button
                      onClick={showRecorded}
                      disabled={!recordedRun}
                      className="mt-2.5 h-10 w-full gap-2 rounded-lg bg-[#7A6438] text-[13px] font-medium text-white hover:bg-[#8A7350] disabled:opacity-60"
                    >
                      <Mountain className="h-4 w-4" aria-hidden />
                      View the recorded result
                    </Button>
                    <TokenPasteBox datasetTitle={detail.title} />
                  </div>
                )}

                <Button
                  onClick={runAnalysis}
                  disabled={running || detail.availability.state !== 'available'}
                  className="mt-4 h-11 w-full gap-2 rounded-lg bg-[#1C1A17] text-[13.5px] font-medium text-white hover:bg-[#33302B] disabled:opacity-60"
                >
                  {running ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                      Analyzing… {elapsed}s
                    </>
                  ) : detail.availability.state !== 'available' ? (
                    <>
                      <AlertTriangle className="h-4 w-4" aria-hidden />
                      Data unavailable — choose another area
                    </>
                  ) : (
                    <>
                      <Play className="h-4 w-4" aria-hidden />
                      Analyze this area
                    </>
                  )}
                </Button>
                {detail.availability.state === 'available' && (
                  <p className="mt-2 text-center text-[11px] leading-4 text-[#8A837B]">
                    {detail.mode === 'evidence'
                      ? 'Assembles the real catalog records, official imagery and the model prediction.'
                      : 'Typically a few seconds; cross-checking earthquake records can take up to ~20 s.'}
                  </p>
                )}

                {running && (
                  <div className="mt-4 rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3.5" aria-live="polite">
                    <p className="flex items-center gap-2 text-[12.5px] font-medium text-[#1C1A17]">
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-[#B08D57]" aria-hidden />
                      Running the analysis
                    </p>
                    <p className="mt-1 text-[11px] leading-4 text-[#8A837B]">
                      The server runs all of the steps below in one pass — they complete together when the
                      result returns.
                    </p>
                    <ul className="mt-2.5 space-y-2">
                      {stages.map((s) => (
                        <li key={s.label} className="flex gap-2.5">
                          <span className="mt-1 h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-[#B08D57]" aria-hidden />
                          <span>
                            <span className="block text-[12px] font-medium leading-4 text-[#4A443C]">{s.label}</span>
                            <span className="mt-0.5 block text-[11px] leading-4 text-[#8A837B]">{s.detail}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <Collapsible className="mt-4 border-t border-[#EFEAE2] pt-3">
                  <CollapsibleTrigger className="group flex w-full items-center justify-between text-[12px] text-[#6F6862] hover:text-[#1C1A17]">
                    <span>Advanced settings</span>
                    <ChevronDown className="h-3.5 w-3.5 transition-transform duration-150 group-data-[state=open]:rotate-180" aria-hidden />
                  </CollapsibleTrigger>
                  <CollapsibleContent className="space-y-4 pt-3.5">
                    <p className="text-[11px] leading-4 text-[#8A837B]">
                      These control how strictly the analysis flags changes. The defaults are sensible;
                      change them only if you know why.
                    </p>
                    <div>
                      <div className="mb-1.5 flex items-center justify-between">
                        <MicroLabel>
                          Signal-quality threshold
                          <span className="ml-1.5 font-mono text-[9px] normal-case tracking-normal" title="Technical name: coherence threshold">(coherence)</span>
                        </MicroLabel>
                        <span className="font-mono text-[11px] tabular-nums text-[#4A443C]">{advanced.coherenceThreshold.toFixed(2)}</span>
                      </div>
                      <Slider
                        value={[advanced.coherenceThreshold]}
                        min={0.05}
                        max={0.9}
                        step={0.05}
                        onValueChange={(v) => setAdvanced((a) => ({ ...a, coherenceThreshold: v[0] }))}
                        aria-label="Coherence threshold"
                      />
                      <p className="mt-1.5 text-[10.5px] leading-3.5 text-[#8A837B]">
                        Pixels with a signal-quality (coherence) score below this are excluded as too unreliable to measure.
                      </p>
                      {(() => {
                        const sugg = selectedEntry?.suggestedParams?.coherenceThreshold;
                        if (sugg === undefined || Math.abs(sugg - advanced.coherenceThreshold) < 1e-9) return null;
                        return (
                          <p className="mt-1.5 text-[10.5px] leading-3 text-[#8A837B]">
                            Suggested starting value for this scene: {sugg.toFixed(2)} — the dataset notes explain why.
                          </p>
                        );
                      })()}
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <MicroLabel>
                        Minimum change to flag
                        <span className="ml-1.5 font-mono text-[9px] normal-case tracking-normal" title="Technical name: detection floor">(detection floor)</span>
                      </MicroLabel>
                      <Select
                        value={String(advanced.detectionFloor)}
                        onValueChange={(v) => setAdvanced((a) => ({ ...a, detectionFloor: Number(v) }))}
                      >
                        <SelectTrigger className="h-8 w-32 rounded-md border-[#D8D2C6] text-[12px]" aria-label="Detection floor">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {['0.005', '0.01', '0.02', '0.05'].map((v) => (
                            <SelectItem key={v} value={v}>{(Number(v) * 1000).toFixed(0)} mm</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <MicroLabel>
                        Strictness
                        <span className="ml-1.5 font-mono text-[9px] normal-case tracking-normal" title="Technical name: significance k·σ">(k·σ)</span>
                      </MicroLabel>
                      <Select
                        value={String(advanced.significanceK)}
                        onValueChange={(v) => setAdvanced((a) => ({ ...a, significanceK: Number(v) }))}
                      >
                        <SelectTrigger className="h-8 w-20 rounded-md border-[#D8D2C6] text-[12px]" aria-label="Significance k">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {['2', '3', '4'].map((v) => (
                            <SelectItem key={v} value={v}>{v}σ {v === '3' ? '(default)' : ''}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <MicroLabel>
                        Zero point
                        <span className="ml-1.5 font-mono text-[9px] normal-case tracking-normal" title="Technical name: reference region strategy">(reference)</span>
                      </MicroLabel>
                      <Select
                        value={advanced.referenceStrategy}
                        onValueChange={(v) => setAdvanced((a) => ({ ...a, referenceStrategy: v as AdvancedParams['referenceStrategy'] }))}
                      >
                        <SelectTrigger className="h-8 w-44 rounded-md border-[#D8D2C6] text-[12px]" aria-label="Reference region">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="farField">quiet frame edge (default)</SelectItem>
                          <SelectItem value="fullFrameMedian">whole-frame median</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <MicroLabel>Remove tilted background</MicroLabel>
                        <p className="mt-0.5 text-[10.5px] leading-3 text-[#8A837B]">can also remove real motion — off by default</p>
                      </div>
                      <Switch
                        checked={advanced.detrend}
                        onCheckedChange={(c) => setAdvanced((a) => ({ ...a, detrend: c }))}
                        aria-label="Planar detrend"
                      />
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setAdvanced({
                        ...DEFAULT_ADVANCED,
                        coherenceThreshold:
                          selectedEntry?.suggestedParams?.coherenceThreshold ?? DEFAULT_ADVANCED.coherenceThreshold,
                      })}
                      className="h-7 gap-1.5 px-2 text-[11px] font-normal text-[#8A837B] hover:text-[#1C1A17]"
                    >
                      <RotateCcw className="h-3 w-3" aria-hidden /> Reset to defaults
                    </Button>
                  </CollapsibleContent>
                </Collapsible>

                <DatasetDetailsCollapsible detail={detail} />
              </>
            ) : (
              <div className="mt-4 space-y-3">
                <Skeleton className="h-20 w-full" />
                <Skeleton className="h-11 w-full" />
                <Skeleton className="h-16 w-full" />
              </div>
            )}
          </section>

          {/* Map: second on mobile, left column spanning both rows on desktop */}
          <section id="map-section" aria-label="Study area map" className="order-2 scroll-mt-20 lg:order-1 lg:col-start-1 lg:row-span-2 lg:row-start-1">
            {detail ? (
              <div className="lg:sticky lg:top-20">
                <MapPanel
                  key={detail.id}
                  dataset={detail}
                  grids={grids}
                  gridsMeta={gridsMeta}
                  layer={layer}
                  opacity={opacity}
                  events={eventsForMap as never}
                  primaryEvent={primaryEvent as never}
                  granulePolys={granulePolys}
                  beforeAfter={beforeAfter}
                  wavelengthM={wavelengthM}
                  swipe={mapMode === 'swipe'}
                />
                {grids && detail && (
                  <div className="mt-3 rounded-xl border border-[#E7E2DA] bg-white px-3.5 py-3">
                    {beforeAfter && (
                      <div className="mb-3 rounded-lg border border-[#EFE5CC] bg-[#FCF8EE] px-3 py-2.5">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="text-[11px] font-semibold text-[#7A6438]">Map view</span>
                          <div role="tablist" aria-label="Map view mode" className="flex overflow-hidden rounded-md border border-[#E3D4AE]">
                            <button
                              type="button"
                              role="tab"
                              aria-selected={mapMode === 'swipe'}
                              onClick={() => setMapMode('swipe')}
                              className={`px-2.5 py-1 text-[11px] transition-colors ${
                                mapMode === 'swipe' ? 'bg-[#1C1A17] text-white' : 'bg-white text-[#6F6862] hover:bg-[#F7F4EE]'
                              }`}
                            >
                              Before / After
                            </button>
                            <button
                              type="button"
                              role="tab"
                              aria-selected={mapMode === 'single'}
                              onClick={() => setMapMode('single')}
                              className={`px-2.5 py-1 text-[11px] transition-colors ${
                                mapMode === 'single' ? 'bg-[#1C1A17] text-white' : 'bg-white text-[#6F6862] hover:bg-[#F7F4EE]'
                              }`}
                            >
                              Single layer
                            </button>
                          </div>
                        </div>
                        <p className="mt-2 text-[12px] leading-[1.5rem] text-[#4A443C]">
                          {mapMode === 'swipe' ? (
                            <>
                              Drag the divider to compare. NASA&rsquo;s radar took two snapshots of this area{' '}
                              <strong>{swipeDays ?? 0} days apart</strong> — the colored layer is what{' '}
                              {beforeAfter.prediction ? (
                                <>a physics model <strong>predicts</strong> happened in between (a labelled prediction, not a measurement).</>
                              ) : (
                                <>was <strong>measured</strong> to have changed in between.</>
                              )}
                              {windowQuake && (
                                <>
                                  {' '}Between those dates, a <strong>magnitude {windowQuake.magnitude.toFixed(1)}</strong> earthquake
                                  struck in this area (the red marker).
                                </>
                              )}
                            </>
                          ) : (
                            <>The full map, one layer — drag the map and zoom freely; the divider is off.</>
                          )}
                        </p>
                        <p className="mt-1 text-[10.5px] leading-4 text-[#8A837B]">
                          The photo underneath is a multi-date satellite composite (Esri) — it is not from the radar acquisition dates.
                        </p>
                      </div>
                    )}
                    <div role="tablist" aria-label="Map layer" className="flex flex-wrap overflow-hidden rounded-md border border-[#E7E2DA]">
                      {layerOptions.map((l) => (
                        <button
                          key={l}
                          role="tab"
                          aria-selected={layer === l}
                          onClick={() => setLayer(l)}
                          className={`flex-1 px-3 py-1.5 text-[11.5px] transition-colors ${
                            layer === l ? 'bg-[#1C1A17] text-white' : 'bg-white text-[#6F6862] hover:bg-[#F7F4EE]'
                          }`}
                        >
                          {layerLabel(l, isPredictionLayer, !!sbasResult)}
                        </button>
                      ))}
                    </div>
                    {sbasResult && layer === 'displacement' && (
                      <div className="mt-3 rounded-lg border border-[#EFE5CC] bg-[#FCF8EE] px-3 py-2.5">
                        <div className="flex items-center justify-between text-[11px] text-[#7A6438]">
                          <span className="font-semibold">Time slider — cumulative movement</span>
                          <span className="font-mono tabular-nums">
                            {new Date(sbasResult.epochs[sbasEpoch]).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                          </span>
                        </div>
                        <Slider
                          value={[sbasEpoch]}
                          min={0}
                          max={sbasResult.epochs.length - 1}
                          step={1}
                          onValueChange={(v) => setSbasEpoch(v[0])}
                          className="mt-2"
                          aria-label="Acquisition epoch"
                        />
                        <div className="mt-1 flex justify-between font-mono text-[9.5px] text-[#8A837B]">
                          <span>{new Date(sbasResult.epochs[0]).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</span>
                          <span>{new Date(sbasResult.epochs[sbasResult.epochs.length - 1]).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</span>
                        </div>
                      </div>
                    )}
                    {layer === 'displacement' && (
                      <label className="mt-2.5 flex items-center gap-2 text-[11px] text-[#8A837B]">
                        Layer opacity
                        <Slider
                          value={[Math.round(opacity * 100)]}
                          min={40}
                          max={100}
                          step={5}
                          onValueChange={(v) => setOpacity(v[0] / 100)}
                          className="w-24"
                          aria-label="Layer opacity"
                        />
                        <span className="w-8 font-mono tabular-nums text-[#4A443C]">{Math.round(opacity * 100)}%</span>
                      </label>
                    )}
                    <p className="mt-2.5 text-[11.5px] leading-4 text-[#6F6862]">
                      {layerInterpretation(layer, isPredictionLayer, !!sbasResult)}
                    </p>
                    {isPredictionLayer && (
                      <p className="mt-1.5 text-[10.5px] font-medium uppercase tracking-[0.06em] text-[#8F4A2B]">
                        Model prediction — not a measurement
                      </p>
                    )}
                    {/* export row — client-side, from the same decoded grids */}
                    <div className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-[#EFEAE2] pt-2.5">
                      <span className="mr-0.5 text-[10px] font-medium uppercase tracking-[0.06em] text-[#8A837B]">Download this layer</span>
                      <ExportChip onClick={exportGeotiff} label="GeoTIFF" title="Georeferenced TIFF — opens directly in QGIS / ArcGIS / GDAL">
                        <Download className="h-3 w-3" aria-hidden />
                      </ExportChip>
                      <ExportChip onClick={exportCsv} label="CSV" title="Sampled values with UTM + lat/lon coordinates">
                        <FileSpreadsheet className="h-3 w-3" aria-hidden />
                      </ExportChip>
                      <ExportChip onClick={exportPng} label="PNG" title="The map image exactly as rendered">
                        <ImageIcon className="h-3 w-3" aria-hidden />
                      </ExportChip>
                    </div>
                  </div>
                )}
                {!grids && (
                  <p className="mt-3 rounded-xl border border-[#E7E2DA] bg-white px-3.5 py-3 text-[11.5px] leading-4 text-[#6F6862]">
                    {result && isRecordedResult(result) ? (
                      <>
                        This is the real footprint of the analyzed granule, with the M{result.event?.magnitude.toFixed(2)} earthquake
                        that occurred inside it. No measurement layer is drawn: the 2.6 GB source product is no longer
                        on this server, so the recorded result shows figures and charts instead of a re-computed map.
                      </>
                    ) : (
                      <>
                        This is a real satellite view of the selected study area. The outlined frame marks the patch of
                        ground the radar will measure. After you run the analysis, the measurements appear here as
                        colors on the map.
                      </>
                    )}
                  </p>
                )}
              </div>
            ) : (
              <div className="flex min-h-[420px] items-center justify-center rounded-xl border border-[#E7E2DA] bg-white">
                <Loader2 className="h-5 w-5 animate-spin text-[#B8AFA2]" aria-label="Loading" />
              </div>
            )}
          </section>

          {/* Step 3 — Understand the result (below step 2 on mobile, right column on desktop) */}
          <section id="step-result" aria-label="Step 3 — understand the result" className="order-3 scroll-mt-20 lg:order-2 lg:col-start-2 lg:row-start-2">
            {error && (
              <div className="rounded-xl border border-[#E7E2DA] bg-white p-5">
                <Alert variant="destructive" className="rounded-lg border-[#E5C4B7] bg-[#FBF3EF] text-[#9E3B2C]">
                  <AlertTitle>The analysis could not be completed</AlertTitle>
                  <AlertDescription className="text-[12px] leading-[1.4rem] text-[#9E3B2C]">{error}</AlertDescription>
                </Alert>
              </div>
            )}
            {result && detail && (
              <motion.div
                key={isRecordedResult(result) ? `rec-${result.datasetId}` : (result as { runId?: string }).runId ?? result.datasetId}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, ease: 'easeOut' }}
                className="rounded-xl border border-[#E7E2DA] bg-white p-5"
              >
                <StepHeader n={3} title="Understand the result" note="Read this top to bottom: the verdict, the finding, the charts, the certainty, possible explanations, and the limits." inside />
                <div className="mt-4">
                  {isRecordedResult(result) ? (
                    <RecordedResults rec={result} />
                  ) : isSbasResult(result) ? (
                    <SbasResults result={result} />
                  ) : isEvidenceResult(result) ? (
                    <EvidenceResults result={result} onGranulePolys={setGranulePolys} />
                  ) : (
                    <QuantitativeResults
                      result={result}
                      rg={{ grids, gridsMeta, isPrediction: isPredictionLayer }}
                      acquisitionTimes={{
                        referenceTime: detail.provenance.referenceTime,
                        secondaryTime: detail.provenance.secondaryTime,
                      }}
                    />
                  )}
                </div>
              </motion.div>
            )}
            {!result && !error && !running && detail && (
              <div className="rounded-xl border border-dashed border-[#D8D2C6] bg-white/60 p-6 text-center">
                <p className="text-[13px] font-medium text-[#4A443C]">No result yet</p>
                <p className="mx-auto mt-1.5 max-w-xs text-[12px] leading-[1.45rem] text-[#8A837B]">
                  {detail.availability.recorded
                    ? 'View the recorded result (step 2) — the real analysis of this dataset, captured before the source data left this server.'
                    : 'Run the analysis above (step 2). When it finishes, the result appears here — the verdict, what was found, how certain it is, and what could explain it.'}
                </p>
              </div>
            )}
          </section>
        </div>

        {/* ---------- Honest scope note ---------- */}
        {/* ---------- Tsunami capability: ocean check + physics simulation ---------- */}
        <TsunamiPanel />

        <section id="scope-note" aria-label="About the results" className="mt-10 scroll-mt-20 rounded-xl border border-[#E7E2DA] bg-white p-5">
          <h2 className="text-[14px] font-semibold text-[#1C1A17]">What MOTUS can and cannot tell you</h2>
          <ul className="mt-2.5 grid gap-2 text-[12.5px] leading-[1.45rem] text-[#6F6862] sm:grid-cols-2">
            <li className="flex gap-2">
              <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#2C5F58]" aria-hidden />
              <span>It measures <em>apparent</em> movement toward or away from the satellite — one viewing direction, not a full 3-D motion.</span>
            </li>
            <li className="flex gap-2">
              <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#2C5F58]" aria-hidden />
              <span>It compares each pattern with earthquake records and models — a consistency check, not proof of cause.</span>
            </li>
            <li className="flex gap-2">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#B08D57]" aria-hidden />
              <span>It cannot always separate real ground motion from atmospheric effects in the radar signal.</span>
            </li>
            <li className="flex gap-2">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#B08D57]" aria-hidden />
              <span>One measurement pair cannot distinguish slow ongoing processes from one-off events.</span>
            </li>
          </ul>
          <p className="mt-3 text-[12px] text-[#8A837B]">
            Radar terminology is explained where it appears — look for{' '}
            <WhatIsThis label="&ldquo;What does this mean?&rdquo;">These expandable notes define technical terms such as coherence (how consistently the radar signal matches between observations), interferogram (a map made by comparing two radar observations), and LOS displacement (movement toward or away from the satellite&apos;s viewing direction).</WhatIsThis>
          </p>
        </section>

        {/* ---------- Find a good radar pair (live catalog tool) ---------- */}
        <PairFinder />
      </main>

      {/* ---------- Guided tour ---------- */}
      <StoryPicker
        open={storyPickerOpen}
        onOpenChange={setStoryPickerOpen}
        onPick={(storyIdx) => {
          setStoryPickerOpen(false);
          enterTourStep(storyIdx, 0);
        }}
      />
      {tour && (
        <TourCard
          story={TOUR_STORIES[tour.storyIdx]}
          stepIdx={tour.step}
          busy={tourBusy || running}
          onStep={(i) => enterTourStep(tour.storyIdx, i)}
          onExit={() => {
            setTour(null);
            setPendingTourAction(null);
            setTourBusy(false);
          }}
        />
      )}
    </>
  );
}

/** Small download chip used in the map layer card. */
function ExportChip({ onClick, label, title, children }: { onClick: () => void; label: string; title: string; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={`Download ${label} — ${title}`}
      className="inline-flex items-center gap-1 rounded-md border border-[#D8D2C6] bg-white px-2 py-1 text-[10.5px] font-medium text-[#4A443C] transition-colors hover:bg-[#F7F4EE] hover:text-[#1C1A17]"
    >
      {children}
      {label}
    </button>
  );
}

/** Numbered step heading used across the page. */
function StepHeader({ n, title, note, inside }: { n: 1 | 2 | 3; title: string; note?: string; inside?: boolean }) {
  return (
    <div className={inside ? '' : 'mb-4'}>
      <div className="flex items-center gap-2.5">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[#1C1A17] font-mono text-[11px] font-semibold text-white" aria-hidden>
          {n}
        </span>
        <h2 className="text-[16px] font-semibold tracking-[-0.005em] text-[#1C1A17]">{title}</h2>
      </div>
      {note && <p className="mt-1 pl-[34px] text-[12px] leading-[1.45rem] text-[#8A837B]">{note}</p>}
    </div>
  );
}

/** "About this data" — technical provenance behind a collapsible, off the default path. */
function DatasetDetailsCollapsible({ detail }: { detail: DatasetDetail }) {
  const [open, setOpen] = useState(false);
  const p = detail.provenance;
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mt-4 border-t border-[#EFEAE2] pt-3">
      <CollapsibleTrigger className="group flex w-full items-center justify-between text-[12px] text-[#6F6862] hover:text-[#1C1A17]">
        <span>About this data</span>
        <ChevronDown className="h-3.5 w-3.5 transition-transform duration-150 group-data-[state=open]:rotate-180" aria-hidden />
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-3">
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <Fact label="Sensor" value={p.platform} />
          <Fact label="Product" value={p.productType} />
          {p.referenceTime && <Fact label="Earlier image" value={fmtDateTime(p.referenceTime)} mono />}
          {p.secondaryTime && <Fact label="Later image" value={fmtDateTime(p.secondaryTime)} mono />}
          {detail.geometry && (
            <>
              <Fact label="Radar wavelength" value={`${(detail.geometry.wavelength * 100).toFixed(1)} cm (L band)`} mono />
              <Fact label="Viewing angle" value={`${detail.geometry.incidenceAngleDeg.toFixed(1)}° from vertical`} mono />
              <Fact label="Orbit / view" value={`${detail.geometry.passDirection} pass · ${detail.geometry.lookDirection}-looking`} />
            </>
          )}
          {detail.numberOfLooks !== undefined && (
            <Fact label="Averaged samples (looks)" value={String(detail.numberOfLooks)} mono />
          )}
        </div>
        {p.doi && (
          <a
            href={`https://doi.org/${p.doi}`}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-3 inline-block font-mono text-[10.5px] text-[#6F6862] underline decoration-[#D8D2C6] underline-offset-2 hover:text-[#1C1A17]"
          >
            DOI {p.doi}
          </a>
        )}
        {p.accessNotes && (
          <div className="mb-3 mt-3 flex items-start gap-2 rounded-md border border-[#E7D9B8] bg-[#FCF8EE] px-2.5 py-2 text-[11px] leading-4 text-[#7A6438]">
            <span>{p.accessNotes}</span>
          </div>
        )}
        <dl className="space-y-2 text-[11px] leading-4">
          <ProvenRow k="Provider" v={p.provider} />
          <ProvenRow k="Product ID" v={p.productId} mono />
          <ProvenRow k="Processing" v={p.processingSoftware ?? '—'} />
          <ProvenRow k="Processed at" v={p.processingTime ? fmtDateTime(p.processingTime) : '—'} />
          <ProvenRow k="Source" v={p.sourceUrl} link />
          {p.qualityNotes && (
            <div>
              <dt className="text-[10px] font-medium uppercase tracking-[0.06em] text-[#8A837B]">Quality flags</dt>
              <dd>
                <ul className="mt-1 list-inside list-disc space-y-0.5 text-[#6F6862]">
                  {p.qualityNotes.map((q, i) => (
                    <li key={i}>{q}</li>
                  ))}
                </ul>
              </dd>
            </div>
          )}
          {detail.availability.state !== 'available' &&
            (detail.availability.missingFiles.length > 0 || detail.availability.invalidFiles.length > 0) && (
              <div>
                <dt className="text-[10px] font-medium uppercase tracking-[0.06em] text-[#8A837B]">Missing / failed files</dt>
                <dd className="mt-1 font-mono text-[10px] leading-4 text-[#9E3B2C]/80">
                  {[...detail.availability.missingFiles,
                    ...detail.availability.invalidFiles.map((f) => `${f.file} (${f.reason})`)].join(' · ')}
                </dd>
              </div>
            )}
        </dl>
      </CollapsibleContent>
    </Collapsible>
  );
}

function Fact({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <MicroLabel>{label}</MicroLabel>
      <div className={`mt-0.5 truncate text-[12px] leading-4 text-[#1C1A17] ${mono ? 'font-mono tabular-nums' : ''}`} title={value}>
        {value}
      </div>
    </div>
  );
}

function ProvenRow({ k, v, mono, link }: { k: string; v: string; mono?: boolean; link?: boolean }) {
  return (
    <div className="flex gap-2">
      <dt className="w-24 shrink-0 text-[10px] font-medium uppercase leading-4 tracking-[0.06em] text-[#8A837B]">{k}</dt>
      <dd className={`min-w-0 break-words text-[#4A443C] ${mono ? 'font-mono text-[10px]' : ''}`}>
        {link ? (
          <a href={v} target="_blank" rel="noopener noreferrer" className="underline decoration-[#D8D2C6] underline-offset-2 hover:text-[#1C1A17]">
            {v.length > 60 ? v.slice(0, 57) + '…' : v}
          </a>
        ) : (
          v
        )}
      </dd>
    </div>
  );
}
