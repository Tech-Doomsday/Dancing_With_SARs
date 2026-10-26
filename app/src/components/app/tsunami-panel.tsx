'use client';

/**
 * Tsunami panel — the "did the ocean move?" workflow.
 *
 * Every number rendered here comes from a real API response: live/cached USGS
 * metadata, fetched NOAA DART records with de-tided residuals, or an actual
 * solver run executed on this server. Missing data renders as explicit
 * states. Model outputs are labelled as model outputs. The evidence verdict
 * cites its criteria and never claims official confirmation.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  Waves, Play, Loader2, CircleAlert, CheckCircle2, XCircle, Hourglass, Info,
  Database, ChevronDown, Activity, Compass, ShieldQuestion, Ship, ExternalLink,
} from 'lucide-react';
import { MapContainer, TileLayer, ImageOverlay, CircleMarker, Tooltip, ScaleControl } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RTooltip, ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import {
  TSUNAMI_LAYERS, decodeTsunamiTransport, renderTsunamiLayer, tsunamiGridBounds,
  type TsunamiLayerKind, type TsunamiGrids,
} from './tsunami-render';

/* ---------------- API payload types (mirror the server) ---------------- */

interface EventCard {
  id: string;
  usgsId: string;
  title: string;
  blurb: string;
  time: string;
  magnitude: number;
  lat: number;
  lon: number;
  depthKm: number;
  place: string;
  hasFiniteFault: boolean;
  officialUrl: string | null;
  officialSource: string | null;
  note: string;
  live: boolean;
  liveNote: string;
  nodalPlane?: { strike: number; dip: number; rake: number };
}

interface Screening {
  met: boolean;
  reasons: string[];
  notes: string[];
}

interface StationInfo {
  id: string;
  lat: number;
  lon: number;
  name: string;
  region: string;
  distKm: number;
}

interface DetectionPayload {
  stationId: string;
  availability: { state: 'available' | 'missing'; reason?: string };
  sigmaNoise?: number;
  triggerMs?: number | null;
  arrivalMin?: number | null;
  preOriginExcursion?: boolean;
  maxAmplitudeM?: number | null;
  dominantPeriodMin?: number | null;
  nSamples?: number;
  caveats?: string[];
  t?: number[];
  resid?: number[];
  provenance?: {
    provider: string;
    productId: string;
    sourceUrl: string;
    retrievedAt: string;
    observationStart: string;
    observationEnd: string;
    units: string;
    processing: string[];
  };
}

interface RunSummary {
  eventId: string;
  grid: { nx: number; ny: number; lat0: number; lon0: number; resArcmin: number };
  wallClockS: number;
  simDurationS: number;
  nSteps: number;
  minDtS: number;
  maxDtS: number;
  massDriftRelative: number;
  maxEtaM: number;
  sourceMaxAbsDzM: number;
  comparisons: Array<{
    stationId: string;
    obsArrivalMin: number | null;
    obsMaxAmplitudeM: number | null;
    simArrivalMin: number | null;
    simMaxAmplitudeM: number | null;
    arrivalResidualMin: number | null;
    amplitudeRatio: number | null;
    consistent: boolean | null;
    note: string;
  }>;
  gauges: Array<{ stationId: string; lat: number; lon: number; tMin: number[]; eta: number[] }>;
  provenance: Array<{
    provider: string;
    productId: string;
    productType: string;
    sourceUrl: string;
    retrievedAt: string;
    units: string;
    dataClass: string;
    processing: string[];
    qualityNotes: string[];
  }>;
  solverNotes: string[];
}

interface RunState {
  id: string;
  eventId: string;
  status: 'queued' | 'running' | 'ok' | 'error';
  params: { resArcmin: number; simHours: number; manningN: number; order: number };
  progress: { tSim: number; tTarget: number; steps: number; maxVel: number };
  error: string | null;
  summary?: RunSummary;
  evidence?: {
    level: string;
    headline: string;
    reasons: string[];
    caveats: string[];
    contributing: string[];
  };
  transport?: {
    maxEtaB64: string;
    arrivalB64: string;
    dzB64: string;
    bedB64: string;
    travelB64: string;
  };
}

/* ---------------- small building blocks ---------------- */

function fmtWhen(iso: string): string {
  return new Date(iso).toLocaleString('en-US', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC',
  }) + ' UTC';
}

