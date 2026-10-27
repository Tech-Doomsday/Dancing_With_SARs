"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { EventItem } from "@/lib/types";

/** Display-only ComCat context for a map point (via GET /api/events → the R1 adapter).
 *  The PIPELINE derives its own events from the pair window — this panel is context,
 *  and it says so. Upstream failures show the engine's own error, never a fake list. */
export default function EventsPanel({ lon, lat }: { lon: number; lat: number }) {
  const [events, setEvents] = useState<EventItem[] | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .events(lon, lat, 30)
      .then((r) => {
        if (!alive) return;
        setEvents(r.events);
        setSource(r.source);
      })
      .catch((e) => alive && setErr(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [lon, lat]);

  return (
    <section className="rounded-lg border border-[#e7e2da] bg-white p-4">
      <h3 className="text-sm font-semibold">Catalogue context (USGS ComCat)</h3>
      <p className="mt-1 text-[11px] text-[#8a837b]">
        Display-only context for this tile centre, M ≥ 4 over the last 30 days. The pipeline
        derives its own event set from the acquisition window — this list never feeds a verdict.
      </p>
      {err && (
        <p role="alert" className="mono mt-2 rounded border border-[#9e3b2c] bg-[#fbf3ef] px-3 py-2 text-xs text-[#9e3b2c]">
          {err}
        </p>
      )}
      {events !== null && events.length === 0 && !err && (
        <p className="mt-2 text-xs text-[#8a837b]">No catalogue events in this window.</p>
      )}
      {events && events.length > 0 && (
        <ul className="mt-2 divide-y divide-[#f0ece4]">
          {events.slice(0, 8).map((e) => (
            <li key={e.id} className="flex flex-wrap items-baseline gap-2 py-1.5 text-xs">
              <span className="mono font-semibold text-[#2c5f58]">
                M{e.magnitude !== null ? e.magnitude.toFixed(1) : "?"}
              </span>
              <span className="min-w-0 flex-1 truncate text-[#4a443c]">{e.place || e.id}</span>
              <span className="mono text-[10px] text-[#8a837b]">{e.time.slice(0, 10)}</span>
              <span className="mono text-[10px] text-[#8a837b]">
                {e.depth_km !== null ? `${e.depth_km.toFixed(0)} km` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
      {source && <p className="mono mt-2 text-[10px] text-[#b08d57]">source: {source}</p>}
    </section>
  );
}
