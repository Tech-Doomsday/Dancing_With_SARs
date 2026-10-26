'use client';

/**
 * "Find a good radar pair" — a real tool on top of the live NASA CMR catalog.
 *
 * The user picks a place (presets or coordinates); the server searches live
 * CMR for NISAR GUNW pairs over it, ranks them by honest metadata-only
 * criteria, and shows the live USGS earthquake context for each pair's
 * window. The ranking never claims measured coherence — it says so.
 */

import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Search, Loader2, Radar, ExternalLink, Copy, Check, MapPin, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { MicroLabel, fmtDateTime } from './ui-bits';

interface RankedPairDto {
  pairKey: string;
  reference: { granuleId: string; time: string };
  secondary: { granuleId: string; time: string };
  temporalBaselineDays: number;
  sizeMB: number;
  dataUrl?: string;
  browseUrl?: string;
  conceptId: string;
  windowEvents: Array<{ id: string; magnitude: number; place: string; time: string; lat: number; lon: number }>;
  rankReasons: string[];
  score: number;
}

interface PairSearchDto {
  ok: boolean;
  pairs?: RankedPairDto[];
  hits?: number | null;
  granuleCount?: number;
  note?: string;
  error?: string;
}

const PLACES: Array<{ label: string; lat: number; lon: number }> = [
  { label: 'Mojave Desert, USA', lat: 35.05, lon: -117.7 },
  { label: 'Los Angeles, USA', lat: 34.05, lon: -118.24 },
  { label: 'Panama', lat: 8.5, lon: -80.0 },
  { label: 'Istanbul, Türkiye', lat: 41.0, lon: 29.0 },
  { label: 'Tokyo, Japan', lat: 35.7, lon: 139.7 },
  { label: 'Reykjanes, Iceland', lat: 63.9, lon: -22.4 },
  { label: 'Himalaya front, Nepal', lat: 27.7, lon: 85.3 },
  { label: 'Atacama, Chile', lat: -23.5, lon: -70.4 },
];

function scoreColor(score: number): string {
  if (score >= 75) return 'border-[#2C5F58] bg-[#EDF4F1] text-[#2C5F58]';
  if (score >= 55) return 'border-[#B08D57] bg-[#F7F2E7] text-[#7A6438]';
  return 'border-[#D8D2C6] bg-[#F7F4EE] text-[#6F6862]';
}