function EvidenceBadge({ level }: { level: string }) {
  const map: Record<string, { label: string; cls: string; icon: React.ReactNode }> = {
    insufficient: { label: 'Insufficient data', cls: 'border-[#D8D2C6] bg-[#F7F4EE] text-[#6F6862]', icon: <Hourglass className="h-3.5 w-3.5" aria-hidden /> },
    'none-detected': { label: 'No tsunami evidence in the available observations', cls: 'border-[#D8D2C6] bg-[#F7F4EE] text-[#4A443C]', icon: <ShieldQuestion className="h-3.5 w-3.5" aria-hidden /> },
    possible: { label: 'Possible tsunami signal', cls: 'border-[#E5C4B7] bg-[#FBF3EF] text-[#9E3B2C]', icon: <CircleAlert className="h-3.5 w-3.5" aria-hidden /> },
    consistent: { label: 'Multiple observations consistent with a tsunami', cls: 'border-[#BFD3CD] bg-[#EFF5F3] text-[#2C5F58]', icon: <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> },
    'confirmed-official': { label: 'Cited to an official source', cls: 'border-[#B08D57]/50 bg-[#F7F2E7] text-[#7A6438]', icon: <Info className="h-3.5 w-3.5" aria-hidden /> },
  };
  const m = map[level] ?? map.insufficient;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12px] font-medium ${m.cls}`}>
      {m.icon}
      {m.label}
    </span>
  );
}

function DetectionChart({ det, originMs }: { det: DetectionPayload; originMs: number }) {
  if (!det.t || det.t.length < 2) return null;
  const data = det.t.map((t, i) => ({
    m: (t - originMs) / 60000,
    r: (det.resid?.[i] ?? NaN) * 100,
  }));
  const sigma = (det.sigmaNoise ?? NaN) * 100;
  return (
    <div className="h-[150px] w-full" aria-label={`De-tided residual chart for station ${det.stationId}`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 6, right: 6, bottom: 2, left: -18 }}>
          <CartesianGrid stroke="#EFEAE2" strokeDasharray="3 3" />
          <XAxis dataKey="m" type="number" domain={['dataMin', 'dataMax']} tick={{ fontSize: 9, fill: '#8A837B' }} tickFormatter={(v) => `${Math.round(v)}`} label={{ value: 'minutes after origin', position: 'insideBottom', offset: -2, fontSize: 9, fill: '#8A837B' }} height={22} />
          <YAxis tick={{ fontSize: 9, fill: '#8A837B' }} tickFormatter={(v) => v.toFixed(0)} width={38} label={{ value: 'cm', angle: -90, position: 'insideLeft', fontSize: 9, fill: '#8A837B' }} />
          <RTooltip
            formatter={(v: number | string) => [`${Number(v).toFixed(1)} cm`, 'de-tided residual']}
            labelFormatter={(l) => `${Number(l) >= 0 ? '+' : ''}${Number(l).toFixed(0)} min`}
            contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid #E7E2DA' }}
          />
          {Number.isFinite(sigma) && (
            <>
              <ReferenceLine y={5 * sigma} stroke="#9E3B2C" strokeDasharray="4 3" strokeWidth={1} />
              <ReferenceLine y={-5 * sigma} stroke="#9E3B2C" strokeDasharray="4 3" strokeWidth={1} />
            </>
          )}
          {det.arrivalMin !== null && (
            <ReferenceLine x={det.arrivalMin} stroke="#B08D57" strokeWidth={1.5} label={{ value: 'detected', fontSize: 9, fill: '#7A6438', position: 'top' }} />
          )}
          <ReferenceLine x={0} stroke="#4A443C" strokeWidth={1} />
          <Line type="monotone" dataKey="r" stroke="#2C5F58" strokeWidth={1.4} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ---------------- the panel ---------------- */

export default function TsunamiPanel() {
  const [events, setEvents] = useState<EventCard[] | null>(null);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [selected, setSelected] = useState<EventCard | null>(null);
  const [screening, setScreening] = useState<Screening | null>(null);
  const [stations, setStations] = useState<StationInfo[] | null>(null);
  const [detections, setDetections] = useState<DetectionPayload[] | null>(null);
  const [obsLoading, setObsLoading] = useState(false);
  const [obsError, setObsError] = useState<string | null>(null);
  const [resArcmin, setResArcmin] = useState('2');
  const [simHours, setSimHours] = useState('4');
  const [run, setRun] = useState<RunState | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [layer, setLayer] = useState<TsunamiLayerKind>('maxEta');
  const [showProv, setShowProv] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch('/api/tsunami/events');
        const j = await res.json();
        if (cancelled) return;
        if (j.ok) setEvents(j.events as EventCard[]);
        else setEventsError(j.error ?? 'event list failed');
      } catch (err) {
        if (!cancelled) setEventsError((err as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const selectEvent = useCallback(async (ev: EventCard) => {
    setSelected(ev);
    setScreening(null);
    setStations(null);
    setDetections(null);
    setObsError(null);
    setRun(null);
    setRunError(null);
    try {
      const res = await fetch(`/api/tsunami/events/${ev.id}`);
      const j = await res.json();
      if (j.ok) {
        setScreening(j.screening as Screening);
        setStations(j.stations as StationInfo[]);
      }
    } catch {
      // station list is optional context; observation fetch will surface errors
    }
  }, []);

  const fetchObservations = useCallback(async () => {
    if (!selected) return;
    setObsLoading(true);
    setObsError(null);
    try {
      const res = await fetch(`/api/tsunami/observations?event=${selected.id}`);
      const j = await res.json();
      if (!j.ok) throw new Error(j.error ?? 'observation fetch failed');
      setDetections(j.detections as DetectionPayload[]);
    } catch (err) {
      setObsError((err as Error).message);
    } finally {
      setObsLoading(false);
    }
  }, [selected]);

  const startSimulation = useCallback(async () => {
    if (!selected) return;
    setRunError(null);
    setRun(null);
    try {
      const res = await fetch('/api/tsunami/simulate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          eventId: selected.id,
          params: { resArcmin: Number(resArcmin), simHours: Number(simHours), order: 2 },
        }),
      });
      const j = await res.json();
      if (!j.ok) throw new Error(j.error ?? 'simulation start failed');
      const runId = j.runId as string;
      // Self-scheduling poll: the next request only fires after the previous
      // one settles. A plain setInterval with a busy server lets requests
      // pile up unboundedly (browser caps ~6/host) and freezes the tab —
      // a real bug caught during E2E.
      const stopRef = { stopped: false };
      stopPollRef.current = stopRef;
      const poll = async () => {
        if (stopRef.stopped) return;
        const t0 = performance.now();
        try {
          const r = await fetch(`/api/tsunami/runs/${runId}`);
          const rj = await r.json();
          if (rj.ok) {
            setRun(rj.run as RunState);
            if (rj.run.status === 'ok' || rj.run.status === 'error') {
              stopRef.stopped = true;
              return;
            }
          }
        } catch {
          // keep polling through transient errors
        }
        const elapsed = performance.now() - t0;
        setTimeout(poll, Math.max(1500, elapsed));
      };
      void poll();
    } catch (err) {
      setRunError((err as Error).message);
    }
  }, [selected, resArcmin, simHours]);

  const stopPollRef = useRef<{ stopped: boolean } | null>(null);

  useEffect(
    () => () => {
      if (stopPollRef.current) stopPollRef.current.stopped = true;
    },
    [],
  );

  const grids: TsunamiGrids | null = useMemo(() => {
    if (!run?.transport || !run.summary) return null;
    try {
      return decodeTsunamiTransport(run.transport, run.summary.grid);
    } catch {
      return null;
    }
  }, [run]);

  const overlay = useMemo(() => {
    if (!grids) return null;
    try {
      return renderTsunamiLayer(grids, layer);
    } catch {
      return null;
    }
  }, [grids, layer]);

  const layerDef = TSUNAMI_LAYERS.find((l) => l.key === layer)!;
  const originMs = selected ? Date.parse(selected.time) : 0;
  const busy = run?.status === 'running' || run?.status === 'queued';
  const progressPct = run ? Math.min(100, (run.progress.tSim / Math.max(1, run.progress.tTarget)) * 100) : 0;

  return (
    <section id="tsunami-section" aria-label="Tsunami check" className="mt-12 scroll-mt-20">
      <div className="mb-5 flex items-start gap-3">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[#1C1A17]">
          <Waves className="h-4 w-4 text-white" aria-hidden />
        </span>
        <div>
          <h2 className="text-[19px] font-semibold tracking-[-0.01em] text-[#1C1A17]">
            Did the ocean move? Tsunami check.
          </h2>
          <p className="mt-1 max-w-3xl text-[13px] leading-5 text-[#6F6862]">
            The same earthquakes this app watches from space can move the ocean. This section
            checks <strong>real deep-ocean pressure records</strong> (NOAA DART buoys) for a
            tsunami signature and runs a physics simulation — shallow-water equations on real
            ETOPO1 bathymetry — to test whether the observations and the model tell the same
            story. It tells you honestly when they don&apos;t.
          </p>
        </div>
      </div>

      {/* Step 1 — event */}
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[#8A837B]">Step 1 · Choose a real earthquake</h3>
      {events === null && !eventsError && (
        <div className="grid gap-3 sm:grid-cols-3">
          {[0, 1, 2].map((i) => <div key={i} className="h-[120px] animate-pulse rounded-xl border border-[#EFEAE2] bg-white" />)}
        </div>
      )}
      {eventsError && (
        <div className="rounded-xl border border-[#E5C4B7] bg-[#FBF3EF] p-4 text-[12.5px] text-[#9E3B2C]" role="alert">
          Could not load the event list: {eventsError}
        </div>
      )}
      {events && (
        <div className="grid gap-3 sm:grid-cols-3">
          {events.map((ev) => (
            <button
              key={ev.id}
              type="button"
              onClick={() => selectEvent(ev)}
              aria-pressed={selected?.id === ev.id}
              className={`h-fit rounded-xl border bg-white p-4 text-left transition-all duration-150 ${
                selected?.id === ev.id
                  ? 'border-[1.5px] border-[#1C1A17] shadow-[0_1px_8px_rgba(28,26,23,0.08)]'
                  : 'border-[#E7E2DA] hover:border-[#B8AFA2]'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="rounded-full border border-[#D8D2C6] bg-[#F7F4EE] px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.04em] text-[#6F6862]">
                  M {ev.magnitude.toFixed(1)}
                </span>
                <span className={`inline-flex items-center gap-1 font-mono text-[9.5px] uppercase tracking-[0.04em] ${ev.live ? 'text-[#2C5F58]' : 'text-[#B08D57]'}`}>
                  <span className={`inline-block h-1.5 w-1.5 rounded-full ${ev.live ? 'bg-[#2C5F58]' : 'bg-[#B08D57]'}`} aria-hidden />
                  {ev.live ? 'USGS live' : 'curated record'}
                </span>
              </div>
              <div className="mt-2 line-clamp-2 min-h-[2.5em] text-[13px] font-medium leading-[1.25em] text-[#1C1A17]">{ev.title}</div>
              <p className="mt-1 line-clamp-2 text-[11.5px] leading-4 text-[#8A837B]">{ev.blurb}</p>
              <div className="mt-2 font-mono text-[10px] uppercase tracking-[0.04em] text-[#8A837B]">
                {fmtWhen(ev.time)} · depth {ev.depthKm.toFixed(0)} km
              </div>
            </button>
          ))}
        </div>
      )}

      {selected && (
        <div className="mt-4 rounded-xl border border-[#E7E2DA] bg-white p-5">
          {/* screening */}
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <div>
              <h4 className="text-[13px] font-semibold text-[#1C1A17]">Earthquake facts</h4>
              <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[12px]">
                <dt className="text-[#8A837B]">Origin time</dt><dd className="font-mono text-[#4A443C]">{fmtWhen(selected.time)}</dd>
                <dt className="text-[#8A837B]">Epicentre</dt><dd className="font-mono text-[#4A443C]">{selected.lat.toFixed(3)}°, {selected.lon.toFixed(3)}°</dd>
                <dt className="text-[#8A837B]">Depth</dt><dd className="font-mono text-[#4A443C]">{selected.depthKm.toFixed(1)} km</dd>
                {selected.nodalPlane && (
                  <dt className="text-[#8A837B]">Mechanism</dt>
                )}
                {selected.nodalPlane && (
                  <dd className="font-mono text-[#4A443C]">strike {selected.nodalPlane.strike.toFixed(0)}° / dip {selected.nodalPlane.dip.toFixed(0)}° / rake {selected.nodalPlane.rake.toFixed(0)}°</dd>
                )}
                <dt className="text-[#8A837B]">Source model</dt>
                <dd className="text-[#4A443C]">{selected.hasFiniteFault ? 'USGS finite-fault inversion' : 'moment-tensor plane + scaling (approximation)'}</dd>
              </dl>
              {selected.officialUrl && (
                <a href={selected.officialUrl} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1 text-[11.5px] text-[#B08D57] underline decoration-dotted underline-offset-2 hover:text-[#7A6438]">
                  <ExternalLink className="h-3 w-3 shrink-0" aria-hidden /> official record: {selected.officialSource}
                </a>
              )}
            </div>
            <div className="rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3.5">
              <h4 className="text-[13px] font-semibold text-[#1C1A17]">Could this earthquake make a tsunami?</h4>
              <p className="mt-1 text-[11px] leading-4 text-[#8A837B]">
                A screening heuristic — not a verdict. Only ocean observations (below) can show
                whether a wave actually happened.
              </p>
              {screening ? (
                <ul className="mt-2 space-y-1 text-[11.5px] leading-4">
                  {screening.reasons.map((r) => (
                    <li key={r} className="flex gap-1.5 text-[#4A443C]"><CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-[#2C5F58]" aria-hidden />{r}</li>
                  ))}
                  {screening.notes.map((r) => (
                    <li key={r} className="flex gap-1.5 text-[#8A837B]"><Info className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />{r}</li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-[11.5px] text-[#8A837B]">loading…</p>
              )}
            </div>
          </div>

          {/* Step 2 — observations */}
          <h3 className="mb-2 mt-5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[#8A837B]">Step 2 · Check the real ocean-sensor records</h3>
          {stations && (
            <p className="mb-2 text-[12px] leading-5 text-[#6F6862]">
              {stations.length} DART deep-ocean pressure stations within 2,200 km of the
              epicentre: {stations.map((s) => `${s.id} (${s.distKm.toLocaleString()} km)`).join(' · ')}.
            </p>
          )}
          <Button
            onClick={fetchObservations}
            disabled={obsLoading}
            className="h-10 gap-2 rounded-lg bg-[#1C1A17] px-4 text-[13px] font-medium text-white hover:bg-[#33302B]"
          >
            {obsLoading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Activity className="h-4 w-4" aria-hidden />}
            {obsLoading ? 'Fetching & de-tiding records…' : detections ? 'Re-check observations' : 'Check the observations'}
          </Button>
          {obsError && (
            <p className="mt-2 rounded-lg border border-[#E5C4B7] bg-[#FBF3EF] p-2.5 text-[12px] text-[#9E3B2C]" role="alert">{obsError}</p>
          )}
          {detections && (
            <div className="mt-3 grid gap-3 lg:grid-cols-2">
              {detections.map((d) => {
                const ok = d.availability.state === 'available';
                const trig = d.triggerMs !== null && d.triggerMs !== undefined;
                return (
                  <div key={d.stationId} className={`min-w-0 overflow-hidden rounded-lg border p-3.5 ${ok ? (trig ? 'border-[#E5C4B7] bg-[#FBF3EF]' : 'border-[#E7E2DA] bg-white') : 'border-dashed border-[#D8D2C6] bg-[#F7F4EE]'}`}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-1.5 font-mono text-[12px] font-semibold text-[#1C1A17]">
                        <Ship className="h-3.5 w-3.5 text-[#6F6862]" aria-hidden />
                        DART {d.stationId}
                      </span>
                      {!ok ? (
                        <span className="inline-flex items-center gap-1 rounded-full border border-[#D8D2C6] px-2 py-0.5 text-[10px] text-[#8A837B]">
                          <XCircle className="h-3 w-3" aria-hidden /> no usable record
                        </span>
                      ) : trig ? (
                        <span className="inline-flex items-center gap-1 rounded-full border border-[#E5C4B7] bg-white px-2 py-0.5 text-[10px] font-medium text-[#9E3B2C]">
                          <CircleAlert className="h-3 w-3" aria-hidden /> wave detected
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-full border border-[#D8D2C6] bg-white px-2 py-0.5 text-[10px] text-[#6F6862]">
                          <ShieldQuestion className="h-3 w-3" aria-hidden /> no signal above threshold
                        </span>
                      )}
                    </div>
                    {!ok ? (
                      <p className="mt-1.5 text-[11.5px] leading-4 text-[#8A837B]">{d.availability.reason ?? 'record unavailable'}</p>
                    ) : (
                      <>
                        <p className="mt-1 text-[11.5px] leading-4 text-[#4A443C]">
                          {trig ? (
                            <>
                              Anomalous wave <strong>detected {(d.arrivalMin ?? 0) >= 0 ? '+' : ''}{(d.arrivalMin ?? 0).toFixed(0)} min</strong> after the origin,
                              max amplitude <strong>{((d.maxAmplitudeM ?? 0) * 100).toFixed(1)} cm</strong>
                              {d.dominantPeriodMin ? <> over the noise, dominant period ≈ {d.dominantPeriodMin.toFixed(0)} min</> : null}.
                              Noise floor σ = {((d.sigmaNoise ?? 0) * 100).toFixed(2)} cm; trigger = 5σ.
                            </>
                          ) : (
                            <>The de-tided record stays within 5σ (σ = {((d.sigmaNoise ?? 0) * 100).toFixed(2)} cm) of quiet for the whole window — no anomalous wave at this station.</>
                          )}
                          {d.preOriginExcursion && <> <em className="text-[#9E3B2C]">A pre-origin excursion was flagged — likely the seismic phase on the pressure sensor, not the water wave.</em></>}
                        </p>
                        {d.caveats && d.caveats.length > 0 && (
                          <ul className="mt-1 list-inside list-disc text-[10.5px] leading-3.5 text-[#8A837B]">
                            {d.caveats.map((c) => <li key={c}>{c}</li>)}
                          </ul>
                        )}
                        <DetectionChart det={d} originMs={originMs} />
                        {d.provenance && (
                          <p className="mt-1 text-[9.5px] leading-3 text-[#A39B90]">
                            measured · {d.provenance.productId} · {d.provenance.observationStart.slice(0, 10)} → {d.provenance.observationEnd.slice(0, 10)} · retrieved {d.provenance.retrievedAt.slice(0, 16)}Z · {d.provenance.processing.slice(-2).join(' · ')}
                          </p>
                        )}
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {/* Step 3 — simulation */}
          <h3 className="mb-2 mt-5 text-[11px] font-semibold uppercase tracking-[0.08em] text-[#8A837B]">Step 3 · Run the physics simulation</h3>
          <p className="mb-2.5 max-w-3xl text-[12px] leading-5 text-[#6F6862]">
            Nonlinear shallow-water equations solved with a well-balanced finite-volume scheme on
            real ETOPO1 bathymetry, started from the earthquake&apos;s seafloor displacement
            (Okada dislocations + slope correction + Kajiura water-column filter). Runs on this
            server — expect about 1–4 minutes.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-[12px] text-[#4A443C]">
              <span className="text-[#8A837B]">Grid</span>
              <Select value={resArcmin} onValueChange={setResArcmin}>
                <SelectTrigger className="h-9 w-40 rounded-md border-[#D8D2C6] text-[12px]" aria-label="Grid resolution">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="4">4′ coarse (fastest)</SelectItem>
                  <SelectItem value="2">2′ regional (default)</SelectItem>
                  <SelectItem value="1">1′ detailed (slow)</SelectItem>
                </SelectContent>
              </Select>
            </label>
            <label className="flex items-center gap-2 text-[12px] text-[#4A443C]">
              <span className="text-[#8A837B]">Duration</span>
              <Select value={simHours} onValueChange={setSimHours}>
                <SelectTrigger className="h-9 w-36 rounded-md border-[#D8D2C6] text-[12px]" aria-label="Simulation duration">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="2">2 hours</SelectItem>
                  <SelectItem value="4">4 hours</SelectItem>
                  <SelectItem value="6">6 hours</SelectItem>
                </SelectContent>
              </Select>
            </label>
            <Button
              onClick={startSimulation}
              disabled={busy}
              className="h-10 gap-2 rounded-lg bg-[#2C5F58] px-4 text-[13px] font-medium text-white hover:bg-[#234B45] disabled:opacity-60"
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
              {busy ? 'Simulating…' : 'Run the simulation'}
            </Button>
          </div>
          {runError && <p className="mt-2 rounded-lg border border-[#E5C4B7] bg-[#FBF3EF] p-2.5 text-[12px] text-[#9E3B2C]" role="alert">{runError}</p>}

          {run && (run.status === 'running' || run.status === 'queued') && (
            <div className="mt-3 rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3.5" aria-live="polite">
              <div className="flex items-center justify-between text-[12px] text-[#4A443C]">
                <span className="flex items-center gap-2 font-medium">
                  <Compass className="h-3.5 w-3.5 animate-spin text-[#B08D57]" aria-hidden />
                  Computing the wave field on this server
                </span>
                <span className="font-mono text-[11px] tabular-nums">{progressPct.toFixed(0)}%</span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#EFEAE2]">
                <div className="h-full rounded-full bg-[#2C5F58] transition-all duration-500" style={{ width: `${progressPct}%` }} />
              </div>
              <p className="mt-1.5 font-mono text-[10.5px] text-[#8A837B]">
                model time { (run.progress.tSim / 60).toFixed(0) } / { (run.progress.tTarget / 60).toFixed(0) } min · {run.progress.steps.toLocaleString()} steps · max velocity {run.progress.maxVel.toFixed(1)} m/s
              </p>
            </div>
          )}

          {run?.status === 'error' && (
            <div className="mt-3 rounded-lg border border-[#E5C4B7] bg-[#FBF3EF] p-3.5 text-[12.5px] text-[#9E3B2C]" role="alert">
              <p className="flex items-center gap-1.5 font-semibold"><XCircle className="h-4 w-4 shrink-0" aria-hidden /> The simulation failed</p>
              <p className="mt-1 font-mono text-[11px]">{run.error}</p>
            </div>
          )}

          {/* Results */}
          {run?.status === 'ok' && run.summary && run.evidence && grids && (
            <div className="mt-4 space-y-4">
              <div className="rounded-xl border border-[#E7E2DA] bg-white p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h4 className="text-[14px] font-semibold text-[#1C1A17]">Evidence verdict</h4>
                  <EvidenceBadge level={run.evidence.level} />
                </div>
                <p className="mt-2 text-[13px] leading-5 text-[#4A443C]">{run.evidence.headline}</p>
                {run.evidence.reasons.length > 0 && (
                  <ul className="mt-2 space-y-1 text-[12px] leading-4 text-[#4A443C]">
                    {run.evidence.reasons.map((r) => <li key={r} className="flex gap-1.5"><CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-[#2C5F58]" aria-hidden />{r}</li>)}
                  </ul>
                )}
                {run.evidence.caveats.length > 0 && (
                  <ul className="mt-2 space-y-1 text-[11.5px] leading-4 text-[#8A837B]">
                    {run.evidence.caveats.map((c) => <li key={c} className="flex gap-1.5"><Info className="mt-0.5 h-3 w-3 shrink-0 shrink-0" aria-hidden />{c}</li>)}
                  </ul>
                )}
                {selected.officialUrl && (
                  <p className="mt-2 text-[11.5px] text-[#8A837B]">
                    Official status for this event: see the{' '}
                    <a href={selected.officialUrl} target="_blank" rel="noopener noreferrer" className="text-[#B08D57] underline decoration-dotted underline-offset-2">{selected.officialSource}</a>{' '}
                    — this system never asserts it.
                  </p>
                )}
              </div>

              {/* map */}
              <div className="overflow-hidden rounded-xl border border-[#E7E2DA] bg-white">
                <div className="flex flex-wrap items-center gap-2 border-b border-[#EFEAE2] p-3">
                  <span className="text-[12px] font-semibold text-[#1C1A17]">Model result map</span>
                  <span className="rounded-full border border-[#B08D57]/50 bg-[#F7F2E7] px-2 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.04em] text-[#7A6438]">model output</span>
                  <div className="ml-auto flex flex-wrap gap-1">
                    {TSUNAMI_LAYERS.map((l) => (
                      <button
                        key={l.key}
                        type="button"
                        onClick={() => setLayer(l.key)}
                        aria-pressed={layer === l.key}
                        className={`rounded-full border px-2.5 py-1 text-[10.5px] transition-colors ${
                          layer === l.key ? 'border-[#1C1A17] bg-[#1C1A17] text-white' : 'border-[#D8D2C6] bg-white text-[#6F6862] hover:border-[#B8AFA2]'
                        }`}
                      >
                        {l.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="relative h-[380px] w-full sm:h-[440px]">
                  <MapContainer
                    center={[selected.lat, selected.lon]}
                    zoom={5}
                    scrollWheelZoom={false}
                    className="h-full w-full"
                    attributionControl={false}
                  >
                    <TileLayer
                      url="https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}"
                    />
                    <TileLayer
                      url="https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Reference/MapServer/tile/{z}/{y}/{x}"
                    />
                    {overlay?.dataUrl && (
                      <ImageOverlay url={overlay.dataUrl} bounds={tsunamiGridBounds(grids)} opacity={0.78} />
                    )}
                    <CircleMarker
                      center={[selected.lat, selected.lon]}
                      radius={7}
                      pathOptions={{ color: '#9E3B2C', fillColor: '#9E3B2C', fillOpacity: 0.9, weight: 2 }}
                    >
                      <Tooltip>epicentre (USGS)</Tooltip>
                    </CircleMarker>
                    {run.summary.gauges.map((g) => {
                      const cmp = run.summary!.comparisons.find((c) => c.stationId === g.stationId);
                      const color = cmp?.consistent === true ? '#2C5F58' : cmp?.consistent === false ? '#9E3B2C' : '#B08D57';
                      return (
                        <CircleMarker key={g.stationId} center={[g.lat, g.lon]} radius={6} pathOptions={{ color, fillColor: color, fillOpacity: 0.85, weight: 2 }}>
                          <Tooltip>
                            DART {g.stationId} — {cmp?.consistent === true ? 'observation consistent' : cmp?.consistent === false ? 'observation not consistent' : 'no comparison'}
                          </Tooltip>
                        </CircleMarker>
                      );
                    })}
                    <ScaleControl imperial={false} />
                  </MapContainer>
                  <div className="pointer-events-none absolute bottom-3 left-3 z-[500] rounded-lg border bg-white/95 px-3 py-2 shadow-sm">
                    <div className="text-[10px] uppercase tracking-[0.08em] text-[#8A837B]">{layerDef.label} ({layerDef.unit})</div>
                    <div className="mt-1 flex items-center gap-2">
                      <span className="font-mono text-[10px] text-[#6F6862]">{fmtRange(overlay, 0)}</span>
                      <div className="h-2 w-28 rounded-sm border border-[#E7E2DA]" style={{ background: layerGradient(layer) }} />
                      <span className="font-mono text-[10px] text-[#6F6862]">{fmtRange(overlay, 1)}</span>
                    </div>
                    <div className="mt-1 max-w-[280px] text-[10px] leading-3.5 text-[#8A837B]">{layerDef.plain}</div>
                  </div>
                </div>
              </div>

              {/* station comparisons */}
              <div className="rounded-xl border border-[#E7E2DA] bg-white p-4">
                <h4 className="text-[14px] font-semibold text-[#1C1A17]">Observation vs model, station by station</h4>
                <p className="mt-1 text-[11.5px] leading-4 text-[#8A837B]">
                  Consistency tolerances: arrival within ±15 min and amplitude within a factor 3 —
                  documented diagnostics, not validated error bars.
                </p>
                <div className="mt-2.5 overflow-x-auto">
                  <table className="w-full min-w-[640px] text-left text-[11.5px]">
                    <thead>
                      <tr className="border-b border-[#EFEAE2] text-[10px] uppercase tracking-[0.04em] text-[#8A837B]">
                        <th className="py-1.5 pr-3 font-medium">Station</th>
                        <th className="py-1.5 pr-3 font-medium">Observed arrival</th>
                        <th className="py-1.5 pr-3 font-medium">Model arrival</th>
                        <th className="py-1.5 pr-3 font-medium">Observed max</th>
                        <th className="py-1.5 pr-3 font-medium">Model max</th>
                        <th className="py-1.5 pr-3 font-medium">Verdict</th>
                      </tr>
                    </thead>
                    <tbody>
                      {run.summary.comparisons.map((c) => (
                        <tr key={c.stationId} className="border-b border-[#F5F1E8] align-top">
                          <td className="py-2 pr-3 font-mono font-semibold text-[#1C1A17]">{c.stationId}</td>
                          <td className="py-2 pr-3 font-mono text-[#4A443C]">{c.obsArrivalMin === null ? '—' : `+${c.obsArrivalMin.toFixed(0)} min`}</td>
                          <td className="py-2 pr-3 font-mono text-[#4A443C]">{c.simArrivalMin === null ? '—' : `+${c.simArrivalMin.toFixed(0)} min`}</td>
                          <td className="py-2 pr-3 font-mono text-[#4A443C]">{c.obsMaxAmplitudeM === null ? '—' : `${(c.obsMaxAmplitudeM * 100).toFixed(1)} cm`}</td>
                          <td className="py-2 pr-3 font-mono text-[#4A443C]">{c.simMaxAmplitudeM === null ? '—' : `${(c.simMaxAmplitudeM * 100).toFixed(1)} cm`}</td>
                          <td className="py-2 pr-3">
                            {c.consistent === true ? (
                              <span className="inline-flex items-center gap-1 font-medium text-[#2C5F58]"><CheckCircle2 className="h-3 w-3" aria-hidden />consistent</span>
                            ) : c.consistent === false ? (
                              <span className="inline-flex items-center gap-1 font-medium text-[#9E3B2C]"><XCircle className="h-3 w-3" aria-hidden />not consistent</span>
                            ) : (
                              <span className="text-[#8A837B]">no comparison</span>
                            )}
                            <span className="block text-[10.5px] leading-3.5 text-[#8A837B]">{c.note}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mt-3 grid gap-3 lg:grid-cols-2">
                  {run.summary.gauges.map((g) => {
                    const det = detections?.find((d) => d.stationId === g.stationId);
                    return (
                      <div key={g.stationId} className="min-w-0 overflow-hidden rounded-lg border border-[#EFEAE2] p-3">
                        <div className="mb-1 flex items-center justify-between">
                          <span className="font-mono text-[11.5px] font-semibold text-[#1C1A17]">DART {g.stationId} — modelled η (tan) vs observed residual (teal)</span>
                        </div>
                        <GaugeChartObs gauge={g} det={det} originMs={originMs} />
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* run facts + provenance */}
              <Collapsible open={showProv} onOpenChange={setShowProv} className="rounded-xl border border-[#E7E2DA] bg-white">
                <CollapsibleTrigger className="group flex w-full items-center justify-between p-4 text-[13px] font-semibold text-[#1C1A17]">
                  <span className="flex items-center gap-2">
                    <Database className="h-4 w-4 text-[#B08D57]" aria-hidden />
                    How this run was computed — data, parameters, quality
                  </span>
                  <ChevronDown className="h-4 w-4 transition-transform duration-150 group-data-[state=open]:rotate-180" aria-hidden />
                </CollapsibleTrigger>
                <CollapsibleContent className="space-y-3 px-4 pb-4">
                  <div className="grid gap-3 sm:grid-cols-3">
                    <Fact label="Grid" value={`${run.summary.grid.nx}×${run.summary.grid.ny} @ ${run.summary.grid.resArcmin}′`} />
                    <Fact label="Model time" value={`${(run.summary.simDurationS / 3600).toFixed(1)} h in ${run.summary.wallClockS.toFixed(0)} s wall`} />
                    <Fact label="Time steps" value={`${run.summary.nSteps.toLocaleString()} (Δt ${(run.summary.minDtS).toFixed(1)}–${run.summary.maxDtS.toFixed(1)} s)`} />
                    <Fact label="Mass drift" value={`${(run.summary.massDriftRelative * 100).toExponential(1)} %`} />
                    <Fact label="Max modelled |η|" value={`${run.summary.maxEtaM.toFixed(2)} m`} />
                    <Fact label="Max seafloor shift" value={`${run.summary.sourceMaxAbsDzM.toFixed(2)} m`} />
                  </div>
                  <div>
                    <h5 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-[#8A837B]">Solver notes</h5>
                    <ul className="mt-1 space-y-0.5 text-[11px] leading-4 text-[#4A443C]">
                      {run.summary.solverNotes.map((n) => <li key={n}>· {n}</li>)}
                    </ul>
                  </div>
                  <div>
                    <h5 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-[#8A837B]">Data provenance ({run.summary.provenance.length} records)</h5>
                    <div className="mt-1 max-h-72 space-y-2 overflow-y-auto pr-1">
                      {run.summary.provenance.map((p, i) => (
                        <div key={`${p.productId}-${i}`} className="rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-2.5 text-[10.5px] leading-4">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className={`rounded px-1 py-px font-mono text-[9px] uppercase ${p.dataClass === 'measured' ? 'bg-[#EFF5F3] text-[#2C5F58]' : p.dataClass === 'modelled' ? 'bg-[#F7F2E7] text-[#7A6438]' : 'bg-[#F7F4EE] text-[#8A837B]'}`}>{p.dataClass}</span>
                            <span className="font-semibold text-[#1C1A17]">{p.productId}</span>
                            <span className="text-[#8A837B]">· {p.provider}</span>
                          </div>
                          <div className="mt-0.5 text-[#8A837B]">
                            {p.units} · retrieved {p.retrievedAt.slice(0, 16)}Z · <a className="underline decoration-dotted" href={p.sourceUrl} target="_blank" rel="noopener noreferrer">source</a>
                          </div>
                          {p.processing.length > 0 && <div className="mt-0.5 text-[#A39B90]">processing: {p.processing.join(' → ')}</div>}
                          {p.qualityNotes.length > 0 && <div className="mt-0.5 text-[#A39B90]">quality: {p.qualityNotes.join(' · ')}</div>}
                        </div>
                      ))}
                    </div>
                  </div>
                  <p className="text-[11px] leading-4 text-[#9E3B2C]">
                    Limitations: {run.summary.grid.resArcmin}′ ETOPO1 cells (~{(run.summary.grid.resArcmin * 1.85).toFixed(0)} km) cannot resolve
                    coastal inundation — any land flooding shown is indicative only, and site-specific flow depths or safety must never be
                    inferred from this map. Coriolis and frequency dispersion are not modelled (regional-domain approximation).
                    Uncertainty in the earthquake source propagates directly into the wave field; no error bars are shown because
                    no ensemble was computed.
                  </p>
                </CollapsibleContent>
              </Collapsible>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-2.5">
      <div className="text-[10px] uppercase tracking-[0.06em] text-[#8A837B]">{label}</div>
      <div className="mt-0.5 font-mono text-[12.5px] text-[#1C1A17]">{value}</div>
    </div>
  );
}

function fmtRange(overlay: { range: [number, number]; unit: string } | null, i: 0 | 1): string {
  if (!overlay) return '—';
  const v = overlay.range[i];
  if (overlay.unit === 'min') return `${(v / 60).toFixed(0)} min`;
  return v.toFixed(v < 1 ? 2 : 1);
}

function layerGradient(layer: TsunamiLayerKind): string {
  if (layer === 'dz') {
    return 'linear-gradient(to right, rgb(140,99,106), rgb(234,199,172), rgb(228,193,180))';
  }
  if (layer === 'bed') {
    return 'linear-gradient(to right, rgb(56,86,94), rgb(214,226,222), rgb(212,204,188))';
  }
  if (layer === 'maxEta') {
    return 'linear-gradient(to right, rgb(247,242,233), rgb(148,175,153), rgb(26,77,68))';
  }
  return 'linear-gradient(to right, rgb(249,245,235), rgb(176,164,106), rgb(110,78,45))';
}

/** Gauge chart with BOTH model η and observed residual (when present). */
function GaugeChartObs({ gauge, det, originMs }: { gauge: RunSummary['gauges'][number]; det?: DetectionPayload; originMs: number }) {
  // Merge model series and observation series on a common minute axis.
  const points = new Map<number, { m: number; sim?: number; obs?: number }>();
  for (let i = 0; i < gauge.tMin.length; i++) {
    const m = Math.round(gauge.tMin[i] * 10) / 10;
    points.set(m, { m, sim: gauge.eta[i] * 100 });
  }
  if (det?.t && det.resid) {
    for (let i = 0; i < det.t.length; i++) {
      const m = Math.round(((det.t[i] - originMs) / 60000) * 10) / 10;
      const p = points.get(m) ?? { m };
      p.obs = det.resid[i] * 100;
      points.set(m, p);
    }
  }
  const data = [...points.values()].sort((a, b) => a.m - b.m);
  return (
    <div className="h-[170px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 6, right: 6, bottom: 2, left: -18 }}>
          <CartesianGrid stroke="#EFEAE2" strokeDasharray="3 3" />
          <XAxis dataKey="m" type="number" domain={['dataMin', 'dataMax']} tick={{ fontSize: 9, fill: '#8A837B' }} tickFormatter={(v) => `${Math.round(v)}`} height={20} />
          <YAxis tick={{ fontSize: 9, fill: '#8A837B' }} width={38} label={{ value: 'cm', angle: -90, position: 'insideLeft', fontSize: 9, fill: '#8A837B' }} />
          <RTooltip
            formatter={(v: number | string, name) => [`${Number(v).toFixed(1)} cm`, name === 'sim' ? 'modelled η' : 'observed residual']}
            labelFormatter={(l) => `${Number(l) >= 0 ? '+' : ''}${Number(l).toFixed(0)} min`}
            contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid #E7E2DA' }}
          />
          <ReferenceLine x={0} stroke="#4A443C" strokeWidth={1} />
          <Line type="monotone" dataKey="sim" stroke="#B08D57" strokeWidth={1.6} dot={false} isAnimationActive={false} />
          {det?.triggerMs !== null && det?.triggerMs !== undefined && det.arrivalMin !== null && (
            <ReferenceLine x={det.arrivalMin} stroke="#9E3B2C" strokeDasharray="4 3" label={{ value: 'observed arrival', fontSize: 9, fill: '#9E3B2C', position: 'top' }} />
          )}
          {det?.t && (
            <Line type="monotone" dataKey="obs" stroke="#2C5F58" strokeWidth={1.3} dot={false} isAnimationActive={false} />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
