"use client";

/** ExplorerApp (§7.8) — the Granule Explorer: for any box, exactly which NISAR granules exist,
 *  on which tracks/passes, with what day gap, on a real UTC time scale. Layout (desktop):
 *  top bar → filter row → summary strip → timeline (fills height) | right detail panel → footer.
 *  Mobile: filters collapsible, timeline full width, detail as bottom sheet.
 *  Honesty: counts come from the engine (N of M, truncation, degradation), day gaps are
 *  engine-computed (DEC13), "nominal repeat" appears only if the engine supplies it (it does
 *  not, so it never renders). Zero science. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronDown, ChevronUp, RotateCcw, Search } from "lucide-react";
import { api, ENGINE_API, granulesBbox } from "@/lib/api";
import type { ExplorerGranule, ExplorerStack, GranuleExplorerResponse } from "@/lib/types";
import {
  emptyFilters, filterGranules,
  type ExplorerFilters,
} from "@/lib/explorer";
import { parseExplorerState, writeExplorerState, type ExplorerUrlState } from "@/lib/urlstate";
import { fmtUtc } from "@/lib/format";
import AppNav from "../AppNav";
import ExplorerFiltersRow from "./ExplorerFiltersRow";
import ExplorerSummary from "./ExplorerSummary";
import ExplorerDetail from "./ExplorerDetail";
import ExplorerList from "./ExplorerList";

const TimelineCanvas = dynamic(() => import("./TimelineCanvas"), {
  ssr: false,
  loading: () => <p className="p-6 text-xs text-[#8a837b]">loading the timeline…</p>,
});

const LEVELS = ["GUNW", "GCOV", "GOFF"] as const;

function num(v: string): number | undefined {
  if (v.trim() === "") return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

export default function ExplorerApp() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // ---- inputs (the box the query will use) ----------------------------------------------------
  const [west, setWest] = useState("");
  const [south, setSouth] = useState("");
  const [east, setEast] = useState("");
  const [north, setNorth] = useState("");
  const [levels, setLevels] = useState<string[]>(["GUNW"]);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [tileForBox, setTileForBox] = useState<string | null>(null);

  // ---- query state ----------------------------------------------------------------------------
  const [data, setData] = useState<GranuleExplorerResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retryAfter, setRetryAfter] = useState<number | null>(null);
  const [health, setHealth] = useState<boolean | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // ---- client-side filters + selection --------------------------------------------------------
  const [filters, setFilters] = useState<ExplorerFilters>(emptyFilters());
  const [onlyInView, setOnlyInView] = useState(false);
  const [viewWindow, setViewWindow] = useState<{ t0: number; t1: number } | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [listView, setListView] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);

  const bootRef = useRef(false);
  const runSearchRef = useRef<typeof runSearch | null>(null);

  // ---- boot: URL state (+ ?tile= resolution through the ENGINE, never client-side) -------------
  useEffect(() => {
    if (bootRef.current) return;
    bootRef.current = true;
    const st: ExplorerUrlState = parseExplorerState(new URLSearchParams(searchParams.toString()));
    if (st.west !== undefined) setWest(String(st.west));
    if (st.south !== undefined) setSouth(String(st.south));
    if (st.east !== undefined) setEast(String(st.east));
    if (st.north !== undefined) setNorth(String(st.north));
    if (st.level) setLevels(st.level.split(","));
    if (st.from) setFrom(st.from);
    if (st.to) setTo(st.to);
    if (st.track !== undefined) setFilters((f) => ({ ...f, track: st.track as number }));
    if (st.dir) setFilters((f) => ({ ...f, direction: st.dir as ExplorerFilters["direction"] }));
    if (st.sel) setSelectedIds([st.sel]);
    const tile = new URLSearchParams(searchParams.toString()).get("tile");
    if (tile && st.west === undefined) {
      api.tileAtId(tile).then((r) => {
        const [w, s, e, n] = r.tile.bbox_lonlat;
        setWest(String(Number(w.toFixed(4))));
        setSouth(String(Number(s.toFixed(4))));
        setEast(String(Number(e.toFixed(4))));
        setNorth(String(Number(n.toFixed(4))));
        setTileForBox(r.tile.id);
      }).catch(() => undefined);
    } else if (tile) {
      setTileForBox(tile);
    }
    api.health().then(() => setHealth(true)).catch(() => setHealth(false));
    // a pasted link restores the same view: a complete box in the URL runs the search ONCE
    if (st.west !== undefined && st.south !== undefined && st.east !== undefined && st.north !== undefined) {
      const bootLevels = st.level ? st.level.split(",") : ["GUNW"];
      // defer until after the first paint so the inputs render with the restored values
      setTimeout(() => runSearchRef.current?.(st.west!, st.south!, st.east!, st.north!, bootLevels, st.from ?? "", st.to ?? ""), 0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- URL write-back (validated; a pasted link restores the same view) ------------------------
  const pushUrl = useCallback((patch: Partial<ExplorerUrlState>) => {
    const params = new URLSearchParams(window.location.search);
    const st = parseExplorerState(params);
    writeExplorerState(params, { ...st, ...patch });
    router.replace(`/granules?${params.toString()}`, { scroll: false });
  }, [router]);

  // ---- the search. The Search button runs it with the form values; a pasted link with a full
  // box re-runs it once on boot (restoring the same view — the ONLY automatic search). ----
  const runSearch = useCallback((w: number, s: number, e: number, n: number, lvls: string[], fromIso: string, toIso: string) => {
    setError(null);
    setRetryAfter(null);
    if (w > e) {
      setError("box crosses the antimeridian (west > east) — split it at ±180");
      return;
    }
    if (s >= n) {
      setError("south must be below north");
      return;
    }
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    granulesBbox({
      west: w, south: s, east: e, north: n,
      level: lvls.join(",") || "GUNW",
      from: fromIso || undefined,
      to: toIso || undefined,
    }, ctrl.signal)
      .then((r) => {
        setData(r);
        pushUrl({ west: w, south: s, east: e, north: n, level: lvls.join(",") || undefined, from: fromIso || undefined, to: toIso || undefined });
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
        if (message.includes("429") || /rate limit/i.test(message)) setRetryAfter(60);
        setData(null);
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
  }, [pushUrl]);

  runSearchRef.current = runSearch;

  const search = useCallback(() => {
    const w = num(west);
    const s = num(south);
    const e = num(east);
    const n = num(north);
    if (w === undefined || s === undefined || e === undefined || n === undefined) {
      setError("enter the box as West, South, East, North (decimal degrees)");
      return;
    }
    runSearch(w, s, e, n, levels, from, to);
  }, [west, south, east, north, levels, from, to, runSearch]);

  // ---- filtering (client-side over the fetched set) --------------------------------------------
  const kept = useMemo(() => (data ? filterGranules(data.granules, filters) : new Set<string>()), [data, filters]);

  const inView = useMemo(() => {
    if (!onlyInView || !viewWindow || !data) return kept;
    const out = new Set<string>();
    for (const g of data.granules) {
      if (!kept.has(g.id)) continue;
      const times = [g.ref_time, g.sec_time, g.start].filter(Boolean) as string[];
      const hits = times.some((t) => {
        const ms = Date.parse(t);
        return Number.isFinite(ms) && ms >= viewWindow.t0 && ms <= viewWindow.t1;
      });
      if (hits) out.add(g.id);
    }
    return out;
  }, [kept, onlyInView, viewWindow, data]);

  const visible: ExplorerGranule[] = useMemo(
    () => (data ? data.granules.filter((g) => inView.has(g.id)) : []),
    [data, inView],
  );
  const visibleStacks: ExplorerStack[] = useMemo(() => {
    if (!data) return [];
    const ids = new Set(visible.map((g) => g.stack));
    return data.stacks.filter((s) => ids.has(s.key));
  }, [data, visible]);

  const selected = selectedIds.length > 0
    ? data?.granules.find((g) => g.id === selectedIds[selectedIds.length - 1]) ?? null
    : null;
  const selectedStack = selected ? visibleStacks.find((s) => s.key === selected.stack) ?? null : null;

  // ---- footer echo ------------------------------------------------------------------------------
  const queryEcho = data
    ? `box ${data.bbox.map((v) => Number(v.toFixed(4))).join(", ")} · window ${data.window.t0} → ${data.window.t1} · level ${levels.join(",")}`
    : null;

  return (
    <div className="explorer-root fixed inset-0 flex flex-col bg-[#0e1116] text-[#d9dee4]">
      {/* ---- top bar ---- */}
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-[#262c36] px-4 py-2.5">
        <AppNav active="granules" dark />
        <div className="ml-auto flex flex-wrap items-center gap-2 text-xs">
          <LabeledInput label="West" value={west} onChange={setWest} placeholder="-119.0" width="w-24" />
          <LabeledInput label="South" value={south} onChange={setSouth} placeholder="33.5" width="w-20" />
          <LabeledInput label="East" value={east} onChange={setEast} placeholder="-117.0" width="w-24" />
          <LabeledInput label="North" value={north} onChange={setNorth} placeholder="35.0" width="w-20" />
          <span className="flex items-center gap-1.5" role="group" aria-label="Level (multi-select)">
            {LEVELS.map((lv) => (
              <button
                key={lv}
                type="button"
                aria-pressed={levels.includes(lv)}
                onClick={() => setLevels((cur) => (cur.includes(lv) ? cur.filter((x) => x !== lv) : [...cur, lv]))}
                className={`rounded border px-2 py-1 font-mono text-[11px] ${
                  levels.includes(lv)
                    ? "border-[#3fb6c9] bg-[#12333a] text-[#7fd8e6]"
                    : "border-[#333b47] text-[#8b949e] hover:border-[#4a5462]"
                }`}
              >
                {lv}
              </button>
            ))}
          </span>
          <LabeledInput label="From (UTC)" value={from} onChange={setFrom} placeholder="2026-06-01" type="date" width="w-36" />
          <LabeledInput label="To (UTC)" value={to} onChange={setTo} placeholder="2026-10-30" type="date" width="w-36" />
          <button
            type="button"
            onClick={search}
            disabled={loading || health === false}
            className="flex items-center gap-1.5 rounded-md bg-[#2c5f58] px-3 py-1.5 text-xs font-medium text-white hover:bg-[#37736a] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Search size={13} aria-hidden /> Search
          </button>
          <button
            type="button"
            onClick={() => { setFilters(emptyFilters()); setOnlyInView(false); }}
            title="Reset filters (the box and window stay)"
            className="flex items-center gap-1 rounded-md border border-[#333b47] px-2 py-1.5 text-[11px] text-[#aab3bd] hover:border-[#4a5462]"
          >
            <RotateCcw size={12} aria-hidden /> Reset view
          </button>
        </div>
      </header>

      {/* ---- filter row (client-side over the fetched set) ---- */}
      <div className="border-b border-[#262c36] bg-[#12161d]">
        <button
          type="button"
          onClick={() => setFiltersOpen((o) => !o)}
          aria-expanded={filtersOpen}
          className="flex w-full items-center justify-between px-4 py-1.5 text-[11px] text-[#8b949e] hover:text-[#d9dee4] lg:hidden"
        >
          filters {filtersOpen ? <ChevronUp size={12} aria-hidden /> : <ChevronDown size={12} aria-hidden />}
        </button>
        <div className={`${filtersOpen ? "block" : "hidden"} lg:block`}>
          <ExplorerFiltersRow filters={filters} onChange={setFilters} />
        </div>
      </div>

      {/* ---- banners ---- */}
      {health === false && (
        <p role="alert" className="border-b border-[#4a2f24] bg-[#2a1712] px-4 py-2 text-xs text-[#e8a087]">
          the engine API is not reachable at <span className="font-mono">{ENGINE_API}</span> — the
          granule search runs on the engine (it searches ASF live), so nothing can be listed right now.
        </p>
      )}
      {error && !retryAfter && (
        <p role="alert" className="border-b border-[#4a2f24] bg-[#2a1712] px-4 py-2 text-xs text-[#e8a087]">
          {error}
        </p>
      )}
      {retryAfter !== null && (
        <p role="alert" className="border-b border-[#4a3a1f] bg-[#2a2212] px-4 py-2 text-xs text-[#e6c977]">
          rate limited by the engine — retry shortly (the granule search hits ASF live; the
          window resets automatically).
        </p>
      )}
      {data?.truncated && (
        <p role="status" className="border-b border-[#4a3a1f] bg-[#2a2212] px-4 py-2 text-xs text-[#e6c977]">
          result budget hit — this list is incomplete ({data.n_total} listed, the engine&apos;s search
          budget is {data.search_budget}); narrow the box or dates.
        </p>
      )}
      {data?.degraded && (
        <p role="status" className="border-b border-[#3a3524] bg-[#222012] px-4 py-2 text-xs text-[#d9c98a]">
          served from stale cache (ASF unreachable at search time) — counts may lag.
        </p>
      )}

      {/* ---- summary strip (always visible once a search ran) ---- */}
      {data && (
        <ExplorerSummary
          data={data}
          visibleCount={visible.length}
          keptCount={kept.size}
          fetchedCount={data.granules.length}
        />
      )}

      {/* ---- main area: timeline | detail ---- */}
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <section className="flex min-h-0 flex-1 flex-col" aria-label="granule timeline">
          {!data && !loading && (
            <div className="flex flex-1 items-center justify-center p-8 text-center text-xs text-[#8b949e]">
              enter a box and press Search — the engine answers how many NISAR granules exist
              there, on which tracks and passes, with what day gaps.
            </div>
          )}
          {loading && (
            <div className="flex flex-1 items-center justify-center gap-2 p-8 text-xs text-[#8b949e]" role="status">
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-[#3fb6c9] border-t-transparent" aria-hidden />
              searching…
            </div>
          )}
          {data && !loading && visible.length === 0 && (
            <div className="flex flex-1 items-center justify-center p-8 text-center text-xs text-[#8b949e]" role="status">
              {kept.size === 0 && data.n_total === 0
                ? "no granules match this box and window."
                : inView.size === 0
                  ? "no granules match the active filters (the search itself returned results)."
                  : "nothing in the visible time window — pan out or turn off “only in view”."}
            </div>
          )}
          {data && !loading && visible.length > 0 && (
            <div className="flex min-h-0 flex-1 flex-col p-2">
              <div className="mb-1 flex items-center justify-between px-1 text-[11px] text-[#8b949e]">
                <label className="flex items-center gap-1.5">
                  <input type="checkbox" checked={onlyInView} onChange={(e) => setOnlyInView(e.target.checked)} />
                  only in view
                </label>
                <label className="flex items-center gap-1.5">
                  <input type="checkbox" checked={listView} onChange={(e) => setListView(e.target.checked)} />
                  List view
                </label>
              </div>
              {listView ? (
                <ExplorerList
                  granules={visible}
                  stacks={data.stacks}
                  selectedIds={selectedIds}
                  onSelect={(id, extend) =>
                    setSelectedIds((cur) => (extend ? (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]) : [id]))}
                />
              ) : (
                <TimelineCanvas
                  data={data}
                  granules={visible}
                  stacks={visibleStacks}
                  selectedIds={selectedIds}
                  focusedId={focusedId}
                  onSelect={(id, extend) =>
                    setSelectedIds((cur) => (extend ? (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]) : [id]))}
                  onFocus={setFocusedId}
                  onViewWindow={setViewWindow}
                />
              )}
            </div>
          )}
        </section>

        {/* ---- detail panel (desktop right ≈380 px; mobile bottom sheet) ---- */}
        <aside className="explorer-detail max-h-[45vh] overflow-y-auto border-t border-[#262c36] bg-[#12161d] lg:max-h-none lg:w-[380px] lg:shrink-0 lg:overflow-y-auto lg:border-l lg:border-t-0">
          <ExplorerDetail
            granule={selected}
            stack={selectedStack}
            stackGranules={selected ? visible.filter((g) => g.stack === selected.stack) : []}
            bbox={data?.bbox ?? null}
            engineOnline={health === true}
            onClose={() => setSelectedIds([])}
            onShowOnGlobe={(lat, lon) => {
              window.location.href = `/?lat=${lat}&lon=${lon}&z=7`;
            }}
            onScanTile={(tileId) => {
              window.location.href = `/?tile=${encodeURIComponent(tileId)}&panel=pin`;
            }}
          />
        </aside>
      </div>

      {/* ---- footer ---- */}
      <footer className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-[#262c36] px-4 py-1.5 text-[10px] text-[#8b949e]">
        <span className="font-mono">{queryEcho ?? "no query yet"}</span>
        {data && <span>generated {fmtUtc(data.generated_at)}</span>}
        {tileForBox && <span>tile {tileForBox}</span>}
        <span className="ml-auto">times are UTC · day gaps computed by the engine</span>
      </footer>

      {/* aria-live readout for keyboard focus */}
      <div aria-live="polite" className="sr-only">
        {focusedId && data
          ? `focused: ${describeGranule(data.granules.find((g) => g.id === focusedId))}`
          : selectedIds.length > 0
            ? `selected: ${describeGranule(selected)}`
            : ""}
      </div>
    </div>
  );
}

function describeGranule(g?: ExplorerGranule | null): string {
  if (!g) return "nothing";
  const t = g.ref_time ?? g.start ?? "undated";
  return `${g.id} — ${g.stack}, ${t}${g.baseline_days != null ? `, baseline ${g.baseline_days} days` : ""}`;
}

function LabeledInput({ label, value, onChange, placeholder, width = "w-24", type = "text" }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  width?: string;
  type?: string;
}) {
  return (
    <label className="flex items-center gap-1 text-[11px] text-[#8b949e]">
      {label}
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={`${width} rounded border border-[#333b47] bg-[#0e1116] px-1.5 py-1 font-mono text-[11px] text-[#e6eaee] placeholder:text-[#5a636e] focus:border-[#3fb6c9] focus:outline-none`}
      />
    </label>
  );
}
