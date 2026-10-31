"use client";

/** GranuleDetail (§7.5 item 2) — for the selected pair/epoch, show ONLY the fields the engine
 *  really computes (§5.5 item 4's "real-data" column). Fields the engine does not track
 *  (polarization, CRID, bandwidth, cycle…) are simply ABSENT — never blank placeholder rows.
 *  "flipped" is explained in plain language. The footprint is labelled "footprint": it is ASF's
 *  imaged outer ring, NOT the satellite's trajectory. */
import FootprintInset from "./FootprintInset";
import type { TimelineSelection } from "./GranuleTimeline";
import { selectionFromPath } from "./GranuleTimeline";
import { directionLabel } from "@/lib/status";
import type { GranuleSearchResponse } from "@/lib/types";

export interface GranuleDetailProps {
  data: GranuleSearchResponse;
  selection: TimelineSelection | null;
  onFootprintOnMap: (ring: [number, number][] | null, fit?: boolean) => void;
}

export default function GranuleDetail(props: GranuleDetailProps) {
  const sel = selectionFromPath(props.data, props.selection);
  if (!sel) {
    return (
      <p className="rounded-lg border border-[#e7e2da] bg-white p-3 text-[11px] text-[#8a837b]">
        select a bar or marker in the timeline to see that acquisition&apos;s real metadata — and
        (for GUNW pairs) to draw its footprint on the globe
      </p>
    );
  }
  if (sel.kind === "gunw") {
    const p = sel.pair;
    return (
      <div className="rounded-lg border border-[#e7e2da] bg-white p-3 text-xs">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="font-semibold text-[#1c1a17]">
              GUNW pair · stack {sel.stackKey} ({directionLabel(sel.stack.direction)})
            </p>
            <dl className="mono mt-1 space-y-0.5 text-[11px] text-[#4a443c]">
              <div>reference: {p.ref_time ? `${p.ref_time} UTC` : "—"}</div>
              <div>secondary: {p.sec_time ? `${p.sec_time} UTC` : "—"}</div>
              <div>baseline: {p.dt_days != null ? `${Math.round(p.dt_days * 10) / 10} d` : "—"}</div>
              {p.frame != null && <div>frame: {p.frame}</div>}
              <div>
                resolved: {p.resolved ? (p.flipped ? "yes — dates recovered in reversed order; corrected" : "yes") : "no — the id carries no usable date pair"}
              </div>
            </dl>
          </div>
          <FootprintInset ring={p.footprint} label="footprint (first granule)" />
        </div>
        <p className="mono mt-2 break-all text-[10px] text-[#8a837b]">{p.granule_ids.join(", ")}</p>
        <p className="mt-1 text-[10px] text-[#8a837b]">
          the footprint outline is the first granule&apos;s imaged outer ring — not the satellite&apos;s
          orbital path
        </p>
        {p.footprint && (
          <button
            type="button"
            onClick={() => props.onFootprintOnMap(p.footprint, true)}
            className="mt-2 rounded-md border border-[#2c5f58] px-2 py-1 text-[11px] text-[#2c5f58] hover:bg-[#eff5f3]"
          >
            draw this pair&apos;s footprint on the globe
          </button>
        )}
      </div>
    );
  }
  const e = sel.epoch;
  return (
    <div className="rounded-lg border border-[#e7e2da] bg-white p-3 text-xs">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-[#1c1a17]">
            GCOV acquisition · stack {sel.stackKey} ({directionLabel(sel.stack.direction)})
          </p>
          <dl className="mono mt-1 space-y-0.5 text-[11px] text-[#4a443c]">
            <div>time: {e.time ? `${e.time} UTC` : "—"}</div>
            {e.frame != null && <div>frame: {e.frame}</div>}
          </dl>
        </div>
        <FootprintInset ring={e.footprint} label="footprint (first granule)" stroke="#b08d57" />
      </div>
      <p className="mono mt-2 break-all text-[10px] text-[#8a837b]">{e.granule_ids.join(", ")}</p>
      <p className="mt-1 text-[10px] text-[#8a837b]">
        one backscatter pass (near-instantaneous, not a span); the outline is the first
        granule&apos;s imaged outer ring
      </p>
    </div>
  );
}
