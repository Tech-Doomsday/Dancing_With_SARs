"use client";

/** FootprintInset — the footprint-outline renderer, written ONCE and used by the Data tab detail
 *  panel AND the tile footprint display (§7.5 item 6). Small SVG outline of an outer ring.
 *  Labelled "footprint": the engine tracks the imaged footprint (ASF's outer ring), NOT the
 *  satellite's orbital path — the UI never claims more precision than exists. */
import { TOKENS } from "@/lib/status";

export default function FootprintInset({
  ring,
  size = 88,
  label = "footprint",
  stroke = TOKENS.teal,
}: {
  ring: [number, number][] | null;
  size?: number;
  label?: string;
  stroke?: string;
}) {
  let path: string | null = null;
  if (ring && ring.length >= 3) {
    const lons = ring.map((p) => p[0]);
    const lats = ring.map((p) => p[1]);
    const minLon = Math.min(...lons);
    const maxLon = Math.max(...lons);
    const minLat = Math.min(...lats);
    const maxLat = Math.max(...lats);
    const spanLon = maxLon - minLon || 1e-9;
    const spanLat = maxLat - minLat || 1e-9;
    const pad = 6;
    const w = size - pad * 2;
    const h = size - pad * 2;
    const pts = ring.map(([lon, lat]) => {
      const x = pad + ((lon - minLon) / spanLon) * w;
      const y = pad + (1 - (lat - minLat) / spanLat) * h; // row 0 = north
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    path = pts.join(" ");
  }
  return (
    <figure className="inline-flex flex-col items-center">
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={ring ? `${label} outline` : "no footprint available"}
        className="rounded-md border border-[#e7e2da] bg-[#f7f4ee]"
      >
        <rect x="0" y="0" width={size} height={size} fill="#f7f4ee" />
        {path ? (
          <polygon points={path} fill="none" stroke={stroke} strokeWidth="1.5" />
        ) : (
          <text x="50%" y="50%" textAnchor="middle" fontSize="9" fill="#8a837b">
            none
          </text>
        )}
      </svg>
      <figcaption className="mt-0.5 text-[10px] text-[#8a837b]">{label}</figcaption>
    </figure>
  );
}
