"use client";

/** ExplorerDetail (§7.8) — the selected granule: full file name (monospace, wrapped, copy),
 *  a footprint mini-map (MapLibre, FLAT projection, fitBounds on the search box) showing ALL
 *  footprints of the selected stack with the selected one highlighted and the box outlined
 *  amber, the flight-direction chip (from the engine's direction — headings are never computed
 *  client-side), and the key/value table. Unknown = "—"; undecoded tokens are shown raw with an
 *  "undecoded" tag. "Show on globe" and "Scan this tile" are links — never an automatic scan. */
import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Check, Copy, X } from "lucide-react";
import { api } from "@/lib/api";
import type { ExplorerGranule, ExplorerStack } from "@/lib/types";
import { directionLabel } from "@/lib/status";

/** maplibre loads client-side only; the Explorer route already carries the chunk */
const FootprintMap = dynamic(() => import("./FootprintMiniMap"), {
  ssr: false,
  loading: () => <p className="p-3 text-[11px] text-[#8b949e]">footprint map…</p>,
});

function Row({ k, v, mono = true, tag }: { k: string; v: React.ReactNode; mono?: boolean; tag?: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-[#1c222b] py-1">
      <dt className="shrink-0 text-[#8b949e]">{k}</dt>
      <dd className={`text-right text-[#e6eaee] ${mono ? "font-mono text-[11px]" : "text-xs"} break-all`}>
        {v ?? "—"}
        {tag && <span className="ml-1 rounded border border-[#4a3a1f] px-1 py-px text-[9px] uppercase text-[#e6c977]">{tag}</span>}
      </dd>
    </div>
  );
}

export default function ExplorerDetail({ granule, stack, stackGranules, bbox, engineOnline, onClose, onShowOnGlobe, onScanTile }: {
  granule: ExplorerGranule | null;
  stack: ExplorerStack | null;
  stackGranules: ExplorerGranule[];
  bbox: [number, number, number, number] | null;
  engineOnline: boolean;
  onClose: () => void;
  onShowOnGlobe: (lat: number, lon: number) => void;
  onScanTile: (tileId: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const [tileId, setTileId] = useState<string | null>(null);
  const [tileError, setTileError] = useState<string | null>(null);

  // resolve the containing MGRS tile through the ENGINE (the client never inverts ids)
  useEffect(() => {
    if (!granule) return;
    const lon = granule.footprint
      ? granule.footprint.reduce((acc, p) => acc + p[0], 0) / granule.footprint.length
      : null;
    const lat = granule.footprint
      ? granule.footprint.reduce((acc, p) => acc + p[1], 0) / granule.footprint.length
      : null;
    if (lon === null || lat === null || !engineOnline) {
      setTileId(null);
      return;
    }
    const ctrl = new AbortController();
    setTileError(null);
    api.tileAt(lon, lat)
      .then((r) => setTileId(r.tile.id))
      .catch((e) => (ctrl.signal.aborted ? undefined : setTileError(e instanceof Error ? e.message : String(e))));
    return () => ctrl.abort();
  }, [granule, engineOnline]);

  const copyName = () => {
    if (!granule) return;
    void navigator.clipboard?.writeText(granule.id).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  };

  const stackColor = stack?.direction === "D" ? "#b39ce8" : "#3fb6c9";
  const dirChip = useMemo(() => {
    if (!granule) return null;
    const label = directionLabel(granule.direction);
    const cls = granule.direction === "D"
      ? "border-[#b39ce8]/50 bg-[#241f33] text-[#cdbdf0]"
      : granule.direction === "A"
        ? "border-[#3fb6c9]/50 bg-[#12262c] text-[#7fd8e6]"
        : "border-[#333b47] bg-[#161b22] text-[#8b949e]";
    return <span className={`rounded border px-1.5 py-0.5 text-[10px] ${cls}`}>{label}</span>;
  }, [granule]);

  if (!granule) {
    return (
      <div className="p-4 text-[11px] leading-relaxed text-[#8b949e]">
        select a bar in the timeline (or a row in List view) to see the granule&apos;s real
        metadata: the full file name, its track and frame, reference and secondary acquisitions,
        baseline, polarization, bandwidth, collection, CRID and orbit accuracy — every field the
        engine actually knows, with undecoded tokens shown raw.
      </div>
    );
  }

  const center = granule.footprint && granule.footprint.length > 0
    ? {
        lon: granule.footprint.reduce((acc, p) => acc + p[0], 0) / granule.footprint.length,
        lat: granule.footprint.reduce((acc, p) => acc + p[1], 0) / granule.footprint.length,
      }
    : null;

  return (
    <div className="p-3 text-xs">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${stackColor === "#3fb6c9" ? "border-[#3fb6c9]/50 bg-[#12262c] text-[#7fd8e6]" : "border-[#b39ce8]/50 bg-[#241f33] text-[#cdbdf0]"}`}>
            {granule.stack}
          </span>
          {dirChip}
          <span className="rounded border border-[#333b47] px-1.5 py-0.5 font-mono text-[10px] text-[#aab3bd]">
            {granule.product ?? "—"}
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="clear selection"
          className="rounded p-1 text-[#8b949e] hover:bg-[#1c222b] hover:text-[#e6eaee]"
        >
          <X size={13} aria-hidden />
        </button>
      </div>

      {/* full file name + copy */}
      <div className="rounded-md border border-[#262c36] bg-[#0e1116] p-2">
        <p className="break-all font-mono text-[10.5px] leading-relaxed text-[#cfd6dc]">{granule.id}</p>
        <button
          type="button"
          onClick={copyName}
          className="mt-1.5 flex items-center gap-1 rounded border border-[#333b47] px-2 py-1 text-[10px] text-[#aab3bd] hover:border-[#3fb6c9] hover:text-[#7fd8e6]"
        >
          {copied ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />} {copied ? "copied" : "copy file name"}
        </button>
      </div>

      {/* footprint mini-map: the whole stack's footprints, the selection highlighted, box amber */}
      <div className="mt-2 overflow-hidden rounded-md border border-[#262c36]">
        <FootprintMap
          bbox={bbox}
          stackRings={stackGranules.map((g) => ({ id: g.id, ring: g.footprint }))}
          selectedId={granule.id}
        />
      </div>

      {/* key/value table */}
      <dl className="mt-2">
        <Row k="Level" v={granule.product} />
        <Row k="Direction" v={directionLabel(granule.direction)} mono={false} />
        <Row k="Track / frame" v={granule.track != null && granule.frame != null ? `${granule.track} / ${granule.frame}` : granule.track ?? granule.frame ?? null} />
        <Row
          k="Reference"
          v={granule.ref_time ? `${granule.ref_time} UTC` : granule.start ? `${granule.start} UTC` : null}
          tag={granule.cycle_ref != null ? `cycle ${granule.cycle_ref}` : undefined}
        />
        <Row
          k="Secondary"
          v={granule.sec_time ? `${granule.sec_time} UTC` : null}
          tag={granule.cycle_sec != null ? `cycle ${granule.cycle_sec}` : undefined}
        />
        <Row k="Baseline" v={granule.baseline_days != null ? `${granule.baseline_days} d` : null} />
        <Row k="Polarization" v={granule.polarization} />
        <Row k="Range bandwidth" v={granule.range_bandwidth_mhz != null ? `${granule.range_bandwidth_mhz} MHz` : null} />
        <Row k="Collection" v={granule.collection} />
        <Row k="Release (CRID)" v={granule.crid} />
        <Row
          k="Orbit accuracy"
          v={
            granule.orbit_accuracy.asf || granule.orbit_accuracy.filename
              ? `ASF: ${granule.orbit_accuracy.asf ?? "—"}; filename: ${granule.orbit_accuracy.filename ?? "—"}`
              : null
          }
          tag={granule.flags.some((f) => f.includes("orbit token")) ? "undecoded" : undefined}
        />
        <Row k="Size" v={granule.size_bytes != null ? `${(granule.size_bytes / 1e9).toFixed(2)} GB` : null} />
        <Row k="Version" v={granule.version} />
        {granule.flags.length > 0 && (
          <Row
            k="Flags"
            mono={false}
            v={(
              <ul className="space-y-0.5 text-left">
                {granule.flags.map((f) => (
                  <li key={f} className="text-[10px] text-[#d9c98a]">{f}</li>
                ))}
              </ul>
            )}
          />
        )}
      </dl>

      {/* buttons: viewer + bridge only (DEC14) */}
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={!center}
          onClick={() => center && onShowOnGlobe(center.lat, center.lon)}
          className="rounded-md border border-[#3fb6c9] px-2.5 py-1.5 text-[11px] text-[#7fd8e6] hover:bg-[#12262c] disabled:cursor-not-allowed disabled:opacity-40"
        >
          Show on globe
        </button>
        <button
          type="button"
          disabled={!tileId || !engineOnline}
          title={!engineOnline ? "the engine is offline — the tile cannot be resolved" : !tileId ? (tileError ?? "resolving the containing tile…") : `open tile ${tileId} on the globe (nothing is scanned automatically)`}
          onClick={() => tileId && onScanTile(tileId)}
          className="rounded-md border border-[#2c5f58] bg-[#12241f] px-2.5 py-1.5 text-[11px] text-[#8fd0b8] hover:bg-[#17312a] disabled:cursor-not-allowed disabled:opacity-40"
        >
          {tileId ? `Scan this tile (${tileId})` : "Scan this tile"}
        </button>
      </div>
      <p className="mt-1.5 text-[10px] leading-relaxed text-[#5a636e]">
        the footprint outline is the granule&apos;s imaged outer ring (ASF), not the satellite&apos;s
        trajectory. Clipping or prefetching from the Explorer is out of scope.
      </p>
    </div>
  );
}
