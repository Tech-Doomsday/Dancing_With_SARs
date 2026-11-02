"use client";

/** Legend (bottom-left, shown when an overlay is active) — built from lib/raster.ts::legendStops,
 *  the ONE colour implementation shared with the overlay canvas. Engine strings verbatim. */
import type { LayerPayload } from "@/lib/types";
import { legendStops } from "@/lib/raster";

/** What the map symbols mean (the pins used to appear with no explanation). */
export function MapKey() {
  return (
    <ul aria-label="map key" className="pointer-events-auto rounded-lg border border-[#262c36] bg-[#0e1116]/90 px-2 py-1 text-[10px] text-[#c9d1d9]">
      <li><span aria-hidden className="mr-1 inline-block h-2 w-2 rounded-full bg-[#ffffff] ring-1 ring-[#8a837b]" />your pin (where you clicked or searched)</li>
      <li><span aria-hidden className="mr-1 inline-block h-2 w-2 rounded-full bg-[#9e3b2c] ring-2 ring-white" />a tile that already has a verdict (colour = verdict tier)</li>
      <li><span aria-hidden className="mr-1 inline-block h-2 w-2 border border-[#ffd166]" />granule footprint · <span className="text-[#e07856]">dashed red</span> = detection outline</li>
    </ul>
  );
}

export default function Legend({ layer, opacity }: { layer: LayerPayload | null; opacity: number }) {
  if (!layer) return null;
  const legend = legendStops(layer);
  return (
    <div className="pointer-events-auto max-w-[300px] rounded-xl border border-[#e7e2da] bg-[#faf9f6]/95 p-3 shadow-lg backdrop-blur">
      <p className="text-xs font-semibold text-[#1c1a17]">
        {layer.name} <span className="font-normal text-[#8a837b]">· opacity {Math.round(opacity * 100)}%</span>
      </p>
      {legend.kind === "continuous" ? (
        <div className="mt-1.5">
          <div
            className="h-2.5 w-full rounded-sm border border-[#e7e2da]"
            style={{
              background: `linear-gradient(to right, ${legend.stops.map((s) => s.color).join(", ")})`,
            }}
            aria-hidden
          />
          <div className="mono mt-1 flex justify-between text-[10px] text-[#4a443c]">
            <span>{legend.stops[0].label}</span>
            <span>{legend.stops[legend.stops.length - 1].label}</span>
          </div>
        </div>
      ) : (
        <ul className="mt-1.5 space-y-1">
          {legend.stops.map((s) => (
            <li key={s.label} className="flex items-center gap-1.5 text-[11px] text-[#4a443c]">
              <span className="inline-block h-2.5 w-2.5 rounded-sm border border-[#e7e2da]" style={{ background: s.color }} aria-hidden />
              {s.label}
            </li>
          ))}
        </ul>
      )}
      {layer.units && <p className="mt-1 text-[10px] text-[#4a443c]">{layer.units}</p>}
      <p className="mt-1 text-[10px] text-[#8a837b]">raster shown on its geographic bounding box</p>
      {layer.note && <p className="mt-0.5 text-[10px] text-[#8a837b]">{layer.note}</p>}
    </div>
  );
}
