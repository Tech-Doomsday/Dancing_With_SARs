'use client';

/**
 * Results panel — plain-language first, technical evidence one click away.
 *
 * ORDER (product story):
 *   1. The verdict — one sentence, five seconds (VerdictBanner).
 *   2. What did we find?        (key numbers + narrative)
 *   3. The charts               (histogram + profile, from the real grids)
 *   4. How certain are we?      (uncertainty in plain words)
 *   5. What could explain it?   (seismic timeline + hypotheses + model check)
 *   6. What are the limitations?(honest scope + "why can't I see anything?")
 *   7. Advanced results         (collapsible technical evidence)
 *
 * Scientific language rules preserved: detection is never proof of motion;
 * the null screen stays "exploratory"; model agreement is "consistent",
 * never "caused by"; uncorrected atmospheric contributions keep the
 * "apparent" qualifier; the recorded Mojave result is labelled as recorded.
 */

import { useState } from 'react';
import { motion } from 'framer-motion';
import { ChevronDown, ChevronRight, AlertTriangle, CheckCircle2, HelpCircle, Ruler, ShieldQuestion, Lightbulb, Microscope, ArchiveRestore, Activity, Waves } from 'lucide-react';
import type { AnalysisResult } from '@/lib/science/types';
import { HypothesisStatusBadge, MicroLabel, Stat, WhatIsThis, EventRow, fmtMm, fmtKm2 } from './ui-bits';
import { EvidenceViewer } from './evidence-viewer';
import type { EvidenceAnalysisResult, PanamaGranule, RecordedRunResult } from './types';
import type { DecodedGrids } from './decode';
import { VerdictBanner, HistogramCard, ProfileCard, SeismicTimeline, WhyCantISeeAnything, SignalVsNoiseCard } from './charts';
import { computeRecordedVerdict, areaLabel, outcomeOf } from '@/lib/science/verdict';

export interface ResultGrids {
  grids: DecodedGrids | null;
  gridsMeta?: {
    xStart: number; xSpacing: number; yStart: number; ySpacing: number;
    width: number; height: number; epsg: number;
  };
  isPrediction?: boolean;
}

/* ================================================================== */
/* Quantitative results                                                */
/* ================================================================== */

