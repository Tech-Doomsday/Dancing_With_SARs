"use client";

/** Legend (bottom-left, shown when an overlay is active) — built from lib/raster.ts::legendStops,
 *  the ONE colour implementation shared with the overlay canvas. Engine strings verbatim. */
import type { LayerPayload } from "@/lib/types";
import { legendStops } from "@/lib/raster";

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