function PairRow({ pair, rank }: { pair: RankedPairDto; rank: number }) {
  const [copied, setCopied] = useState(false);
  const quakes = pair.windowEvents;
  return (
    <motion.li
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(0.3, rank * 0.05), duration: 0.25 }}
      className="rounded-xl border border-[#E7E2DA] bg-white p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.06em] text-[#8A837B]">#{rank + 1}</span>
            <span className="rounded-full border border-[#E7E2DA] bg-[#FDFCF9] px-2 py-0.5 font-mono text-[10.5px] text-[#4A443C]">
              {pair.temporalBaselineDays.toFixed(0)}-day pair
            </span>
            <span className="rounded-full border border-[#E7E2DA] bg-[#FDFCF9] px-2 py-0.5 font-mono text-[10.5px] text-[#4A443C]">
              {(pair.sizeMB / 1024).toFixed(1)} GB
            </span>
            {quakes.some((q) => q.magnitude >= 5.5) && (
              <span className="rounded-full border border-[#9E3B2C] bg-[#FBF3EF] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.05em] text-[#9E3B2C]">
                M{quakes.find((q) => q.magnitude >= 5.5)!.magnitude.toFixed(1)} in window
              </span>
            )}
          </div>
          <p className="mt-1.5 font-mono text-[11px] leading-4 text-[#4A443C]">
            {fmtDateTime(pair.reference.time).slice(0, 11)} → {fmtDateTime(pair.secondary.time).slice(0, 11)}
          </p>
        </div>
        <div className="flex items-center gap-2.5">
          <div className={`flex h-10 w-14 shrink-0 flex-col items-center justify-center rounded-lg border ${scoreColor(pair.score)}`} title="Metadata-only promise score — not measured coherence">
            <span className="font-mono text-[14px] font-semibold leading-none tabular-nums">{pair.score}</span>
            <span className="text-[8.5px] uppercase tracking-[0.06em] opacity-80">promise</span>
          </div>
        </div>
      </div>

      {/* why this rank */}
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {pair.rankReasons.slice(0, 4).map((r, i) => (
          <span key={i} className="rounded-full bg-[#F7F4EE] px-2 py-0.5 text-[10.5px] leading-4 text-[#6F6862]">
            {r}
          </span>
        ))}
      </div>

      {/* earthquake context */}
      {quakes.length > 0 && (
        <div className="mt-2.5 rounded-lg border border-[#EFEAE2] bg-[#FDFCF9] px-3 py-2">
          <p className="text-[10.5px] font-medium uppercase tracking-[0.06em] text-[#8A837B]">
            Earthquakes inside this pair&rsquo;s window (live USGS)
          </p>
          <ul className="mt-1 space-y-0.5">
            {quakes.slice(0, 4).map((q) => (
              <li key={q.id} className="flex items-baseline gap-2 text-[11.5px] text-[#4A443C]">
                <span className="font-mono font-semibold text-[#9E3B2C]">M{q.magnitude.toFixed(1)}</span>
                <span className="truncate">{q.place}</span>
                <span className="ml-auto shrink-0 font-mono text-[10px] text-[#8A837B]">{fmtDateTime(q.time).slice(0, 11)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* actions */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {pair.dataUrl && (
          <a
            href={pair.dataUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-[#D8D2C6] px-2.5 py-1.5 text-[11px] font-medium text-[#4A443C] transition-colors hover:bg-[#F7F4EE]"
          >
            <ExternalLink className="h-3 w-3" aria-hidden />
            Product on ASF (Earthdata login)
          </a>
        )}
        {pair.browseUrl && (
          <a
            href={pair.browseUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg border border-[#D8D2C6] px-2.5 py-1.5 text-[11px] font-medium text-[#4A443C] transition-colors hover:bg-[#F7F4EE]"
          >
            <ExternalLink className="h-3 w-3" aria-hidden />
            Official browse image
          </a>
        )}
        <button
          type="button"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(pair.secondary.granuleId);
              setCopied(true);
              setTimeout(() => setCopied(false), 1800);
            } catch { /* clipboard unavailable */ }
          }}
          className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 font-mono text-[10px] text-[#8A837B] transition-colors hover:text-[#1C1A17]"
          title="Copy the full granule ID"
        >
          {copied ? <Check className="h-3 w-3 text-[#2C5F58]" aria-hidden /> : <Copy className="h-3 w-3" aria-hidden />}
          {copied ? 'copied' : `${pair.secondary.granuleId.slice(0, 34)}…`}
        </button>
      </div>
    </motion.li>
  );
}

export function PairFinder() {
  const [place, setPlace] = useState(PLACES[0]);
  const [latInput, setLatInput] = useState(String(PLACES[0].lat));
  const [lonInput, setLonInput] = useState(String(PLACES[0].lon));
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<PairSearchDto | null>(null);
  const [error, setError] = useState<string | null>(null);

  const search = async (lat: number, lon: number, label: string) => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch('/api/pairs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lat, lon, placeLabel: label }),
      });
      const data = (await res.json()) as PairSearchDto;
      if (!data.ok) throw new Error(data.error ?? 'The catalog search failed.');
      setResult(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section aria-labelledby="pair-finder" className="mt-10 rounded-2xl border border-[#E7E2DA] bg-white p-5 sm:p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 id="pair-finder" className="flex items-center gap-2 text-[16px] font-semibold tracking-[-0.005em] text-[#1C1A17]">
            <Radar className="h-4.5 w-4.5 text-[#B08D57]" size={18} aria-hidden />
            Find a good radar pair for any place
          </h2>
          <p className="mt-1 max-w-2xl text-[12.5px] leading-[1.45rem] text-[#6F6862]">
            Searches NASA&rsquo;s live CMR catalog for recent NISAR radar pairs (12-day revisit) over a place you
            pick, and checks the live USGS earthquake catalog for each pair&rsquo;s measurement window. Ranked by
            catalog metadata — the real test is analysing the product.
          </p>
        </div>
      </div>

      {/* place chips */}
      <div className="mt-4 flex flex-wrap gap-1.5" role="group" aria-label="Preset places">
        {PLACES.map((p) => (
          <button
            key={p.label}
            type="button"
            aria-pressed={p.label === place.label}
            onClick={() => {
              setPlace(p);
              setLatInput(String(p.lat));
              setLonInput(String(p.lon));
            }}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[11.5px] transition-colors ${
              p.label === place.label
                ? 'border-[#1C1A17] bg-[#1C1A17] text-white'
                : 'border-[#D8D2C6] bg-white text-[#4A443C] hover:bg-[#F7F4EE]'
            }`}
          >
            <MapPin className="h-3 w-3" aria-hidden />
            {p.label}
          </button>
        ))}
      </div>

      {/* custom coords + search */}
      <div className="mt-3 flex flex-wrap items-end gap-2.5">
        <label className="block">
          <MicroLabel>Latitude</MicroLabel>
          <Input
            value={latInput}
            onChange={(e) => setLatInput(e.target.value)}
            className="mt-1 h-9 w-24 rounded-lg border-[#D8D2C6] font-mono text-[12px]"
            inputMode="decimal"
            aria-label="Latitude"
          />
        </label>
        <label className="block">
          <MicroLabel>Longitude</MicroLabel>
          <Input
            value={lonInput}
            onChange={(e) => setLonInput(e.target.value)}
            className="mt-1 h-9 w-24 rounded-lg border-[#D8D2C6] font-mono text-[12px]"
            inputMode="decimal"
            aria-label="Longitude"
          />
        </label>
        <Button
          onClick={() => {
            const lat = Number(latInput);
            const lon = Number(lonInput);
            if (Number.isFinite(lat) && Number.isFinite(lon) && lat >= -85 && lat <= 85 && lon >= -180 && lon <= 180) {
              void search(lat, lon, `${lat.toFixed(2)}, ${lon.toFixed(2)}`);
            }
          }}
          disabled={loading}
          className="h-9 gap-2 rounded-lg bg-[#1C1A17] px-4 text-[12.5px] font-medium text-white hover:bg-[#33302B] disabled:opacity-60"
        >
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <Search className="h-3.5 w-3.5" aria-hidden />}
          {loading ? 'Searching live catalogs…' : 'Search pairs'}
        </Button>
        <p className="pb-2 text-[10.5px] text-[#8A837B]">last 120 days · M ≥ 4.5 earthquake context</p>
      </div>

      {/* results */}
      <AnimatePresence mode="wait">
        {error && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="mt-4 flex items-start gap-2 rounded-lg border border-[#E5C4B7] bg-[#FBF3EF] p-3 text-[12px] leading-[1.4rem] text-[#9E3B2C]" role="alert"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            {error} The NASA catalog may be busy — try again in a moment.
          </motion.div>
        )}
        {result && (
          <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }} className="mt-4">
            {result.pairs && result.pairs.length > 0 ? (
              <>
                <p className="text-[12px] text-[#6F6862]">
                  <strong className="font-semibold text-[#1C1A17]">{result.pairs.length} pairs</strong> found over{' '}
                  {place.label === `${Number(latInput).toFixed(2)}, ${Number(lonInput).toFixed(2)}` ? 'your point' : place.label}{' '}
                  {result.hits !== null && result.hits !== undefined && <span className="text-[#8A837B]">({result.hits.toLocaleString()} granules total in the catalog)</span>}
                </p>
                <ul className="mt-3 grid max-h-[520px] gap-3 overflow-y-auto pr-1 sm:grid-cols-2">
                  {result.pairs.map((p, i) => (
                    <PairRow key={p.pairKey} pair={p} rank={i} />
                  ))}
                </ul>
                {result.note && <p className="mt-3 text-[10.5px] leading-4 text-[#8A837B]">{result.note}</p>}
              </>
            ) : (
              <p className="rounded-lg border border-[#E7D9B8] bg-[#FCF8EE] p-3 text-[12px] leading-[1.4rem] text-[#7A6438]">
                No NISAR pairs were found over this point in the last 120 days. NISAR coverage is still building
                up — try a preset place or widen the area.
              </p>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