export function QuantitativeResults({
  result,
  rg,
  acquisitionTimes,
}: {
  result: AnalysisResult & { runId?: string };
  rg: ResultGrids;
  acquisitionTimes?: { referenceTime: string; secondaryTime: string };
}) {
  const s = result.summary;
  const v = result.validation;
  const fm = result.attribution.forwardModelCheck;
  const outcome = outcomeOf(s);
  const isDemo = result.datasetId.startsWith('synthetic-');

  return (
    <div className="space-y-6">
      {/* ---------- 1 · The verdict ---------- */}
      <VerdictBanner result={result} />
      {isDemo && (
        <p className="-mt-3 rounded-lg border border-[#B0714E]/40 bg-[#F7EBE2] px-3 py-2 text-[11.5px] leading-4 text-[#8F4A2B]">
          Demonstration data: the &ldquo;right answer&rdquo; is known by construction, so you can see
          how the analysis behaves on a controlled example. It is not an observation of the Earth.
        </p>
      )}

      {/* ---------- 2 · What did we find? ---------- */}
      <section aria-labelledby="what-found">
        <h2 id="what-found" className="text-[13.5px] font-semibold text-[#1C1A17]">What did we find?</h2>
        <p className="mt-2 text-[13px] leading-[1.5rem] text-[#4A443C]">{result.narrative.whatChanged}</p>
        {(outcome === 'detected' || outcome === 'inconclusive') && (
          <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3.5">
            {s.maxAwayM > 0.0005 && (
              <Stat label="Largest movement away from satellite" value={fmtMm(s.maxAwayM, 0)} title="Peak apparent LOS range change away from the satellite (positive convention)." />
            )}
            {s.maxTowardM < -0.0005 && (
              <Stat label="Largest movement toward satellite" value={fmtMm(Math.abs(s.maxTowardM), 0)} title="Peak apparent LOS range change toward the satellite (negative convention)." />
            )}
            <Stat label="Area above threshold" value={fmtKm2(s.affectedAreaKm2)} title="Total area whose apparent change exceeded the detection threshold." />
            <Stat label="Measured area" value={fmtKm2(s.analysedAreaKm2)} title="Area with reliable enough signal to be measured at all." />
          </div>
        )}
        <p className="mt-2 text-[11px] leading-4 text-[#8A837B]">
          &ldquo;Apparent&rdquo; movement means movement in the radar measurement: it is what the
          satellite saw change, which can include effects of the atmosphere between the two images —{' '}
          <WhatIsThis>the radar signal travels through the atmosphere twice; water vapour and charged
          particles (the ionosphere) can shift it by amounts that look like ground movement. The
          NISAR data used here does not have those effects fully removed, so the analysis quantifies
          them as an uncertainty instead of pretending they are gone.</WhatIsThis>
        </p>
      </section>

      {/* ---------- 3 · The charts ---------- */}
      {rg.grids && (
        <section aria-label="Charts of the measurements" className="space-y-4 rounded-xl border border-[#EFEAE2] bg-white p-3.5 sm:p-4">
          <HistogramCard grids={rg.grids} summary={s} isPrediction={rg.isPrediction} />
          <div className="border-t border-[#EFEAE2] pt-3.5">
            <ProfileCard grids={rg.grids} gridsMeta={rg.gridsMeta} summary={s} isPrediction={rg.isPrediction} />
          </div>
        </section>
      )}

      {/* ---------- 4 · How certain are we? ---------- */}
      <section aria-labelledby="how-certain">
        <SectionTitle icon={<Ruler className="h-3.5 w-3.5" aria-hidden />} id="how-certain">
          How certain are we?
        </SectionTitle>
        <p className="mt-2 text-[13px] leading-[1.5rem] text-[#4A443C]">{result.narrative.reliability}</p>
        <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3.5">
          <Stat label="Typical measurement noise" value={`± ${fmtMm(s.medianSigmaM)}`} title="Median per-pixel 1σ uncertainty of the apparent LOS range change." />
          <Stat label="Reliable-signal coverage" value={`${(s.validFraction * 100).toFixed(0)}% of the area`} title="Fraction of the frame that passed the signal-quality (coherence) mask." />
          {s.systematicIonoFloorM > 0 && (
            <Stat
              label="Atmosphere-related floor"
              value={`± ${fmtMm(s.systematicIonoFloorM)}`}
              title={`Uncertainty from the unapplied ionospheric phase screen (basis: ${s.ionoFloorBasis}). Signals smaller than this cannot be confidently told apart from ionosphere effects.`}
            />
          )}
          <Stat label="Change needed to be flagged" value={fmtMm(s.thresholdMedianM)} title="Median detection threshold actually applied (combines the statistical rule and the physical floor)." />
        </div>
        <p className="mt-2 text-[11px] leading-4 text-[#8A837B]">
          The measurement noise describes random, pixel-to-pixel scatter. Uncertainty from the{' '}
          <WhatIsThis label="ionosphere">a layer of charged particles high in the atmosphere that can
          distort the radar signal. The product ships an estimate of this distortion but does not
          remove it from the data, so it is counted as a systematic uncertainty here.</WhatIsThis>{' '}
          affects whole regions at once and does not average out the way random noise does.
        </p>
      </section>

      {/* ---------- 5 · What could explain it? ---------- */}
      <section aria-labelledby="what-explains">
        <SectionTitle icon={<Lightbulb className="h-3.5 w-3.5" aria-hidden />} id="what-explains">
          What could explain it?
        </SectionTitle>
        <p className="mt-1.5 text-[12px] leading-[1.45rem] text-[#8A837B]">
          Candidate explanations, each with the evidence for and against. None of these is a
          diagnosis — a single radar view cannot prove what caused a change.
        </p>

        {/* Seismic context: the acquisition window with real events */}
        {acquisitionTimes && (
          <div id="seismic-context" className="mt-3 scroll-mt-24">
            <SeismicTimeline
              referenceTime={acquisitionTimes.referenceTime}
              secondaryTime={acquisitionTimes.secondaryTime}
              events={result.attribution.events}
              primaryEventId={result.attribution.events[0]?.id}
            />
          </div>
        )}

        <ul className="mt-3 space-y-2">
          {result.attribution.hypotheses.map((h) => (
            <HypothesisRow key={h.id} h={h} />
          ))}
        </ul>
        {fm && (
          <div className="mt-3 rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3">
            <MicroLabel>Earthquake model check (Okada 1985 · USGS fault parameters)</MicroLabel>
            {fm.skipped ? (
              <p className="mt-1.5 text-[12px] leading-[1.4rem] text-[#6F6862]">
                Not run: {fm.skipReason}. No fault geometry was invented to fill the gap.
              </p>
            ) : (
              <>
                <p className="mt-1.5 text-[12.5px] leading-[1.4rem] text-[#4A443C]">
                  A standard earthquake model, built from the published fault information for the
                  matched event, predicts a peak movement of {fmtMm(fm.predictedPeakM, 0)}; the
                  measurement peaks at {fmtMm(fm.measuredPeakM, 0)} (ratio{' '}
                  {fm.amplitudeRatio.toFixed(2)}), and the two spatial patterns correlate at r ={' '}
                  {fm.patternCorrelation.toFixed(2)}.
                </p>
                <p className="mt-1.5 text-[11px] leading-4 text-[#8A837B]">
                  Even when both checks pass, this only means the pattern is{' '}
                  <em>consistent with</em> the model — it does not prove the earthquake caused it.
                </p>
              </>
            )}
            <p className="mt-1.5 text-[11px] leading-4 text-[#8A837B]">{fm.note}</p>
          </div>
        )}
        <p className="mt-3 text-[11px] leading-4 text-[#8A837B]">{result.attribution.methodNote}</p>
      </section>

      {/* ---------- 6 · Limitations + why-empty ---------- */}
      <section aria-labelledby="limits">
        <SectionTitle icon={<ShieldQuestion className="h-3.5 w-3.5" aria-hidden />} id="limits">
          What are the limitations?
        </SectionTitle>
        {(outcome === 'none' || outcome === 'inconclusive') && (
          <div className="mt-3">
            <WhyCantISeeAnything summary={s} />
          </div>
        )}
        <ul className="mt-3 space-y-1.5 text-[12.5px] leading-[1.45rem] text-[#4A443C]">
          <Limitation>
            This is a single comparison of two radar images. Slow, ongoing processes (months to
            years) cannot be separated from one-off events.
          </Limitation>
          <Limitation>
            Movement is measured only toward or away from the satellite — one direction, not the
            full three-dimensional motion.
          </Limitation>
          <Limitation>
            Atmospheric and ionospheric effects are not fully removed from the data, so part of any
            apparent change may not be ground movement at all.
          </Limitation>
          {s.connectedComponentCount > 1 ? (
            <Limitation>
              The unwrapping step split the scene into {s.connectedComponentCount} regions; treat
              abrupt boundaries between regions with caution.{' '}
              <WhatIsThis label="What is unwrapping?">the radar signal repeats in cycles; the
              computer must add the cycles up (&ldquo;unwrap&rdquo; them) to estimate a continuous
              change. Where the signal is weak, it can lose count and jump by a whole cycle, which
              looks like a sudden step in movement.</WhatIsThis>
            </Limitation>
          ) : (
            <Limitation>
              The unwrapping step was consistent across the scene (one region), which lowers — but
              does not eliminate — the chance of step-like artefacts.{' '}
              <WhatIsThis label="What is unwrapping?">the radar signal repeats in cycles; the
              computer must add the cycles up (&ldquo;unwrap&rdquo; them) to estimate a continuous
              change. Where the signal is weak, it can lose count and jump by a whole cycle, which
              looks like a sudden step in movement.</WhatIsThis>
            </Limitation>
          )}
        </ul>
        {result.warnings.length > 0 && (
          <div className="mt-3 rounded-lg border border-[#E7D9B8] bg-[#FCF8EE] p-3">
            <div className="flex items-center gap-2 text-[12px] font-medium text-[#7A6438]">
              <AlertTriangle className="h-3.5 w-3.5" aria-hidden /> Notes from this run
            </div>
            <ul className="mt-1.5 list-inside list-disc space-y-1 text-[11.5px] leading-[1.4rem] text-[#7A6438]">
              {result.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {/* ---------- 7 · Advanced results ---------- */}
      <AdvancedResults result={result} v={v} />
    </div>
  );
}

/* ================================================================== */
/* Recorded results (Mojave)                                           */
/* ================================================================== */

export function RecordedResults({ rec }: { rec: RecordedRunResult }) {
  const s = rec.summary;
  const mf = rec.measuredFindings;
  const v = computeRecordedVerdict({ summary: s as never, measuredFindings: mf });
  const floorMm = (s.systematicIonoFloorM ?? 0) * 1000;
  const signalMm = (mf?.maxAbsLosM ?? 0) * 1000;
  return (
    <div className="space-y-6">
      {/* verdict */}
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35 }}
        className="rounded-xl border border-[#D8D2C6] bg-[#F7F4EE] px-4 py-4"
        role="status"
      >
        <p className="flex items-start gap-2.5 text-[15px] font-semibold leading-6 text-[#1C1A17]">
          <ArchiveRestore className="mt-0.5 h-4.5 w-4.5 shrink-0" size={18} aria-hidden />
          {rec.narrative.verdict ?? v.headline}
        </p>
        <p className="mt-1.5 pl-7 text-[12.5px] leading-[1.45rem] text-[#6F6862]">{v.support}</p>
      </motion.div>

      {/* recorded banner */}
      <div className="rounded-lg border border-[#B08D57]/45 bg-[#F7F2E7] px-3.5 py-3" role="note">
        <p className="text-[12px] leading-[1.45rem] text-[#7A6438]">
          <strong className="font-semibold">Recorded result.</strong> This analysis was run on the real
          {' '}{rec.provenance.productId.slice(0, 42)}… product on {rec.recordedAt.slice(0, 10)}. The 2.6 GB source file
          is no longer stored on this server, so it cannot currently be re-run — every number below was
          captured from that run, and the map shows the granule footprint and the real in-frame earthquake
          rather than a measurement layer.
        </p>
      </div>

      {/* what did we find */}
      <section aria-labelledby="rec-what-found">
        <h2 id="rec-what-found" className="text-[13.5px] font-semibold text-[#1C1A17]">What did we find?</h2>
        <p className="mt-2 text-[13px] leading-[1.5rem] text-[#4A443C]">{rec.narrative.whatChanged}</p>
        <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3.5">
          <Stat label="Largest apparent signal" value={`${signalMm.toFixed(0)} mm`} title="Largest apparent LOS range change over the single valid unwrapping region (recorded)." />
          <Stat label="Ionosphere noise floor" value={`± ${floorMm.toFixed(0)} mm`} title="Systematic floor from the supplied-but-unapplied ionospheric screen (recorded)." />
          <Stat label="Measured area" value={areaLabel(s.analysedAreaKm2)} title="Area that passed the quality mask in the recorded run." />
          <Stat label="Reliable-signal coverage" value={`${(s.validFraction * 100).toFixed(1)}% of the frame`} title="Only this fraction of the frame could be measured at all (recorded)." />
          {mf?.zeroPhaseFraction !== undefined && (
            <Stat label="No-change share of valid area" value={`${(mf.zeroPhaseFraction * 100).toFixed(1)}%`} title="Share of valid pixels with exactly-zero phase — a stable surface (recorded)." />
          )}
          <Stat label="Typical measurement noise" value={`± ${fmtMm(s.medianSigmaM)}`} title="Median per-pixel 1σ uncertainty (recorded)." />
        </div>
      </section>

      {/* signal vs noise chart */}
      <section aria-label="Signal versus noise chart" className="rounded-xl border border-[#EFEAE2] bg-white p-3.5 sm:p-4">
        <SignalVsNoiseCard
          signalMm={signalMm}
          floorMm={floorMm}
          sigmaMm={(s.medianSigmaM ?? 0) * 1000}
          zeroFraction={mf?.zeroPhaseFraction}
        />
        {mf?.distribution && (
          <div className="mt-3 border-t border-[#EFEAE2] pt-3">
            <MicroLabel>Recorded distribution of the measurable region</MicroLabel>
            <div className="mt-2 flex h-8 w-full overflow-hidden rounded-md border border-[#E7E2DA]">
              {mf.distribution.map((d, i) => (
                <div
                  key={i}
                  className={`flex items-center justify-center ${i === 0 ? 'bg-[#2C5F58]/15' : 'bg-[#B0714E]/35'}`}
                  style={{ width: `${d.fraction * 100}%` }}
                  title={`${d.label}: ${(d.fraction * 100).toFixed(1)}%`}
                >
                  {d.fraction > 0.08 && (
                    <span className="font-mono text-[9.5px] text-[#4A443C]">{(d.fraction * 100).toFixed(1)}%</span>
                  )}
                </div>
              ))}
            </div>
            <ul className="mt-1.5 space-y-0.5">
              {mf.distribution.map((d, i) => (
                <li key={i} className="text-[10.5px] leading-4 text-[#8A837B]">
                  <span className={`mr-1.5 inline-block h-2 w-2 rounded-sm align-middle ${i === 0 ? 'bg-[#2C5F58]/30' : 'bg-[#B0714E]/50'}`} aria-hidden />
                  {d.label} — {(d.fraction * 100).toFixed(1)}%
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {/* seismic context */}
      <section id="seismic-context" aria-label="Earthquake context">
        {rec.event && (
          <>
            <SeismicTimeline
              referenceTime={rec.provenance.referenceTime}
              secondaryTime={rec.provenance.secondaryTime}
              events={[rec.event as never]}
              primaryEventId={rec.event.id}
            />
            <div className="mt-3 rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3.5">
              <MicroLabel>The earthquake inside the frame</MicroLabel>
              <ul className="mt-2">
                <EventRow ev={rec.event as never} />
              </ul>
              <p className="mt-2 text-[12px] leading-[1.45rem] text-[#4A443C]">
                A real M{rec.event.magnitude.toFixed(2)} earthquake occurred inside the radar frame
                between the two acquisitions. The recorded attribution run classified coseismic
                deformation as <strong>insufficient evidence</strong>: the apparent changes (≤ {signalMm.toFixed(0)} mm)
                cannot be distinguished from the {floorMm.toFixed(0)} mm ionospheric contamination, and no
                fault geometry was invented to fill the gap left by the missing published mechanism.
              </p>
              {rec.attributionSummary?.methodNote && (
                <p className="mt-1.5 text-[11px] leading-4 text-[#8A837B]">{rec.attributionSummary.methodNote}</p>
              )}
            </div>
          </>
        )}
      </section>

      {/* how certain */}
      <section aria-labelledby="rec-how-certain">
        <SectionTitle icon={<Ruler className="h-3.5 w-3.5" aria-hidden />} id="rec-how-certain">
          How certain are we?
        </SectionTitle>
        <p className="mt-2 text-[13px] leading-[1.5rem] text-[#4A443C]">{rec.narrative.reliability}</p>
        <div className="mt-3">
          <WhyCantISeeAnything summary={s as never} />
        </div>
      </section>

      {/* limitations */}
      <section aria-labelledby="rec-limits">
        <SectionTitle icon={<ShieldQuestion className="h-3.5 w-3.5" aria-hidden />} id="rec-limits">
          What are the limitations?
        </SectionTitle>
        <ul className="mt-2 space-y-1.5 text-[12.5px] leading-[1.45rem] text-[#4A443C]">
          <Limitation>
            Recorded, not re-run: the source product is gone from this server, so these figures cannot
            currently be regenerated — restoring them needs an Earthdata-authenticated re-download
            (instructions in &ldquo;About this data&rdquo;).
          </Limitation>
          <Limitation>
            Movement is measured only toward or away from the satellite, and the un-applied
            ionospheric correction dominates this pair&rsquo;s error budget.
          </Limitation>
          <Limitation>
            Only {areaLabel(s.analysedAreaKm2)} of the frame was measurable at all; the rest of the
            scene had no reliable signal.
          </Limitation>
        </ul>
        {rec.warnings.length > 0 && (
          <div className="mt-3 rounded-lg border border-[#E7D9B8] bg-[#FCF8EE] p-3">
            <div className="flex items-center gap-2 text-[12px] font-medium text-[#7A6438]">
              <AlertTriangle className="h-3.5 w-3.5" aria-hidden /> Notes from the recorded run
            </div>
            <ul className="mt-1.5 list-inside list-disc space-y-1 text-[11.5px] leading-[1.4rem] text-[#7A6438]">
              {rec.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {/* provenance / advanced */}
      <section className="rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3.5">
        <MicroLabel>About the recorded product</MicroLabel>
        <dl className="mt-2 space-y-1.5 text-[11px] leading-4">
          <div className="flex gap-2">
            <dt className="w-24 shrink-0 font-medium uppercase tracking-[0.05em] text-[#8A837B]">Provider</dt>
            <dd className="text-[#4A443C]">{rec.provenance.provider}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-24 shrink-0 font-medium uppercase tracking-[0.05em] text-[#8A837B]">Pair</dt>
            <dd className="font-mono text-[10px] text-[#4A443C]">
              {rec.provenance.referenceTime.slice(0, 10)} → {rec.provenance.secondaryTime.slice(0, 10)} · {rec.provenance.temporalBaselineDays}-day baseline
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-24 shrink-0 font-medium uppercase tracking-[0.05em] text-[#8A837B]">Geometry</dt>
            <dd className="font-mono text-[10px] text-[#4A443C]">
              {rec.geometry.passDirection} {rec.geometry.lookDirection}-looking · incidence {rec.geometry.incidenceAngleDeg.toFixed(1)}° · {(rec.geometry.wavelength * 100).toFixed(1)} cm wavelength
            </dd>
          </div>
          {rec.provenance.doi && (
            <div className="flex gap-2">
              <dt className="w-24 shrink-0 font-medium uppercase tracking-[0.05em] text-[#8A837B]">DOI</dt>
              <dd className="font-mono text-[10px]">
                <a href={`https://doi.org/${rec.provenance.doi}`} target="_blank" rel="noopener noreferrer" className="text-[#4A443C] underline decoration-[#D8D2C6] underline-offset-2">
                  {rec.provenance.doi}
                </a>
              </dd>
            </div>
          )}
        </dl>
      </section>
    </div>
  );
}

/* ================================================================== */
/* Evidence-mode results (Panama)                                      */
/* ================================================================== */

export function EvidenceResults({
  result,
  onGranulePolys,
}: {
  result: EvidenceAnalysisResult;
  onGranulePolys?: (polys: Array<{ granule: PanamaGranule; poly: [number, number][] }>) => void;
}) {
  const ev = result.event;
  return (
    <div className="space-y-6">
      <section aria-label="What did we find">
        <div className="rounded-xl border border-[#2C5F58]/40 bg-[#EDF4F1] px-4 py-4" role="status">
          <p className="flex items-start gap-2.5 text-[15px] font-semibold leading-6 text-[#2C5F58]">
            <CheckCircle2 className="mt-0.5 h-4.5 w-4.5 shrink-0" size={18} aria-hidden />
            Real earthquake, real radar data — measurement pending
          </p>
          <p className="mt-1.5 pl-7 text-[12px] leading-[1.45rem] text-[#2C5F58]">
            A strong earthquake occurred here, and radar data covering it exists. Measuring the ground
            movement requires downloading the full data product with a (free) NASA Earthdata account,
            which is not configured on this server. This page shows the real evidence and a clearly
            labelled model prediction instead — no measurement of this event is being claimed.
          </p>
        </div>
        <p className="mt-3 text-[13px] leading-[1.5rem] text-[#4A443C]">{result.narrative.whatChanged}</p>
        {ev && (
          <div className="mt-3">
            <SeismicTimeline
              referenceTime={new Date(Date.parse(ev.time) - 12 * 86400000).toISOString()}
              secondaryTime={new Date(Date.parse(ev.time) + 12 * 86400000).toISOString()}
              events={[ev, ...result.liveEvents.filter((e) => e.id !== ev.id)]}
              primaryEventId={ev.id}
              compact
            />
          </div>
        )}
        {result.warnings.length > 0 && (
          <ul className="mt-3 list-inside list-disc space-y-1 rounded-lg border border-[#E7D9B8] bg-[#FCF8EE] p-3 text-[11.5px] leading-[1.4rem] text-[#7A6438]">
            {result.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        )}
      </section>
      <EvidenceViewer result={result} onGranulePolys={onGranulePolys} />
    </div>
  );
}

/* ================================================================== */
/* shared pieces                                                       */
/* ================================================================== */

function SectionTitle({ icon, id, children }: { icon: React.ReactNode; id: string; children: React.ReactNode }) {
  return (
    <h3 id={id} className="flex items-center gap-2 text-[13.5px] font-semibold text-[#1C1A17]">
      <span className="text-[#B08D57]">{icon}</span>
      {children}
    </h3>
  );
}

function Limitation({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex gap-2">
      <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-[#B08D57]" aria-hidden />
      <span>{children}</span>
    </li>
  );
}

/** Collapsible technical evidence — the full statistical record. */
function AdvancedResults({
  result,
  v,
}: {
  result: AnalysisResult & { runId?: string };
  v: AnalysisResult['validation'];
}) {
  const [open, setOpen] = useState(false);
  const s = result.summary;
  return (
    <section aria-labelledby="advanced-results" className="rounded-lg border border-[#EFEAE2] bg-[#FDFCF9]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls="advanced-results-body"
        className="flex w-full items-center justify-between px-3.5 py-3 text-left"
      >
        <span id="advanced-results" className="flex items-center gap-2 text-[12.5px] font-semibold text-[#1C1A17]">
          <Microscope className="h-3.5 w-3.5 text-[#8A837B]" aria-hidden />
          Advanced results — inspect the technical evidence
        </span>
        <ChevronDown className={`h-4 w-4 shrink-0 text-[#8A837B] transition-transform duration-150 ${open ? 'rotate-180' : ''}`} aria-hidden />
      </button>
      {open && (
        <div id="advanced-results-body" className="space-y-4 border-t border-[#EFEAE2] px-3.5 py-4">
          <div>
            <MicroLabel>Processing statistics</MicroLabel>
            <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
              <Stat label="Valid fraction" value={`${(s.validFraction * 100).toFixed(0)}%`} />
              <Stat label="Masked pixels" value={s.maskedPixelCount.toLocaleString()} />
              <Stat label="Reference scatter (σ)" value={fmtMm(s.referenceSigmaM)} />
              <Stat label="Detected pixels" value={s.detectedPixelCount.toLocaleString()} />
              <Stat label="Detected clusters ≥ 4 px" value={String(s.clusters.count)} />
              <Stat label="Largest cluster" value={s.clusters.largestKm2 > 0 ? `${s.clusters.largestKm2.toFixed(1)} km²` : '—'} />
              <Stat label="Unwrap components" value={String(s.connectedComponentCount)} />
              <Stat label="Iono floor" value={s.systematicIonoFloorM > 0 ? fmtMm(s.systematicIonoFloorM) : 'n/a'} />
              <Stat label="Median threshold" value={fmtMm(s.thresholdMedianM)} />
              <Stat label="Median σ" value={fmtMm(s.medianSigmaM)} />
              <Stat label="Coherence (median)" value={s.coherenceMedian.toFixed(2)} />
              <Stat label="Anomaly elongation" value={s.anomalyElongation !== undefined && s.anomalyElongation > 0 ? `${s.anomalyElongation.toFixed(2)} : 1` : '—'} />
            </div>
          </div>

          <div>
            <MicroLabel>Correction status of the consumed data layer</MicroLabel>
            <p className="mt-1.5 text-[11px] leading-4 text-[#6F6862]">{s.correctionStatus.note}</p>
          </div>

          {s.nullScreen.scenes > 0 && (
            <div>
              <MicroLabel>No-change simulation (exploratory screen)</MicroLabel>
              <p className="mt-1.5 text-[11px] leading-4 text-[#6F6862]">
                {s.nullScreen.scenes} simulated no-change scenes with spatially correlated noise
                (correlation length {s.nullScreen.correlationLengthPx} px, deterministic seeds) produced a
                mean false-exceedance fraction of {(s.nullScreen.detectedFractionMean * 100).toFixed(2)}%
                (worst scene {(s.nullScreen.detectedFractionMax * 100).toFixed(2)}%;{' '}
                {s.nullScreen.scenesWithClusters} of {s.nullScreen.scenes} scenes formed ≥ 4-pixel clusters).
                {' '}{s.nullScreen.note}
              </p>
            </div>
          )}

          {v && (
            <div className="rounded-lg border border-[#E2CDBA] bg-[#FAF3EA] p-3">
              <MicroLabel>Ground-truth check (demonstration data)</MicroLabel>
              <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
                <Stat label="MAE" value={fmtMm(v.maeM)} />
                <Stat label="RMSE" value={fmtMm(v.rmseM)} />
                <Stat label="Bias" value={fmtMm(v.biasM)} />
                <Stat label="Points" value={v.n.toLocaleString()} />
              </div>
              <p className="mt-2 text-[11px] leading-4 text-[#8A837B]">{v.note}</p>
            </div>
          )}

          {result.attribution.events.length > 0 && (
            <div>
              <MicroLabel>Independent earthquake records (USGS)</MicroLabel>
              <ul className="mt-2 space-y-1.5">
                {result.attribution.events.slice(0, 6).map((ev) => (
                  <EventRow key={ev.id} ev={ev} />
                ))}
              </ul>
            </div>
          )}

          <p className="border-t border-[#EFEAE2] pt-3 text-[11px] text-[#8A837B]">
            Processed in {result.processingMs} ms · {result.params.coherenceThreshold} coherence threshold · {result.params.significanceK}σ detection · run {result.runId ?? result.datasetId}
          </p>
        </div>
      )}
    </section>
  );
}

function HypothesisRow({ h }: { h: import('@/lib/science/types').Hypothesis }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-lg border border-[#EFEAE2] bg-white">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left transition-colors hover:bg-[#FDFCF9]"
      >
        <ChevronRight
          className={`h-3.5 w-3.5 shrink-0 text-[#8A837B] transition-transform duration-150 ${open ? 'rotate-90' : ''}`}
          aria-hidden
        />
        <HypothesisStatusBadge status={h.status} />
        <span className="text-[13px] font-medium text-[#1C1A17]">{h.label}</span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-[#EFEAE2] px-3.5 py-3">
          <p className="text-[12.5px] leading-[1.45rem] text-[#4A443C]">{h.description}</p>
          <ul className="list-inside list-disc space-y-1.5">
            {h.evidence.map((e, i) => (
              <li key={i} className="text-[12px] leading-[1.45rem] text-[#6F6862]">{e}</li>
            ))}
          </ul>
          {h.discriminatingObservation && (
            <p className="border-l-2 border-[#B08D57] pl-2.5 text-[11.5px] italic leading-[1.4rem] text-[#8A837B]">
              What would settle it: {h.discriminatingObservation}
            </p>
          )}
        </div>
      )}
    </li>
  );
}

export { EventRow };

/* ================================================================== */
/* SBAS time-series results (system plan §12 pipeline)                 */
/* ================================================================== */

import {
  SbasSeriesChart,
  PosteriorBars,
  GnssValidationCard,
  CorrectionsLedger,
} from './charts';
import type { SbasResult } from '@/lib/science/types';

export function SbasResults({ result }: { result: SbasResult & { runId?: string } }) {
  const s = result.summary;
  const stepAnomalies = result.anomalies.filter((a) => a.id.startsWith('step-'));
  const velAnomalies = result.anomalies.filter((a) => !a.id.startsWith('step-'));
  const mmYr = (v: number) => (v * 1000).toFixed(0);

  return (
    <div className="space-y-6">
      {/* ---------- 1 · The verdict ---------- */}
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        className="rounded-xl border border-[#E3D5B8] bg-gradient-to-br from-[#FBF6EA] to-[#F6EEDC] p-4"
      >
        <p className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[#8F6A2B]">
          The one-line answer · SBAS time series ({s.epochCount} epochs / {s.ifgCount} interferograms)
        </p>
        <p className="mt-1.5 text-[15px] font-medium leading-[1.55rem] text-[#1C1A17]">
          {result.narrative.verdict}
        </p>
      </motion.div>
      <p className="-mt-3 rounded-lg border border-[#B0714E]/40 bg-[#F7EBE2] px-3 py-2 text-[11.5px] leading-4 text-[#8F4A2B]">
        Demonstration stack: a synthetic validation fixture parameterized from published Central Valley rates
        (plan §10 case study analog). It exercises the complete system pipeline against known truth — it is
        not an observation of the Earth.
      </p>

      {/* ---------- 2 · What did we find? ---------- */}
      <section aria-labelledby="sbas-what">
        <h2 id="sbas-what" className="text-[13.5px] font-semibold text-[#1C1A17]">What did we find?</h2>
        <p className="mt-2 text-[13px] leading-[1.5rem] text-[#4A443C]">{result.narrative.whatChanged}</p>
        <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3.5 sm:grid-cols-3">
          <Stat label="Fastest away-from-satellite rate" value={`${mmYr(s.maxVelocityMPerYr)} mm/yr`} title="Peak positive LOS velocity (ground moving away from the satellite; subsidence convention)." />
          <Stat label="Area above rate floor" value={`${s.fastAreaKm2.toFixed(0)} km²`} title={`Area exceeding ${mmYr(s.rateThresholdMPerYr)} mm/yr LOS velocity.`} />
          <Stat label="Median velocity σ" value={`± ${mmYr(s.medianVelocitySigmaMPerYr)} mm/yr`} title="Median per-pixel 1σ of the secular velocity estimate." />
          <Stat label="Temporal-coherence retention" value={`${(s.temporalCoherenceRetainedFraction * 100).toFixed(0)}%`} title="Fraction of coherent pixels passing the γ_t ≥ 0.5 gate (plan §11 benchmark)." />
          <Stat label="Valid coverage" value={`${(s.validFraction * 100).toFixed(0)}%`} title="Fraction of the frame retained through the coherence and temporal gates." />
          <Stat label="Regularization λ" value={s.lambda.toFixed(2)} title="Tikhonov first-difference weight selected by the L-curve / discrepancy principle (plan §7)." />
        </div>
        <p className="mt-2 text-[13px] leading-[1.5rem] text-[#4A443C]">{result.narrative.howMuch}</p>
      </section>

      {/* ---------- 3 · Time series (PELT + epochs) ---------- */}
      <section aria-labelledby="sbas-series">
        <SectionTitle icon={<Activity className="h-3.5 w-3.5" aria-hidden />} id="sbas-series">
          Time series — where the motion lives
        </SectionTitle>
        <div className="mt-2 rounded-xl border border-[#EFEAE2] bg-white p-3.5">
          <SbasSeriesChart series={result.keySeries} />
        </div>
        <EpochHints result={result} />
      </section>

      {/* ---------- 4 · Bayesian causal attribution ---------- */}
      <section aria-labelledby="sbas-bayes">
        <SectionTitle icon={<Microscope className="h-3.5 w-3.5" aria-hidden />} id="sbas-bayes">
          What could explain it? — Bayesian causal engine (plan §8)
        </SectionTitle>
        <p className="mt-2 text-[12.5px] leading-[1.45rem] text-[#4A443C]">
          Every detected anomaly is evaluated against three physical hypotheses — coseismic rupture
          (Okada forward model), aquifer compaction (linear + seasonal trend), and slope instability — with
          context-derived priors (fault-trace proximity, basin boundaries, steep slopes). The engine
          reports posterior probabilities, a normalized Shannon entropy, and a Bayes factor, and it{' '}
          <strong className="font-semibold text-[#1C1A17]">abstains</strong> (&ldquo;insufficient
          evidence&rdquo;) when entropy exceeds 0.75 or the leading posterior stays below 60%.
        </p>
        <div className="mt-3 space-y-3">
          {[...stepAnomalies, ...velAnomalies].map((a) => (
            <PosteriorBars key={a.id} anomaly={a} />
          ))}
        </div>
        {result.events.length > 0 && (
          <div className="mt-3 rounded-lg border border-[#EFE5CC] bg-[#FCF8EE] p-3">
            <p className="text-[11.5px] font-semibold text-[#7A6438]">Catalog events in the stack window</p>
            {result.events.map((ev) => (
              <p key={ev.id} className="mt-1 text-[11.5px] leading-4 text-[#6F6862]">
                <span className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle ${ev.synthetic ? 'bg-[#C05621]' : 'bg-[#6B8E5A]'}`} />
                {ev.label} · {new Date(ev.time).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })} ·{' '}
                <a href={ev.url} target="_blank" rel="noreferrer" className="underline decoration-dotted underline-offset-2 hover:text-[#1C1A17]">
                  source
                </a>
              </p>
            ))}
          </div>
        )}
      </section>

      {/* ---------- 5 · Validation ---------- */}
      <section aria-labelledby="sbas-valid">
        <SectionTitle icon={<ShieldQuestion className="h-3.5 w-3.5" aria-hidden />} id="sbas-valid">
          How well does it work? — validation (plan §11)
        </SectionTitle>
        <div className="mt-2 space-y-4">
          <GnssValidationCard gnss={result.gnss} />
          <div className="grid gap-3 sm:grid-cols-2">
            {result.brier && (
              <div className="rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3">
                <p className="text-[11px] uppercase tracking-wide text-[#8A837B]">Brier score (calibration)</p>
                <p className={`mt-1 text-[20px] font-semibold ${result.brier.score <= 0.15 ? 'text-[#4A6B3A]' : 'text-[#8F6A2B]'}`}>
                  {result.brier.score.toFixed(3)}
                </p>
                <p className="text-[10.5px] text-[#8A837B]">target ≤ 0.15 · n = {result.brier.n}</p>
                <p className="mt-1.5 text-[11px] leading-4 text-[#6F6862]">{result.brier.note}</p>
              </div>
            )}
            {result.truthValidation && (
              <div className="rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3">
                <p className="text-[11px] uppercase tracking-wide text-[#8A837B]">Velocity RMSE vs analytic truth</p>
                <p className="mt-1 text-[20px] font-semibold text-[#1C1A17]">
                  {(result.truthValidation.velocityRmseMPerYr * 1000).toFixed(1)}{' '}
                  <span className="text-[12px] font-normal text-[#8A837B]">mm/yr</span>
                </p>
                <p className="mt-1.5 text-[11px] leading-4 text-[#6F6862]">{result.truthValidation.note}</p>
              </div>
            )}
          </div>
        </div>
      </section>

      {/* ---------- 6 · Corrections ledger ---------- */}
      <section aria-labelledby="sbas-atm">
        <SectionTitle icon={<Waves className="h-3.5 w-3.5" aria-hidden />} id="sbas-atm">
          What was corrected? — phase-budget ledger (plan §6)
        </SectionTitle>
        <div className="mt-2">
          <CorrectionsLedger corrections={result.corrections} />
        </div>
      </section>

      {/* ---------- 7 · Reliability ---------- */}
      <section aria-labelledby="sbas-reli">
        <SectionTitle icon={<Ruler className="h-3.5 w-3.5" aria-hidden />} id="sbas-reli">
          How certain are we?
        </SectionTitle>
        <p className="mt-2 text-[13px] leading-[1.5rem] text-[#4A443C]">{result.narrative.reliability}</p>
        {result.warnings.length > 0 && (
          <ul className="mt-2 list-disc space-y-1 pl-4 text-[11.5px] leading-4 text-[#8F6A2B]">
            {result.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function EpochHints({ result }: { result: SbasResult }) {
  const quake = result.keySeries.find((k) => k.id === 'quake-step');
  const bowl = result.keySeries.find((k) => k.id === 'basin-bowl' || k.id === 'subsidence-peak');
  return (
    <div className="mt-2 grid gap-2 sm:grid-cols-2">
      {quake && quake.changePoints.length > 0 && (
        <div className="rounded-lg border border-[#E8D8C4] bg-[#FBF4EA] p-2.5">
          <p className="text-[11.5px] font-semibold text-[#8F4A2B]">Earthquake signature</p>
          <p className="mt-1 text-[11.5px] leading-4 text-[#6F6862]">
            The PELT-isolated transient of{' '}
            <strong className="font-semibold text-[#1C1A17]">
              {(Math.abs(quake.changePoints[0].stepM) * 1000).toFixed(0)} mm
            </strong>{' '}
            on {quake.changePoints[0].time.slice(0, 10)} is refit as an explicit step parameter, so the
            secular velocity map is not contaminated by the coseismic displacement.
          </p>
        </div>
      )}
      {bowl && (
        <div className="rounded-lg border border-[#DDE5D5] bg-[#F5F8F1] p-2.5">
          <p className="text-[11.5px] font-semibold text-[#4A6B3A]">Subsidence signature</p>
          <p className="mt-1 text-[11.5px] leading-4 text-[#6F6862]">
            The bowl series moves steadily at{' '}
            <strong className="font-semibold text-[#1C1A17]">{(bowl.velocityMPerYr * 1000).toFixed(0)} mm/yr</strong>{' '}
            (trend R² {bowl.linearR2.toFixed(2)}) with the seasonal pumping modulation visible — the
            aquifer-compaction fingerprint.
          </p>
        </div>
      )}
    </div>
  );
}
