"use client";

/** DataPanel (§7.5) — the fourth tab: granule availability for the selected tile. LIVE-ENGINE-ONLY
 *  (DEC5): granule search is not cached in Supabase, so offline it says so plainly. Read-only
 *  discovery: it never triggers a scan. Loading/empty/error states follow the honesty rules. */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ENGINE_API } from "@/lib/api";
import type { GranuleSearchResponse } from "@/lib/types";
import GranuleTimeline, { type TimelineSelection } from "./GranuleTimeline";
import GranuleDetail from "./GranuleDetail";

export interface DataPanelProps {
  tileId: string | null;
  lon: number | null;
  lat: number | null;
  engineOnline: boolean;
  credentials: "present" | "absent" | undefined;
  granuleSearchStatus: "enabled" | "disabled" | undefined;
  granuleSearchReason: string | null | undefined;
  onFootprintOnMap: (ring: [number, number][] | null) => void;
}

export default function DataPanel(props: DataPanelProps) {
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

  if (!props.engineOnline) {
    return (
      <div className="p-3 text-xs">
        <p role="status" className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] px-3 py-2 text-[#7a6438]">
          the engine is offline — granule availability needs it (this tab is live-engine-only and
          is never cached, so it will not show stale data).
        </p>
        <p className="mono mt-2 text-[10px] text-[#8a837b]">engine: {ENGINE_API}</p>
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
          {data.search_degraded && (
            <p role="status" className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] px-3 py-2 text-[11px] text-[#7a6438]">
              degraded search: results may be incomplete (see flags below)
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
