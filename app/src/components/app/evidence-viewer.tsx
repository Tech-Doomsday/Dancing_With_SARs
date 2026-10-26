'use client';

/**
 * Evidence viewer (Panama): official NISAR browse imagery, granule table,
 * live CMR availability check and the USGS sequence.
 *
 * FINAL-PASS UI: section titles and explanations are plain-language first;
 * technical identifiers (granule IDs, catalog names) remain visible for
 * inspection.
 */

import { useState } from 'react';
import { Loader2, Satellite, RefreshCw, AlertTriangle, ExternalLink, Maximize2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { MicroLabel, fmtDateTime, EventRow } from './ui-bits';
import type { CmrLiveResult, EvidenceAnalysisResult, PanamaGranule } from './types';

function parsePoly(poly: string): [number, number][] {
  const nums = poly.trim().split(/\s+/).map(Number);
  const pts: [number, number][] = [];
  for (let i = 0; i + 1 < nums.length; i += 2) pts.push([nums[i + 1], nums[i]]);
  return pts;
}

function GranuleRow({ g, eventTime }: { g: PanamaGranule; eventTime?: string }) {
  const spans = eventTime
    ? Date.parse(g.timeStart) < Date.parse(eventTime) && Date.parse(g.timeEnd) > Date.parse(eventTime)
    : false;
  return (
    <tr className="border-b border-[#EFEAE2] last:border-0">
      <td className="py-2 pr-3 font-mono text-[10.5px] leading-3 text-[#6F6862]" title={g.granuleId}>
        {g.granuleId.slice(0, 26)}…{g.granuleId.slice(-13)}
      </td>
      <td className="whitespace-nowrap py-2 pr-3 font-mono text-[11px] tabular-nums text-[#4A443C]">
        {g.timeStart.slice(5, 10).replace('-', '/')} → {g.timeEnd.slice(5, 10).replace('-', '/')}
      </td>
      <td className="whitespace-nowrap py-2 pr-3 font-mono text-[11px] tabular-nums text-[#4A443C]">{g.sizeMB} MB</td>
      <td className="py-2 pr-3">
        {spans ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-[#2C5F58] bg-[#EDF4F1] px-1.5 py-0.5 text-[9.5px] font-semibold uppercase tracking-[0.04em] text-[#2C5F58]">
            <span className="h-1.5 w-1.5 rounded-full bg-[#2C5F58]" aria-hidden />
            spans event
          </span>
        ) : (
          <span className="text-[10px] text-[#8A837B]">—</span>
        )}
      </td>
      <td className="py-2 text-right">
        {g.dataUrl && (
          <a href={g.dataUrl} target="_blank" rel="noopener noreferrer" className="text-[11px] text-[#6F6862] underline decoration-[#D8D2C6] underline-offset-2 hover:text-[#1C1A17]">
            product
          </a>
        )}
      </td>
    </tr>
  );
}

export function EvidenceViewer({
  result,
  onGranulePolys,
}: {
  result: EvidenceAnalysisResult;
  onGranulePolys?: (polys: Array<{ granule: PanamaGranule; poly: [number, number][] }>) => void;
}) {
  const [selected, setSelected] = useState(0);
  const [zoomOpen, setZoomOpen] = useState(false);
  const [cmr, setCmr] = useState<CmrLiveResult | null>(null);
  const [cmrLoading, setCmrLoading] = useState(false);

  const granules = result.granules;
  const active = granules[selected];
  const eventTime = result.event?.time;

  const checkCmr = async () => {
    setCmrLoading(true);
    setCmr(null);
    try {
      // bbox from the first granule footprint + margin
      const pts = parsePoly(granules[0]?.polygon ?? '0 0 1 1 1 1 0 0');
      const lons = pts.map((p) => p[0]);
      const lats = pts.map((p) => p[1]);
      const bbox = [
        Math.min(...lons) - 0.3,
        Math.min(...lats) - 0.3,
        Math.max(...lons) + 0.3,
        Math.max(...lats) + 0.3,
      ].join(',');
      const res = await fetch(`/api/external/cmr?bbox=${bbox}${eventTime ? `&eventTime=${encodeURIComponent(eventTime)}` : ''}`);
      const data = (await res.json()) as CmrLiveResult;
      setCmr(data);
      if (data.ok && data.granules && onGranulePolys) {
        onGranulePolys(
          data.granules.slice(0, 8).map((g) => ({ granule: g, poly: parsePoly(g.polygon) })),
        );
      }
    } catch (err) {
      setCmr({ ok: false, error: (err as Error).message });
    } finally {
      setCmrLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <section aria-labelledby="browse">
        <div className="flex items-baseline justify-between">
          <h2 id="browse" className="text-[14px] font-semibold text-[#1C1A17]">Official radar images of the area</h2>
          <button
            type="button"
            onClick={() => setZoomOpen(true)}
            className="flex items-center gap-1 text-[11px] text-[#6F6862] hover:text-[#1C1A17]"
            aria-label="Open full-size image"
          >
            <Maximize2 className="h-3 w-3" aria-hidden /> full size
          </button>
        </div>
        <p className="mt-1 text-[12px] leading-[1.45rem] text-[#8A837B]">
          NASA&apos;s own rendered images of the radar data covering the earthquake area. The colors
          show the pattern in the raw radar comparison — read them as a preview, not as a finished
          measurement.
        </p>
        {active && (
          <figure className="mt-2.5">
            <img
              src={active.localBrowseUrl}
              alt={`NISAR GUNW browse rendering (geocoded unwrapped phase) for granule ${active.granuleId}`}
              className="w-full cursor-zoom-in rounded-lg border border-[#E7E2DA]"
              onClick={() => setZoomOpen(true)}
              loading="lazy"
            />
            <figcaption className="mt-1.5 font-mono text-[10px] leading-3 text-[#8A837B]">
              {active.granuleId.slice(0, 60)}… · {active.timeStart.slice(0, 10)} → {active.timeEnd.slice(0, 10)}
            </figcaption>
          </figure>
        )}
        {granules.length > 1 && (
          <div className="mt-2 flex gap-2">
            {granules.map((g, i) => (
              <button
                key={g.granuleId}
                type="button"
                onClick={() => setSelected(i)}
                aria-pressed={i === selected}
                className={`h-12 w-16 overflow-hidden rounded-md border transition-all ${
                  i === selected ? 'border-[#1C1A17] border-[1.5px]' : 'border-[#E7E2DA] opacity-70 hover:opacity-100'
                }`}
                aria-label={`Show browse image for granule ${g.granuleId.slice(-18)}`}
              >
                <img src={g.localBrowseUrl} alt="" className="h-full w-full object-cover" loading="lazy" />
              </button>
            ))}
          </div>
        )}
      </section>

      <section aria-labelledby="granules">
        <h2 id="granules" className="text-[14px] font-semibold text-[#1C1A17]">Radar data pairs that cover the earthquake</h2>
        <p className="mt-1 text-[12px] leading-[1.45rem] text-[#8A837B]">
          Each row is one radar data product. The date range shows when its two images were taken.
        </p>
        <div className="mt-2 overflow-hidden rounded-lg border border-[#EFEAE2]">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-[#EFEAE2] bg-[#FDFCF9]">
                <th className="px-3 py-2 text-[10px] font-medium uppercase tracking-[0.06em] text-[#8A837B]">Granule</th>
                <th className="px-0 py-2 text-[10px] font-medium uppercase tracking-[0.06em] text-[#8A837B]">Acquisition pair</th>
                <th className="px-0 py-2 text-[10px] font-medium uppercase tracking-[0.06em] text-[#8A837B]">Size</th>
                <th className="px-0 py-2 text-[10px] font-medium uppercase tracking-[0.06em] text-[#8A837B]">Window</th>
                <th className="px-0 py-2" />
              </tr>
            </thead>
            <tbody>
              {granules.map((g) => (
                <GranuleRow key={g.granuleId} g={g} eventTime={eventTime} />
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-1.5 text-[11px] leading-4 text-[#8A837B]">
          &ldquo;Spans event&rdquo; means the two radar images were taken before and after the
          earthquake, so any ground movement it caused would be captured between them. Downloading
          the full data files requires a free NASA Earthdata account.
        </p>
      </section>

      <section aria-labelledby="cmr-live">
        <div className="flex items-center justify-between">
          <div>
            <h2 id="cmr-live" className="text-[14px] font-semibold text-[#1C1A17]">Check NASA&apos;s data catalog now</h2>
            <p className="mt-0.5 text-[11.5px] text-[#8A837B]">Live query — confirms the data are still listed as available.</p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={checkCmr}
            disabled={cmrLoading}
            className="h-7 gap-1.5 rounded-md border-[#D8D2C6] px-2.5 text-[11px] font-normal text-[#4A443C] hover:bg-[#F7F4EE]"
          >
            {cmrLoading ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> : <RefreshCw className="h-3 w-3" aria-hidden />}
            {cmrLoading ? 'Querying…' : 'Check now'}
          </Button>
        </div>
        {cmrLoading && (
          <div className="mt-2 flex items-center gap-2 rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] px-3 py-2.5 text-[12px] text-[#6F6862]">
            <Satellite className="h-3.5 w-3.5" aria-hidden />
            Asking NASA&apos;s data catalog which radar products currently cover this region…
          </div>
        )}
        {cmr?.ok && cmr.granules && (
          <div className="mt-2 rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3">
            <div className="text-[12.5px] text-[#1C1A17]">
              <span className="font-mono font-semibold tabular-nums">{cmr.granules.length}</span> granules catalogue this region
              {eventTime && (
                <>
                  {' '}· <span className="font-mono font-semibold tabular-nums">{cmr.granules.filter((g) => g.bracketsEvent).length}</span> acquisition pairs bracket the event
                </>
              )}
            </div>
            <ul className="mt-2 max-h-56 space-y-1 overflow-y-auto pr-1 [scrollbar-width:thin]">
              {cmr.granules.slice(0, 8).map((g) => (
                <li key={g.granuleId} className="flex items-center justify-between gap-2 font-mono text-[10.5px] text-[#6F6862]">
                  <span className="truncate" title={g.granuleId}>{g.granuleId.slice(0, 44)}…</span>
                  <span className="whitespace-nowrap tabular-nums">{g.timeStart.slice(5, 10)} → {g.timeEnd.slice(5, 10)}</span>
                  {g.bracketsEvent && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[#2C5F58]" aria-label="brackets event" />}
                </li>
              ))}
            </ul>
            <p className="mt-2 border-t border-[#EFEAE2] pt-2 text-[10.5px] text-[#8A837B]">
              {cmr.source} · {cmr.queriedAt ? fmtDateTime(cmr.queriedAt) : ''}
            </p>
          </div>
        )}
        {cmr && !cmr.ok && (
          <div className="mt-2 flex items-start gap-2 rounded-lg border border-[#E7D9B8] bg-[#FCF8EE] px-3 py-2.5 text-[12px] text-[#7A6438]">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            <span>The live check could not reach NASA right now ({cmr.error ?? 'unknown error'}). The records above were verified on 2026-10-20 and remain valid as a snapshot.</span>
          </div>
        )}
        {!cmr && !cmrLoading && (
          <p className="mt-1.5 text-[11px] leading-4 text-[#8A837B]">
            This asks NASA&apos;s live data catalog. The records listed above were verified on 2026-10-20.
          </p>
        )}
      </section>

      {result.liveEvents.length > 0 && (
        <section aria-labelledby="sequence">
          <h2 id="sequence" className="text-[14px] font-semibold text-[#1C1A17]">Other earthquakes nearby (live USGS list)</h2>
          <p className="mt-1 text-[11.5px] leading-4 text-[#8A837B]">
            Independent earthquake records around the same days, from the USGS. The first is the main event.
          </p>
          <ul className="mt-2 space-y-1.5">
            {result.liveEvents.slice(0, 5).map((ev) => (
              <EventRow key={ev.id} ev={ev} />
            ))}
          </ul>
        </section>
      )}
      {result.liveStatus !== 'live' && (
        <div className="flex items-start gap-2 rounded-lg border border-[#E7D9B8] bg-[#FCF8EE] px-3 py-2.5 text-[11.5px] leading-[1.4rem] text-[#7A6438]">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>The live earthquake list is unreachable right now, so the records above come from the verified snapshot taken on 2026-10-20 (including the published fault information for the main event).</span>
        </div>
      )}

      <section aria-labelledby="model">
        <h2 id="model" className="text-[14px] font-semibold text-[#1C1A17]">What an earthquake model predicts</h2>
        <p className="mt-1 text-[11.5px] leading-4 text-[#8A837B]">
          A standard geophysics model, computed from the earthquake&apos;s published fault
          information. It is a labelled prediction — <strong>not a measurement of this event</strong>.
        </p>
        <p className="mt-2 text-[12.5px] leading-[1.5rem] text-[#4A443C]">{result.narrative.howMuch}</p>
        <p className="mt-2 text-[12.5px] leading-[1.5rem] text-[#4A443C]">{result.narrative.reliability}</p>
        {result.event?.nodalPlane && (
          <div className="mt-3 rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] p-3">
            <MicroLabel>Published fault information (USGS moment tensor, nodal plane 1)</MicroLabel>
            <div className="mt-1 font-mono text-[12px] tabular-nums text-[#4A443C]">
              strike {result.event.nodalPlane.strike.toFixed(1)}° · dip {result.event.nodalPlane.dip.toFixed(1)}° · rake {result.event.nodalPlane.rake.toFixed(1)}°
              {result.event.momentTensorSource ? ` · ${result.event.momentTensorSource}` : ''}
            </div>
            <a
              href={result.event.url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-[#6F6862] underline decoration-[#D8D2C6] underline-offset-2 hover:text-[#1C1A17]"
            >
              USGS event page <ExternalLink className="h-3 w-3" aria-hidden />
            </a>
          </div>
        )}
        <p className="mt-2 text-[11px] leading-4 text-[#8A837B]">
          Even a perfect match between this prediction and a future measurement would not prove
          causation on its own — it would be strong, checkable evidence.
        </p>
      </section>
    </div>
  );
}
