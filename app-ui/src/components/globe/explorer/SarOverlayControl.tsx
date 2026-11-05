"use client";

/** SarOverlayControl — draws one SAR layer of the selected granule on the footprint mini-map, georeferenced.
 *  The engine decides everything: which layers are previewable, the colour map, the dB rule, the image
 *  corners (corners_lonlat) and the unit. The fetch / abort / object-URL rules live in useSarImage (shared
 *  with the Map page and Before/After); this component is the controls + legend. If the engine returns no
 *  corners the image is NOT drawn and the engine's reason is shown.
 *  Mount it with key={granuleId}: a new granule is a fresh control, so no request is ever made with the
 *  previous granule's layer key. */
import { useEffect, useMemo, useRef, useState } from "react";
import { previewLegend, qualityLegend } from "@/lib/format";
import type { GranuleDetailResponse } from "@/lib/types";
import ExplainLine from "@/components/background/ExplainLine";
import type { SarOverlay } from "./FootprintMiniMap";
import { useSarImage } from "./useSarImage";

type InvLayer = NonNullable<GranuleDetailResponse["inventory"]>["grids"][number]["layers"][number];

/** "Show on map" from the Layers tab: select this layer and turn the overlay on (nonce re-fires the same layer) */
export interface SarRequest { layerKey: string; nonce: number }

export default function SarOverlayControl({ granuleId, bbox, inventory, onOverlay, request = null, onGate }: {
  granuleId: string;
  bbox: [number, number, number, number] | null;
  inventory: GranuleDetailResponse["inventory"] | null | undefined;
  onOverlay: (o: SarOverlay | null) => void;
  request?: SarRequest | null;
  /** 401 / gate-503 from the engine: the host shows its token prompt */
  onGate?: (kind: "unauthorized" | "unconfigured") => void;
}) {
  const layers: InvLayer[] = useMemo(
    () => (inventory?.grids ?? []).flatMap((g) => g.layers).filter((l) => l.previewable),
    [inventory]);
  const [layerKey, setLayerKey] = useState(request?.layerKey ?? "");
  const [scale, setScale] = useState<"auto" | "linear" | "db">("auto");
  const [opacity, setOpacity] = useState(0.8);
  const [validate, setValidate] = useState(false);
  const [on, setOn] = useState(request != null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // the first previewable layer is the default once the inventory has arrived (unless one was asked for)
  const effectiveKey = layers.some((l) => l.key === layerKey) ? layerKey : (layers[0]?.key ?? "");
  const img = useSarImage({ enabled: on, granuleId, layerKey: effectiveKey, bbox, scale, validate });

  // host callbacks go through refs: no stale closure, and they never sit in an effect's dependency list
  const onOverlayRef = useRef(onOverlay);
  onOverlayRef.current = onOverlay;
  const onGateRef = useRef(onGate);
  onGateRef.current = onGate;

  useEffect(() => {
    if (img.url && img.corners) onOverlayRef.current({ url: img.url, corners: img.corners, opacity });
    else onOverlayRef.current(null);
  }, [img.url, img.corners, opacity]);
  useEffect(() => () => onOverlayRef.current(null), []);   // unmount: take the overlay off the map
  useEffect(() => { if (img.gate) onGateRef.current?.(img.gate); }, [img.gate]);

  useEffect(() => {                                          // "Show on map"
    if (!request) return;
    setLayerKey(request.layerKey);
    setOn(true);
    rootRef.current?.scrollIntoView({ block: "nearest" });
    rootRef.current?.querySelector<HTMLInputElement>("input[type=checkbox]")?.focus();
  }, [request?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!layers.length) {
    return <p className="mt-2 text-[10px] text-[#5a636e]">no previewable layer in this granule&apos;s inventory — no SAR image to show.</p>;
  }
  const chosen = layers.find((l) => l.key === effectiveKey);
  const meta = img.meta;
  return (
    <div ref={rootRef} className="mt-2 rounded-md border border-[#262c36] bg-[#12161c] p-2 text-[10px] text-[#cfd6dc]" data-testid="sar-overlay">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} /> show SAR image on map
        </label>
        <select aria-label="SAR layer" value={effectiveKey} onChange={(e) => setLayerKey(e.target.value)}
                className="max-w-[16rem] rounded border border-[#333b47] bg-[#0e1217] px-1 py-0.5 font-mono">
          {layers.map((l) => <option key={l.key} value={l.key}>{l.path.split("/").slice(-3).join("/")}</option>)}
        </select>
        <select aria-label="scale" value={scale} onChange={(e) => setScale(e.target.value as "auto" | "linear" | "db")}
                className="rounded border border-[#333b47] bg-[#0e1217] px-1 py-0.5">
          <option value="auto">auto</option><option value="linear">linear</option><option value="db">dB</option>
        </select>
        <label className="flex items-center gap-1" title="Fade pixels where the interferometric coherence is low, so unreliable matches do not look like data. Display only: the values are not changed.">
          <input type="checkbox" checked={validate} onChange={(e) => setValidate(e.target.checked)} data-testid="sar-validate" />
          validate with coherence
        </label>
        <label className="flex items-center gap-1">opacity
          <input type="range" min={0.1} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} />
        </label>
      </div>
      {img.busy && <p role="status" className="mt-1 text-[#8b949e]">drawing…</p>}
      {img.message && <p role="alert" className="mt-1 text-[#e8a087]">{img.message}</p>}
      {meta && !img.message && (
        <p className="mt-1 font-mono text-[#8b949e]" data-testid="sar-legend">
          {previewLegend(meta)}{qualityLegend(meta.quality) ? ` · ${qualityLegend(meta.quality)}` : ""}
        </p>
      )}
      {meta && !img.message && (meta.notes ?? []).map((n) => (
        <p key={n} role="note" className="mt-1 text-[#e6c977]" data-testid="sar-note">{n}</p>
      ))}
      <ExplainLine role={chosen?.role} whatItIs={chosen?.what_it_is} />
    </div>
  );
}
