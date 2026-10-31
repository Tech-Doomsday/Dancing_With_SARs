"use client";

/** SarOverlayControl — draws one SAR layer of the selected granule on the footprint mini-map, georeferenced.
 *  The engine decides everything: which layers are previewable, the colour map, the dB rule, the image
 *  corners (corners_lonlat) and the unit. This component fetches the PNG and hands {url, corners, opacity}
 *  to the map; if the engine returns no corners the image is NOT drawn and the reason is shown. */
import { useEffect, useMemo, useRef, useState } from "react";
import { bg } from "@/lib/bg";
import { GateError } from "@/lib/bgApi";
import { dash } from "@/lib/explainCache";
import type { GranuleDetailResponse, PreviewMeta } from "@/lib/types";
import ExplainLine from "@/components/background/ExplainLine";
import type { SarOverlay } from "./FootprintMiniMap";

type InvLayer = NonNullable<GranuleDetailResponse["inventory"]>["grids"][number]["layers"][number];

export default function SarOverlayControl({ granuleId, bbox, inventory, onOverlay }: {
  granuleId: string;
  bbox: [number, number, number, number] | null;
  inventory: GranuleDetailResponse["inventory"] | null | undefined;
  onOverlay: (o: SarOverlay | null) => void;
}) {
  const layers: InvLayer[] = useMemo(
    () => (inventory?.grids ?? []).flatMap((g) => g.layers).filter((l) => l.previewable),
    [inventory]);
  const [layerKey, setLayerKey] = useState("");
  const [scale, setScale] = useState<"auto" | "linear" | "db">("auto");
  const [opacity, setOpacity] = useState(0.8);
  const [on, setOn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [meta, setMeta] = useState<PreviewMeta | null>(null);
  const urlRef = useRef<string | null>(null);
  const cornersRef = useRef<SarOverlay["corners"] | null>(null);

  useEffect(() => { // a different granule resets the choice
    setLayerKey(layers[0]?.key ?? ""); setOn(false); setMeta(null); setMessage(null);
  }, [granuleId, layers]);

  const clear = () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = null; cornersRef.current = null;
    onOverlay(null);
  };
  useEffect(() => clear, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!on || !layerKey || !bbox) { clear(); return; }
    const ctrl = new AbortController();
    setBusy(true); setMessage(null);
    bg.previewBlob({ id: granuleId, layer: layerKey, west: bbox[0], south: bbox[1], east: bbox[2], north: bbox[3],
                     maxPx: 512, scale }, ctrl.signal)
      .then(({ blob, meta: m }) => {
        if (ctrl.signal.aborted) return;
        setMeta(m);
        if (!blob || !m.corners_lonlat) {
          clear();
          setMessage(m.corners_note ?? "the engine returned no image corners for this layer, so it is not drawn");
          return;
        }
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        urlRef.current = URL.createObjectURL(blob);
        cornersRef.current = m.corners_lonlat;
        onOverlay({ url: urlRef.current, corners: m.corners_lonlat, opacity });
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        clear();
        setMessage(err instanceof GateError ? "the Background token is needed (open the Background tab and enter it)"
                                            : err instanceof Error ? err.message : String(err));
      })
      .finally(() => !ctrl.signal.aborted && setBusy(false));
    return () => ctrl.abort();
    // opacity is applied below without refetching
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [on, layerKey, scale, granuleId, bbox?.join(",")]);

  useEffect(() => { // opacity only: reuse the fetched image
    if (urlRef.current && cornersRef.current) onOverlay({ url: urlRef.current, corners: cornersRef.current, opacity });
  }, [opacity]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!layers.length) {
    return <p className="mt-2 text-[10px] text-[#5a636e]">no previewable layer in this granule&apos;s inventory — no SAR image to show.</p>;
  }
  const chosen = layers.find((l) => l.key === layerKey);
  return (
    <div className="mt-2 rounded-md border border-[#262c36] bg-[#12161c] p-2 text-[10px] text-[#cfd6dc]" data-testid="sar-overlay">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} /> show SAR image on map
        </label>
        <select aria-label="SAR layer" value={layerKey} onChange={(e) => setLayerKey(e.target.value)}
                className="max-w-[16rem] rounded border border-[#333b47] bg-[#0e1217] px-1 py-0.5 font-mono">
          {layers.map((l) => <option key={l.key} value={l.key}>{l.path.split("/").slice(-3).join("/")}</option>)}
        </select>
        <select aria-label="scale" value={scale} onChange={(e) => setScale(e.target.value as "auto" | "linear" | "db")}
                className="rounded border border-[#333b47] bg-[#0e1217] px-1 py-0.5">
          <option value="auto">auto</option><option value="linear">linear</option><option value="db">dB</option>
        </select>
        <label className="flex items-center gap-1">opacity
          <input type="range" min={0.1} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} />
        </label>
      </div>
      {busy && <p role="status" className="mt-1 text-[#8b949e]">drawing…</p>}
      {message && <p role="alert" className="mt-1 text-[#e8a087]">{message}</p>}
      {meta && !message && (
        <p className="mt-1 font-mono text-[#8b949e]" data-testid="sar-legend">
          {dash(meta.cmap)} · {dash(meta.scale)} · {dash(meta.lo)} … {dash(meta.hi)} {dash(meta.unit)} · no-data {dash(meta.nan_fraction != null ? `${Math.round(meta.nan_fraction * 100)}%` : null)}
        </p>
      )}
      <ExplainLine role={chosen?.role} whatItIs={chosen?.what_it_is} />
    </div>
  );
}
