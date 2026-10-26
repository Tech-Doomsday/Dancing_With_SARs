'use client';

/**
 * Engine Overview — the entry view of the Dancing with SARs web app.
 *
 * It presents the nisar-hazard-engine (the Python half of this repository)
 * exactly as it is, read live from its own source of truth: the pipeline
 * manifests under engine/pipelines/<name>/manifest.yaml (via /api/engine/pipelines)
 * and filesystem facts (via /api/engine/status). Nothing on this screen is
 * hardcoded — if a manifest changes, this view changes with it — and nothing
 * claims a capability the engine does not have: a pipeline without a run()
 * entry point is shown as NOT_MODELLED, which is the engine registry's own
 * semantics for "scaffolded, not yet built".
 */

import { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Database,
  FileSearch,
  Layers,
  Loader2,
  Radar,
  Route,
  Warehouse,
  Waves,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { MicroLabel } from '@/components/app/ui-bits';
import type {
  EnginePipeline,
  EnginePipelinesResponse,
  EngineStatus,
  EngineStatusResponse,
} from '@/lib/engine/types';
import type { HealthPayload } from '@/components/app/types';

export interface EngineOverviewProps {
  /** App-level health payload (shared with the earthquake section). */
  health: HealthPayload | null;
  /** Open the earthquake analysis section (main module). */
  onOpenEarthquake: () => void;
  /** Open the earthquake analysis section scrolled to the tsunami panel. */
  onOpenTsunami: () => void;
}

/** Honest per-stage implementation badge. */
function StageBadge({ implemented }: { implemented: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-[9.5px] font-semibold uppercase tracking-[0.06em] ${
        implemented ? 'bg-[#2C5F58]/10 text-[#2C5F58]' : 'bg-[#B08D57]/15 text-[#8F7440]'
      }`}
    >
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full ${implemented ? 'bg-[#2C5F58]' : 'bg-[#B08D57]'}`}
        aria-hidden
      />
      {implemented ? 'built' : 'planned'}
    </span>
  );
}

