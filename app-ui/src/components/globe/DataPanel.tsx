"use client";

/** DataPanel (§7.5) — the fourth tab: granule availability for the selected tile. LIVE-ENGINE-ONLY
 *  (DEC5): granule search is not cached in Supabase, so offline it says so plainly. Read-only
 *  discovery: it never triggers a scan. Loading/empty/error states follow the honesty rules. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useEngineUrl } from "@/lib/engineUrl";
import { retrievalHref, saveQueryContext, toDateOnly } from "@/lib/queryContext";
import type { GranuleSearchResponse } from "@/lib/types";
import GranuleTimeline, { type TimelineSelection } from "./GranuleTimeline";
import GranuleDetail from "./GranuleDetail";

export interface DataPanelProps {
  tileId: string | null;
  /** the engine's bbox for the pinned tile (west, south, east, north) — the area this tab searches */
  tileBbox?: [number, number, number, number] | null;
  lon: number | null;
  lat: number | null;
  engineOnline: boolean;
  credentials: "present" | "absent" | undefined;
  granuleSearchStatus: "enabled" | "disabled" | undefined;
  granuleSearchReason: string | null | undefined;
  onFootprintOnMap: (ring: [number, number][] | null, fit?: boolean) => void;
}

export default function DataPanel(props: DataPanelProps) {
  const engineUrl = useEngineUrl();
  const [data, setData] = useState<GranuleSearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selection, setSelection] = useState<TimelineSelection | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [query, setQuery] = useState<{ from?: string; to?: string }>({});
  const querySig = useRef<string>("");

  const tileKey = props.tileId ? `${props.lon},${props.lat}` : null;

  // initial load per tile (default window: the engine's own lookback choice)
  useEffect(() => {
    if (!tileKey || props.lon === null || props.lat === null) return;
    if (!props.engineOnline) return;
    querySig.current = "";
    setQuery({});
    setSelection(null);
    setData(null);
    setError(null);
    setLoading(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    api
      .granules(props.lon, props.lat, {}, ctrl.signal)
      .then((r) => setData(r))
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [tileKey, props.engineOnline]); // eslint-disable-line react-hooks/exhaustive-deps

  // the timeline's visible range updates the query (debounced, stale requests aborted)
  useEffect(() => {
    if (!data || props.lon === null || props.lat === null) return;
    const sig = `${query.from ?? ""}|${query.to ?? ""}`;
    if (sig === querySig.current) return;
    const timer = setTimeout(() => {
      querySig.current = sig;
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setLoading(true);
      api
        .granules(props.lon as number, props.lat as number, { from: query.from, to: query.to }, ctrl.signal)
        .then((r) => {
          if (!ctrl.signal.aborted) setData(r);
        })
        .catch((e) => {
          if (!ctrl.signal.aborted) setError(e instanceof Error ? e.message : String(e));
        })
        .finally(() => {
          if (!ctrl.signal.aborted) setLoading(false);
        });
    }, 600);
    return () => clearTimeout(timer);
  }, [query, data, props.lon, props.lat]);

  // ---- the search area + window, made visible and editable --------------------------------------
  // The per-tile search is fixed by the ENGINE to the pin's MGRS tile (config/tiling.yaml
  // cell_size_m) over its default lookback window (config/common.yaml window.lookback_days). That
  // was invisible before. These fields show it, let the user change it, and hand the (possibly
  // edited) box to Data retrieval, which is the screen that searches arbitrary boxes.
  const [area, setArea] = useState({ w: "", s: "", e: "", n: "", from: "", to: "" });
  const bboxSig = props.tileBbox ? props.tileBbox.join(",") : "";
  useEffect(() => {
    if (!props.tileBbox) return;
    const [w, s, e, n] = props.tileBbox;
    const r = (v: number) => String(Number(v.toFixed(4)));
    setArea((a) => ({ ...a, w: r(w), s: r(s), e: r(e), n: r(n) }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bboxSig]);

  // the window the engine actually used (default lookback, or the timeline's visible range)
  const winFrom = toDateOnly(data?.window.t0);
  const winTo = toDateOnly(data?.window.t1);
  useEffect(() => {
    if (!winFrom || !winTo) return;
    setArea((a) => ({ ...a, from: winFrom, to: winTo }));
    saveQueryContext({ from: winFrom, to: winTo });   // Background shows the same window
  }, [winFrom, winTo]);

  const areaCheck = useMemo(() => {
    const [w, s, e, n] = [area.w, area.s, area.e, area.n].map((v) => (v.trim() === "" ? NaN : Number(v)));
    if (![w, s, e, n].every(Number.isFinite)) return { ok: false as const, msg: "enter all four edges (decimal degrees)" };
    if (w < -180 || e > 180 || s < -90 || n > 90) return { ok: false as const, msg: "edges outside the valid range" };
    if (w > e) return { ok: false as const, msg: "west is east of east (antimeridian) — split the box at ±180" };
    if (s >= n) return { ok: false as const, msg: "south must be below north" };
    if (area.from && area.to && area.from >= area.to) return { ok: false as const, msg: "From must be before To" };
    return { ok: true as const, bbox: [w, s, e, n] as [number, number, number, number] };
  }, [area]);

  const retrievalLink = areaCheck.ok
    ? retrievalHref({
        bbox: areaCheck.bbox, tile: props.tileId ?? undefined, level: "GUNW,GCOV",
        from: area.from || undefined, to: area.to || undefined,
      })
    : null;

  if (!props.engineOnline) {
    return (
      <div className="p-3 text-xs">
        <p role="status" className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] px-3 py-2 text-[#7a6438]">
          the engine is offline — granule availability needs it (this tab is live-engine-only and
          is never cached, so it will not show stale data).
        </p>
        <p className="mono mt-2 text-[10px] text-[#8a837b]">engine: {engineUrl}</p>
      </div>
    );
  }
  if (props.granuleSearchStatus === "disabled") {
    return (
      <div className="p-3 text-xs">
        <p role="status" className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] px-3 py-2 text-[#7a6438]">
          {props.granuleSearchReason ?? "granule search is disabled on this engine"}
        </p>
      </div>
    );
  }
  if (!props.tileId) {
    return (
      <div className="p-3 text-xs text-[#8a837b]">
        pick a place on the globe (or a cached pin) to see how much SAR data exists there — every
        acquisition, grouped by orbit track and direction, on a real time-scaled timeline.
      </div>
    );
  }
  return (
    <div className="space-y-2 p-3">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold">Data — granule availability · tile {props.tileId}</h3>
      </div>
      <p className="text-[11px] text-[#8a837b]">
        every acquisition the engine can see, grouped exactly the way it groups them (stack =
        direction + track). read-only: this tab never fetches or processes data — Scan does that.
      </p>
      <fieldset className="rounded-lg border border-[#e7e2da] bg-white px-3 py-2">
        <legend className="px-1 text-[11px] font-medium text-[#4a443c]">Search area &amp; window</legend>
        <p className="text-[10px] text-[#8a837b]">
          this tab searches the pinned tile&apos;s box (the engine fixes it to the tile around your pin)
          over the engine&apos;s default lookback window. Edit the box or dates and open it in Data
          retrieval to search any area.
        </p>
        <div className="mt-1.5 grid grid-cols-4 gap-1.5">
          {([["w", "West"], ["s", "South"], ["e", "East"], ["n", "North"]] as const).map(([k, label]) => (
            <label key={k} className="text-[10px] text-[#8a837b]">
              {label}
              <input suppressHydrationWarning
                inputMode="decimal"
                value={area[k]}
                onChange={(ev) => setArea((a) => ({ ...a, [k]: ev.target.value }))}
                className="mono mt-0.5 w-full rounded border border-[#e7e2da] px-1.5 py-1 text-[11px] text-[#1c1a17]"
              />
            </label>
          ))}
        </div>
        <div className="mt-1.5 grid grid-cols-2 gap-1.5">
          {([["from", "From (UTC)"], ["to", "To (UTC)"]] as const).map(([k, label]) => (
            <label key={k} className="text-[10px] text-[#8a837b]">
              {label}
              <input suppressHydrationWarning
                type="date"
                value={area[k]}
                onChange={(ev) => setArea((a) => ({ ...a, [k]: ev.target.value }))}
                className="mono mt-0.5 w-full rounded border border-[#e7e2da] px-1.5 py-1 text-[11px] text-[#1c1a17]"
              />
            </label>
          ))}
        </div>
        <div className="mt-2 flex items-center gap-2">
          {retrievalLink ? (
            <Link
              href={retrievalLink}
              onClick={() => areaCheck.ok && saveQueryContext({
                bbox: areaCheck.bbox, level: "GUNW,GCOV",
                from: area.from || undefined, to: area.to || undefined,
              })}
              className="rounded-md bg-[#2c5f58] px-2.5 py-1 text-[11px] font-medium text-white hover:bg-[#37736a]"
            >
              Search this box in Data retrieval →
            </Link>
          ) : (
            <span className="rounded-md bg-[#e7e2da] px-2.5 py-1 text-[11px] font-medium text-[#8a837b]">
              Search this box in Data retrieval →
            </span>
          )}
          {props.tileBbox && (
            <button
              type="button"
              onClick={() => {
                const [w, s, e, n] = props.tileBbox as [number, number, number, number];
                const r = (v: number) => String(Number(v.toFixed(4)));
                setArea((a) => ({ ...a, w: r(w), s: r(s), e: r(e), n: r(n) }));
              }}
              className="text-[11px] text-[#4a443c] underline"
            >
              reset to tile
            </button>
          )}
        </div>
        {!areaCheck.ok && <p role="status" className="mt-1 text-[10px] text-[#9e3b2c]">{areaCheck.msg}</p>}
      </fieldset>
      {loading && !data && (
        <div className="space-y-2" aria-busy="true">
          <div className="skeleton h-24 rounded-lg" />
          <div className="skeleton h-16 rounded-lg" />
        </div>
      )}
      {error && (
        <p role="alert" className="mono rounded-lg border border-[#9e3b2c] bg-[#fbf3ef] px-3 py-2 text-[11px] text-[#9e3b2c]">
          {error}
        </p>
      )}
      {data && (
        <>
          {(data.search_degraded || data.search_incomplete) && (
            <p role="status" className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] px-3 py-2 text-[11px] text-[#7a6438]">
              {data.search_incomplete ? "ASF/CMR returned a short listing even after a retry: some granules may be missing (see flags below)" : "degraded search: results may be incomplete (see flags below)"}
            </p>
          )}
          <GranuleTimeline
            data={data}
            selection={selection}
            onSelect={(sel) => {
              setSelection(sel);
              if (!sel) {
                props.onFootprintOnMap(null);
                return;
              }
              const stack = data.stacks[sel.stackKey];
              if (!stack) return;
              const ring = sel.kind === "gunw" ? stack.gunw[sel.index]?.footprint : stack.gcov[sel.index]?.footprint;
              props.onFootprintOnMap(sel.kind === "gunw" ? ring ?? null : null);
            }}
            onWindowChange={(from, to) => setQuery({ from, to })}
          />
          {Object.keys(data.search_errors).length > 0 && (
            <div className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] px-3 py-2 text-[11px] text-[#7a6438]">
              {Object.entries(data.search_errors).map(([prod, reason]) => (
                <p key={prod} className="mono">{prod}: {reason}</p>
              ))}
            </div>
          )}
          {data.flags.length > 0 && (
            <ul className="space-y-0.5 text-[10px] text-[#8a837b]">
              {data.flags.map((f, i) => (
                <li key={i} className="mono">{f.message}</li>
              ))}
            </ul>
          )}
          <GranuleDetail
            data={data}
            selection={selection}
            onFootprintOnMap={props.onFootprintOnMap}
          />
        </>
      )}
      {!loading && !data && !error && (
        <p role="status" className="text-[11px] text-[#8a837b]">loading granule availability…</p>
      )}
    </div>
  );
}
