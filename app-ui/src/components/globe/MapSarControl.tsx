"use client";

/** MapSarControl — shows ONE SAR layer of a selected granule on the Map page (globe / flat map), placed by the
 *  engine's corners_lonlat. Reuses useSarImage (fetch / abort / object-URL rules) and ExplainLine; the engine
 *  decides layer list, colour map, dB rule, unit and corners. Null corners -> nothing is drawn and the engine's
 *  corners_note is shown.
 *  TOKEN GATE (decision): the Map page is not inside <BackgroundGate>. useGateFail / useGateRetryNonce do not throw
 *  there (the context has defaults) but they would silently do nothing, and wrapping a bottom-sheet control in the
 *  full-screen gate would put a modal over the whole map. So a GateError is handled HERE: an explicit message, a
 *  link to /background where the token is entered (it is kept in sessionStorage: this browser tab only), and a retry button. */
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { bg } from "@/lib/bg";
import { GateError } from "@/lib/bgApi";
import { previewLegend, qualityLegend } from "@/lib/format";
import type { GranuleDetailResponse } from "@/lib/types";
import ExplainLine from "@/components/background/ExplainLine";
import type { OverlaySpec } from "./GlobeMap";
import { useSarImage } from "./explorer/useSarImage";

/** seconds since `active` became true (0 while inactive): the "still working" counter for a slow engine step */
function useElapsed(active: boolean): number {
  const [sec, setSec] = useState(0);
  useEffect(() => {
    if (!active) { setSec(0); return; }
    const t0 = Date.now();
    const id = setInterval(() => setSec(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [active]);
  return sec;
}

export default function MapSarControl({ granuleId, bbox, onOverlay }: {
  granuleId: string;
  bbox: [number, number, number, number] | null;
  onOverlay: (o: OverlaySpec | null) => void;
}) {
  const [inventory, setInventory] = useState<GranuleDetailResponse["inventory"] | null>(null);
  const [invError, setInvError] = useState<string | null>(null);
  const [gateKind, setGateKind] = useState<"unauthorized" | "unconfigured" | null>(null);
  const [layerKey, setLayerKey] = useState("");
  const [scale, setScale] = useState<"auto" | "linear" | "db">("auto");
  const [opacity, setOpacity] = useState(0.8);
  const [validate, setValidate] = useState(false);
  const [on, setOn] = useState(false);
  const [nonce, setNonce] = useState(0);
  const onOverlayRef = useRef(onOverlay);
  onOverlayRef.current = onOverlay;

  // the inventory (metadata only) is read when the user turns the overlay on, never on selection alone
  useEffect(() => {
    if (!on || inventory) return;
    const ctrl = new AbortController();
    setInvError(null);
    bg.granule(granuleId, ctrl.signal)
      .then((d) => { if (!ctrl.signal.aborted) { setInventory(d.inventory ?? null); setGateKind(null); } })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        if (err instanceof GateError) { setGateKind(err.kind); return; }
        setInvError(err instanceof Error ? err.message : String(err));
      });
    return () => ctrl.abort();
  }, [on, granuleId, inventory, nonce]);

  const layers = useMemo(
    () => (inventory?.grids ?? []).flatMap((g) => g.layers).filter((l) => l.previewable), [inventory]);
  const effectiveKey = layers.some((l) => l.key === layerKey) ? layerKey : (layers[0]?.key ?? "");
  const img = useSarImage({ enabled: on && !!inventory, granuleId, layerKey: effectiveKey, bbox, scale, validate, nonce });
  const gate = gateKind ?? img.gate;
  // every silent wait gets a visible state: reading the layer list (engine finds the granule at NASA, then reads its HDF5 index)
  const invWaiting = on && !inventory && !invError && !gate;
  const waited = useElapsed(invWaiting || (on && img.busy));

  useEffect(() => {
    if (img.url && img.corners) {
      const lons = img.corners.map((c) => c[0]);
      const lats = img.corners.map((c) => c[1]);
      onOverlayRef.current({
        id: `overlay:sar:${granuleId}:${effectiveKey}:${scale}:${img.url.slice(-12)}`,   // a new image is a new source
        dataUrl: img.url, opacity, corners: img.corners,
        bounds: [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)],  // envelope only
      });
    } else {
      onOverlayRef.current(null);
    }
  }, [img.url, img.corners, opacity, granuleId, effectiveKey, scale]);
  useEffect(() => () => onOverlayRef.current(null), []);

  const chosen = layers.find((l) => l.key === effectiveKey);
  const meta = img.meta;
  return (
    <div className="mt-2 rounded-lg border border-[#e7e2da] bg-white p-3 text-[11px] text-[#4a443c]" data-testid="map-sar">
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={on} disabled={!bbox} onChange={(e) => setOn(e.target.checked)} />
        show a SAR layer of this granule on the map
      </label>
      {!bbox && <p className="mt-1 text-[10px] text-[#8a837b]">pin a tile first: the image is clipped to the tile&apos;s box.</p>}
      {on && gate && (
        <p role="alert" className="mt-1 text-[10px] text-[#9e3b2c]">
          {gate === "unconfigured"
            ? "the engine has no Background token configured (NISAR_BACKGROUND_TOKEN)."
            : "the Background token is needed for SAR images."}{" "}
          <Link href="/background" className="underline">open Background to enter it (then come back)</Link>{" "}
          <button type="button" onClick={() => { setGateKind(null); setNonce((n) => n + 1); }}
                  className="rounded border border-[#9e3b2c] px-1">retry</button>
        </p>
      )}
      {invWaiting && (
        <p role="status" className="mt-1 text-[10px] text-[#8a837b]" data-testid="map-sar-inventory-wait">
          asking the engine for this granule&apos;s layer list… {waited}s (it finds the granule at NASA, then reads its file index: the first time can take a minute)
        </p>
      )}
      {on && invError && <p role="alert" className="mt-1 text-[10px] text-[#9e3b2c]">{invError}</p>}
      {on && inventory && !layers.length && (
        <p className="mt-1 text-[10px] text-[#8a837b]">no previewable layer in this granule&apos;s inventory — no SAR image to show.</p>
      )}
      {on && layers.length > 0 && (
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <select aria-label="SAR layer" value={effectiveKey} onChange={(e) => setLayerKey(e.target.value)}
                  className="max-w-[16rem] rounded border border-[#d8d2c8] bg-white px-1 py-0.5 font-mono">
            {layers.map((l) => <option key={l.key} value={l.key}>{l.path.split("/").slice(-3).join("/")}</option>)}
          </select>
          <select aria-label="scale" value={scale} onChange={(e) => setScale(e.target.value as "auto" | "linear" | "db")}
                  className="rounded border border-[#d8d2c8] bg-white px-1 py-0.5">
            <option value="auto">auto</option><option value="linear">linear</option><option value="db">dB</option>
          </select>
          <label className="flex items-center gap-1" title="Fade pixels where the interferometric coherence is low, so unreliable matches do not look like data. Display only: the values are not changed.">
          <input type="checkbox" checked={validate} onChange={(e) => setValidate(e.target.checked)} data-testid="map-sar-validate" />
          validate with coherence
        </label>
          <label className="flex items-center gap-1">opacity
            <input type="range" min={0.1} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} />
          </label>
        </div>
      )}
      {on && img.busy && (
        <p role="status" className="mt-1 text-[10px] text-[#8a837b]">
          drawing… {waited}s (the engine reads only this tile&apos;s pixels from NASA, then colours them)
        </p>
      )}
      {on && img.message && !gate && <p role="alert" className="mt-1 text-[10px] text-[#9e3b2c]">{img.message}</p>}
      {on && meta && !img.message && (
        <p className="mono mt-1 text-[10px] text-[#8a837b]" data-testid="map-sar-legend">
          {previewLegend(meta)}{qualityLegend(meta.quality) ? ` · ${qualityLegend(meta.quality)}` : ""}
        </p>
      )}
      {on && meta && !img.message && (meta.notes ?? []).map((n) => (
        <p key={n} role="note" className="mt-1 text-[10px] text-[#8a6d1f]" data-testid="map-sar-note">{n}</p>
      ))}
      {on && chosen && <div className="mt-1"><ExplainLine role={chosen.role} whatItIs={chosen.what_it_is} /></div>}
    </div>
  );
}