/** Pipeline implementation state, using the engine registry's own vocabulary. */
function PipelineBadge({ implemented }: { implemented: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-[9.5px] font-semibold uppercase tracking-[0.06em] ${
        implemented ? 'bg-[#2C5F58]/10 text-[#2C5F58]' : 'bg-[#EFEAE2] text-[#8A837B]'
      }`}
      title={
        implemented
          ? 'verdict.py defines run() — this pipeline executes in the engine registry.'
          : "The engine registry's status for a pipeline without run(): NOT_MODELLED. Its manifest documents the agreed method; the implementation is still to be written."
      }
    >
      {implemented ? 'implemented' : 'NOT_MODELLED'}
    </span>
  );
}

/** Human name for a pipeline folder, e.g. earthquake_coseismic → Earthquake (coseismic). */
function prettyName(p: EnginePipeline): string {
  const map: Record<string, string> = {
    earthquake_coseismic: 'Earthquake · coseismic',
    land_subsidence: 'Land subsidence',
    volcanic_deformation: 'Volcanic deformation',
    landslide: 'Landslide',
    glacier_ice_flow: 'Glacier & ice flow',
    permafrost_thaw: 'Permafrost thaw',
    flood_inundation: 'Flood inundation',
    deforestation: 'Deforestation',
    wildfire_burn_scar: 'Wildfire burn scar',
    river_erosion: 'River erosion',
    coastal_erosion: 'Coastal erosion',
    urban_damage: 'Urban damage',
    tropical_cyclone: 'Tropical cyclone',
    tsunami_inundation: 'Tsunami inundation',
    oil_spill: 'Oil spill',
    iceberg_calving_drift: 'Iceberg calving & drift',
  };
  return map[p.name] ?? p.name.replace(/_/g, ' ');
}

export function EngineOverview({ health, onOpenEarthquake, onOpenTsunami }: EngineOverviewProps) {
  const [catalog, setCatalog] = useState<EnginePipelinesResponse | null>(null);
  const [status, setStatus] = useState<EngineStatusResponse | null>(null);

  useEffect(() => {
    fetch('/api/engine/pipelines')
      .then((r) => r.json())
      .then((d: EnginePipelinesResponse) => setCatalog(d))
      .catch(() => setCatalog(null));
    fetch('/api/engine/status')
      .then((r) => r.json())
      .then((d: EngineStatusResponse) => setStatus(d))
      .catch(() => setStatus(null));
  }, []);

  const pipelines = catalog?.ok ? catalog.pipelines : null;
  const engineStatus: EngineStatus | null = status?.ok ? (status.status ?? null) : null;
  const liveCatalogs = !!health && (health.capabilities.liveUsgs || health.capabilities.liveCmr);

  const earthquakePipeline = useMemo(
    () => pipelines?.find((p) => p.name === 'earthquake_coseismic') ?? null,
    [pipelines],
  );
  const tsunamiPipeline = useMemo(
    () => pipelines?.find((p) => p.name === 'tsunami_inundation') ?? null,
    [pipelines],
  );

  return (
    <main className="mx-auto w-full max-w-[1240px] flex-1 px-4 py-8 sm:px-6 sm:py-10">
      {/* ---------- Intro ---------- */}
      <section aria-label="Engine introduction" className="mb-10 max-w-3xl">
        <motion.h1
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: 'easeOut' }}
          className="text-[26px] font-semibold leading-8 tracking-[-0.01em] text-[#1C1A17] sm:text-[30px] sm:leading-9"
        >
          A global SAR hazard engine, one hazard at a time.
        </motion.h1>
        <motion.p
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.06, ease: 'easeOut' }}
          className="mt-3 max-w-2xl text-[14px] leading-6 text-[#4A443C]"
        >
          <span className="font-mono text-[12.5px]">nisar-hazard-engine</span> watches the planet with
          NISAR radar (Sentinel-1 as fallback): retrieve granules, preprocess them into one shared
          scene bundle, run sixteen independent hazard pipelines, and turn their outputs into
          verdicts. This page is generated from the engine&rsquo;s own pipeline manifests — the same
          files its Python registry reads — so it always tells the truth about what is built and
          what is still scaffolded.
        </motion.p>
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.12, ease: 'easeOut' }}
          className="mt-5 flex flex-wrap items-center gap-3"
        >
          <Button
            onClick={onOpenEarthquake}
            className="h-11 gap-2 rounded-lg bg-[#1C1A17] px-5 text-[13.5px] font-medium text-white hover:bg-[#33302B]"
          >
            <Activity className="h-4 w-4" aria-hidden />
            Open the earthquake analysis module
          </Button>
          <span className="text-[12px] text-[#8A837B]">
            the engine&rsquo;s earthquake &amp; tsunami pipelines, made interactive
          </span>
        </motion.div>

        {/* status strip */}
        <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-[#EFEAE2] bg-white px-4 py-3" aria-label="Engine status">
          <span className="flex items-baseline gap-1.5 text-[12px] text-[#4A443C]">
            <span className="font-mono text-[15px] font-semibold tabular-nums text-[#1C1A17]">
              {catalog === null ? '—' : catalog.ok ? catalog.pipelineCount : 0}
            </span>
            hazard pipelines
          </span>
          <span className="flex items-baseline gap-1.5 text-[12px] text-[#4A443C]">
            <span className="font-mono text-[15px] font-semibold tabular-nums text-[#1C1A17]">
              {catalog === null ? '—' : catalog.ok ? catalog.implementedCount : 0}
            </span>
            implemented
          </span>
          <span className="flex items-center gap-1.5 text-[12px] text-[#4A443C]">
            <span
              className={`motus-live-dot inline-block h-2 w-2 rounded-full ${
                engineStatus ? 'bg-[#2C5F58]' : 'bg-[#B08D57]'
              }`}
              aria-hidden
            />
            {status === null
              ? 'checking engine source…'
              : engineStatus
                ? 'engine source found'
                : 'engine source not found'}
          </span>
          <span className="flex items-center gap-1.5 text-[12px] text-[#4A443C]">
            <span
              className={`motus-live-dot inline-block h-2 w-2 rounded-full ${liveCatalogs ? 'bg-[#2C5F58]' : 'bg-[#B08D57]'}`}
              aria-hidden
            />
            {health === null ? 'checking NASA catalogs…' : liveCatalogs ? 'Live NASA catalogs connected' : 'NASA catalogs: cached records'}
          </span>
        </div>
      </section>

      {/* ---------- Honest unavailable state ---------- */}
      {catalog && !catalog.ok && (
        <div className="mb-10 rounded-xl border border-[#E5C4B7] bg-[#FBF3EF] p-5 text-[13px] leading-5 text-[#9E3B2C]" role="alert">
          <p className="flex items-center gap-1.5 font-semibold">
            <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
            The engine catalog cannot be shown right now
          </p>
          <p className="mt-1.5">{catalog.message}</p>
          <p className="mt-1.5 text-[12px] text-[#9E3B2C]/80">{catalog.hint}</p>
          <p className="mt-2 text-[12px] text-[#9E3B2C]/80">
            The earthquake analysis module below still works — it only loses this overview of the
            engine&rsquo;s sixteen pipelines.
          </p>
        </div>
      )}

      {/* ---------- Workflow stages ---------- */}
      <section aria-label="Engine workflow" className="mb-10">
        <h2 className="text-[16px] font-semibold tracking-[-0.005em] text-[#1C1A17]">The workflow</h2>
        <p className="mt-1 max-w-2xl text-[12px] leading-[1.45rem] text-[#8A837B]">
          Five stages, each stated as it truly is. &ldquo;Planned&rdquo; stages exist as design and
          scaffolding in the repository — the pipeline grid below names the exact status of every
          hazard pipeline.
        </p>
        {catalog === null ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="h-[120px] animate-pulse rounded-xl border border-[#EFEAE2] bg-white" />
            ))}
          </div>
        ) : catalog.ok ? (
          <ol className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {catalog.stages.map((s) => (
              <li key={s.n} className="rounded-xl border border-[#EFEAE2] bg-white p-4">
                <div className="flex items-center justify-between gap-2">
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[#1C1A17] font-mono text-[10px] font-semibold text-white" aria-hidden>
                    {s.n}
                  </span>
                  <StageBadge implemented={s.implemented} />
                </div>
                <span className="mt-2 block text-[13px] font-semibold text-[#1C1A17]">{s.title}</span>
                <span className="mt-1 block text-[11.5px] leading-4 text-[#8A837B]">{s.blurb}</span>
              </li>
            ))}
          </ol>
        ) : null}
      </section>

      {/* ---------- Engine runtime facts ---------- */}
      {engineStatus && (
        <section aria-label="Engine runtime" className="mb-10 grid gap-3 sm:grid-cols-3">
          <div className="rounded-xl border border-[#EFEAE2] bg-white p-4">
            <MicroLabel>
              <span className="flex items-center gap-1.5">
                <FileSearch className="h-3.5 w-3.5 text-[#B08D57]" aria-hidden /> Engine source
              </span>
            </MicroLabel>
            <p className="mt-1 break-all font-mono text-[11px] leading-4 text-[#4A443C]" title={engineStatus.engineRoot}>
              {engineStatus.engineRoot}
            </p>
            <p className="mt-1 text-[11px] text-[#8A837B]">manifests read live at request time</p>
          </div>
          <div className="rounded-xl border border-[#EFEAE2] bg-white p-4">
            <MicroLabel>
              <span className="flex items-center gap-1.5">
                <Warehouse className="h-3.5 w-3.5 text-[#B08D57]" aria-hidden /> Data directory
              </span>
            </MicroLabel>
            <p className="mt-1 break-all font-mono text-[11px] leading-4 text-[#4A443C]" title={engineStatus.dataDir}>
              {engineStatus.dataDir}
            </p>
            <p className="mt-1 text-[11px] text-[#8A837B]">
              {engineStatus.dataDirExists ? 'present (engine has run here)' : 'not created yet — appears after the first poller run'}
            </p>
          </div>
          <div className="rounded-xl border border-[#EFEAE2] bg-white p-4">
            <MicroLabel>
              <span className="flex items-center gap-1.5">
                <Database className="h-3.5 w-3.5 text-[#B08D57]" aria-hidden /> Retrieval state
              </span>
            </MicroLabel>
            <p className="mt-1 font-mono text-[11px] leading-4 text-[#4A443C]">
              {engineStatus.retrievalDbExists ? 'retrieval.sqlite present' : 'no retrieval state yet'}
            </p>
            <p className="mt-1 text-[11px] text-[#8A837B]">
              {engineStatus.bundlesDirExists ? 'scene bundles have been persisted' : 'no scene bundles persisted yet'}
            </p>
          </div>
        </section>
      )}

      {/* ---------- The 16 pipelines ---------- */}
      <section aria-label="Hazard pipelines">
        <h2 className="text-[16px] font-semibold tracking-[-0.005em] text-[#1C1A17]">The sixteen hazard pipelines</h2>
        <p className="mt-1 max-w-2xl text-[12px] leading-[1.45rem] text-[#8A837B]">
          Independent by rule — no pipeline ever reads another pipeline&rsquo;s verdict; shared
          science lives in <span className="font-mono">engine/lib/</span>. Open any card for its
          full manifest: routing rule, inputs, fetch plan, method and output contract.
          &ldquo;NOT_MODELLED&rdquo; is the engine&rsquo;s own honest status for a scaffolded
          pipeline — a valid answer, not an error.
        </p>

        {catalog === null ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="h-[130px] animate-pulse rounded-xl border border-[#EFEAE2] bg-white" />
            ))}
          </div>
        ) : pipelines ? (
          <>
            {/* flagship cards: the two pipelines this app makes interactive */}
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              {earthquakePipeline && (
                <div className="rounded-xl border border-[1.5px] border-[#1C1A17] bg-white p-5 shadow-[0_1px_8px_rgba(28,26,23,0.08)]">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2">
                      <Route className="h-4 w-4 text-[#B08D57]" aria-hidden />
                      <span className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.06em] text-[#8A837B]">
                        {earthquakePipeline.code} · live in this app
                      </span>
                    </span>
                    <PipelineBadge implemented={earthquakePipeline.implemented} />
                  </div>
                  <h3 className="mt-2 text-[15px] font-semibold text-[#1C1A17]">
                    {prettyName(earthquakePipeline)}
                  </h3>
                  <p className="mt-1 text-[12px] leading-[1.45rem] text-[#6F6862]">
                    The interactive <strong>Earthquake analysis</strong> module implements this
                    pipeline&rsquo;s science end-to-end — Okada physics, honest uncertainty,
                    evidence linking — on real NISAR products today.
                  </p>
                  <Button
                    onClick={onOpenEarthquake}
                    className="mt-3 h-9 gap-2 rounded-lg bg-[#1C1A17] px-4 text-[12.5px] font-medium text-white hover:bg-[#33302B]"
                  >
                    <Activity className="h-3.5 w-3.5" aria-hidden />
                    Analyze an earthquake
                    <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                </div>
              )}
              {tsunamiPipeline && (
                <div className="rounded-xl border border-[1.5px] border-[#1C1A17] bg-white p-5 shadow-[0_1px_8px_rgba(28,26,23,0.08)]">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2">
                      <Waves className="h-4 w-4 text-[#2C5F58]" aria-hidden />
                      <span className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.06em] text-[#8A837B]">
                        {tsunamiPipeline.code} · live in this app
                      </span>
                    </span>
                    <PipelineBadge implemented={tsunamiPipeline.implemented} />
                  </div>
                  <h3 className="mt-2 text-[15px] font-semibold text-[#1C1A17]">
                    {prettyName(tsunamiPipeline)}
                  </h3>
                  <p className="mt-1 text-[12px] leading-[1.45rem] text-[#6F6862]">
                    The <strong>tsunami check</strong> inside the earthquake module screens real
                    DART buoy records against an NSWE simulation of the same events.
                  </p>
                  <Button
                    onClick={onOpenTsunami}
                    className="mt-3 h-9 gap-2 rounded-lg border border-[#D8D2C6] bg-white px-4 text-[12.5px] font-medium text-[#1C1A17] hover:bg-[#F7F4EE]"
                  >
                    <Waves className="h-3.5 w-3.5 text-[#2C5F58]" aria-hidden />
                    Check the tsunami evidence
                    <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                </div>
              )}
            </div>

            {/* the full grid */}
            <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {pipelines
                .filter((p) => p.name !== 'earthquake_coseismic' && p.name !== 'tsunami_inundation')
                .map((p) => (
                  <PipelineCard key={p.name} pipeline={p} />
                ))}
            </div>
          </>
        ) : null}
      </section>

      {/* ---------- Honest scope note ---------- */}
      <section aria-label="About the engine status" className="mt-10 rounded-xl border border-[#E7E2DA] bg-white p-5">
        <h2 className="text-[14px] font-semibold text-[#1C1A17]">What this overview can and cannot tell you</h2>
        <ul className="mt-2.5 grid gap-2 text-[12.5px] leading-[1.45rem] text-[#6F6862] sm:grid-cols-2">
          <li className="flex gap-2">
            <Radar className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#2C5F58]" aria-hidden />
            <span>Pipeline cards are read from the real <span className="font-mono">manifest.yaml</span> files at request time — edit a manifest and reload.</span>
          </li>
          <li className="flex gap-2">
            <Layers className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#2C5F58]" aria-hidden />
            <span>&ldquo;Implemented&rdquo; uses the registry&rsquo;s own test: <span className="font-mono">verdict.py</span> must define <span className="font-mono">run()</span>.</span>
          </li>
          <li className="flex gap-2">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#B08D57]" aria-hidden />
            <span>No scan results, verdicts or tiles are shown, because the engine has not produced any yet — this app will say so until it does.</span>
          </li>
          <li className="flex gap-2">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#B08D57]" aria-hidden />
            <span>&ldquo;Cold start&rdquo; is a designed state: the calibrated NISAR archive only begins in mid-2026, so empty results are honest answers.</span>
          </li>
        </ul>
      </section>
    </main>
  );
}

/** One pipeline card; opens the manifest drawer. */
function PipelineCard({ pipeline: p }: { pipeline: EnginePipeline }) {
  return (
    <Sheet>
      <SheetTrigger asChild>
        <button
          type="button"
          aria-label={`Open the manifest of pipeline ${p.code} ${prettyName(p)}`}
          className="h-fit rounded-xl border border-[#E7E2DA] bg-white p-4 text-left transition-all duration-150 hover:border-[#B8AFA2]"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.06em] text-[#8A837B]">
              {p.code}
            </span>
            <PipelineBadge implemented={p.implemented} />
          </div>
          <div className="mt-2 text-[13px] font-medium leading-[1.25em] text-[#1C1A17]">{prettyName(p)}</div>
          <p className="mt-1 line-clamp-2 text-[11.5px] leading-4 text-[#8A837B]">{p.modelType}</p>
          <div className="mt-2 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.04em] text-[#8A837B]">
            <Radar className="h-3 w-3 shrink-0" aria-hidden />
            {p.sensor}
            {p.minEpochs ? ` · needs ≥${p.minEpochs} epochs` : ''}
          </div>
        </button>
      </SheetTrigger>
      <SheetContent side="right" className="motus-scroll w-full overflow-y-auto sm:max-w-md">
        <SheetHeader className="text-left">
          <SheetTitle className="text-[15px] text-[#1C1A17]">
            {p.code} · {prettyName(p)}
          </SheetTitle>
          <p className="font-mono text-[11px] text-[#8A837B]">engine/pipelines/{p.name}/manifest.yaml</p>
        </SheetHeader>
        <div className="mt-2 space-y-3.5 px-4 pb-8">
          <div className="flex flex-wrap items-center gap-2">
            <PipelineBadge implemented={p.implemented} />
            <span className="rounded-full border border-[#E7E2DA] px-2 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.06em] text-[#6F6862]">
              {p.sensor}
            </span>
            {p.minEpochs ? (
              <span className="rounded-full border border-[#E7E2DA] px-2 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.06em] text-[#6F6862]">
                min {p.minEpochs} epochs
              </span>
            ) : null}
          </div>
          <ManifestRow label="Model" value={p.modelType} />
          <ManifestRow label="Applies where" value={p.applies} />
          <ManifestRow label="Uses (shared)" value={`${p.uses}${p.usesCodes.length ? ` [${p.usesCodes.join(', ')}]` : ''}`} />
          <ManifestRow label="Own preprocessing" value={p.ownPrep} />
          <ManifestRow label="Own fetches" value={`${p.ownFetch}${p.fetchCodes.length ? ` [${p.fetchCodes.join(', ')}]` : ''}`} />
          <div>
            <MicroLabel>Method</MicroLabel>
            <ol className="mt-1 list-inside list-decimal space-y-1.5 text-[12px] leading-[1.45rem] text-[#4A443C]">
              {p.method.map((m, i) => (
                <li key={i}>{m}</li>
              ))}
            </ol>
          </div>
          <ManifestRow label="Output" value={p.output} />
          <div>
            <MicroLabel>Status vocabulary</MicroLabel>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {p.statusValues.map((s) => (
                <span key={s} className="rounded-md border border-[#EFEAE2] bg-[#FDFCF9] px-1.5 py-0.5 font-mono text-[10px] text-[#6F6862]">
                  {s}
                </span>
              ))}
              <span className="rounded-md border border-[#EFEAE2] bg-[#FDFCF9] px-1.5 py-0.5 font-mono text-[10px] text-[#6F6862]">
                NOT_APPLICABLE
              </span>
              <span className="rounded-md border border-[#EFEAE2] bg-[#FDFCF9] px-1.5 py-0.5 font-mono text-[10px] text-[#6F6862]">
                NOT_RUN
              </span>
            </div>
            <p className="mt-1.5 text-[10.5px] leading-4 text-[#8A837B]">
              The registry adds NOT_APPLICABLE, NOT_RUN and ERROR around the manifest&rsquo;s own
              vocabulary.
            </p>
          </div>
          <p className="rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3 text-[11px] leading-4 text-[#8A837B]">
            This drawer is generated straight from the manifest, exactly as the engine&rsquo;s
            architecture prescribes for its serving layer.
          </p>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function ManifestRow({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div>
      <MicroLabel>{label}</MicroLabel>
      <p className="mt-0.5 text-[12px] leading-[1.45rem] text-[#4A443C]">{value}</p>
    </div>
  );
}

/** Loading fallback used while the catalog request is in flight. */
export function EngineOverviewSkeleton() {
  return (
    <div className="flex min-h-[420px] items-center justify-center rounded-xl border border-[#E7E2DA] bg-white">
      <Loader2 className="h-5 w-5 animate-spin text-[#B8AFA2]" aria-label="Loading engine overview" />
    </div>
  );
}
