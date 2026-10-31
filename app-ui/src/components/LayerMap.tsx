"use client";

import { useEffect, useRef, useState } from "react";
import { MapContainer, ImageOverlay, TileLayer, CircleMarker, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { colorize as colorizeLayer, decodeLayer } from "@/lib/raster";
import type { LayerCategory, LayerPayload } from "@/lib/types";

/** <u1> payloads that predate the engine's `categories`: 1 = toward the sensor, 2 = away (the detected layer) */
const LEGACY_DETECTED_CATEGORIES: LayerCategory[] = [
  { value: 1, label: "toward sensor", tone: "positive" },
  { value: 2, label: "away from sensor", tone: "negative" },
];

/** the shared colour implementation (lib/raster.ts), drawn 1:1 — this map does no pre-upscaling */
function colorize(layer: LayerPayload, canvas: HTMLCanvasElement): void {
  const decoded = decodeLayer(layer);
  if (!decoded.ok) throw new Error(decoded.reason);
  const withCats = layer.dtype === "<u1" && !layer.categories
    ? { ...layer, categories: LEGACY_DETECTED_CATEGORIES }
    : layer;
  colorizeLayer(withCats, decoded.data, canvas, 1);
}

const LAYERS = [
  { key: "displacement", label: "LOS displacement" },
  { key: "detected", label: "Detected" },
  { key: "threshold", label: "Threshold" },
  { key: "residual", label: "Residual vs model" },
];

/** Where the layer grids come from is the caller's business: the engine (a live run) or the
 *  Supabase cache (a cached verdict). `sourceKey` identifies the source so a change reloads. */
export default function LayerMap({
  load,
  sourceKey,
  bounds,
}: {
  load: (layer: string) => Promise<LayerPayload>;
  sourceKey: string;
  bounds: [[number, number], [number, number]] | null;
}) {
  const [layerKey, setLayerKey] = useState("displacement");
  const [layer, setLayer] = useState<LayerPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErr(null);
    load(layerKey)
      .then((l) => alive && setLayer(l))
      .catch((e) => {
        if (!alive) return;
        setLayer(null); // never leave the previous layer on screen under a new layer's name
        setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // `load` is identified by sourceKey; listing the function itself would reload on every render
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey, layerKey]);

  const [dataUrl, setDataUrl] = useState<string | null>(null);

  useEffect(() => {
    if (layer && canvasRef.current) {
      colorize(layer, canvasRef.current);
      setDataUrl(canvasRef.current.toDataURL());
    }
  }, [layer]);

  // plan 6.4: the layer payload carries its own geographic frame (server-computed
  // core-grid corners). Fall back to the bundle bbox only when a payload predates it.
  const frame: [[number, number], [number, number]] | null = layer?.bounds_lonlat
    ? [
        [layer.bounds_lonlat[1], layer.bounds_lonlat[0]],
        [layer.bounds_lonlat[3], layer.bounds_lonlat[2]],
      ]
    : bounds;
  const center: [number, number] = frame
    ? [(frame[0][0] + frame[1][0]) / 2, (frame[0][1] + frame[1][1]) / 2]
    : [0, 0];

  return (
    <section className="rounded-lg border border-[#e7e2da] bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Evidence layers</h3>
        {LAYERS.map((l) => (
          <button
            key={l.key}
            onClick={() => setLayerKey(l.key)}
            aria-pressed={layerKey === l.key}
            className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
              layerKey === l.key
                ? "border-[#2c5f58] bg-[#eff5f3] text-[#2c5f58]"
                : "border-[#e7e2da] text-[#8a837b] hover:border-[#b08d57] hover:text-[#4a443c]"
            }`}
          >
            {l.label}
          </button>
        ))}
      </div>
      {loading && !err && <p className="mono mt-2 text-xs text-[#8a837b]">loading layer…</p>}
      {err && <p className="mono mt-2 text-xs text-[#8a837b]">{err} (layer not produced for this verdict)</p>}
      <div className="mt-3 h-[420px] overflow-hidden rounded-md border border-[#e7e2da]">
        <MapContainer center={center} zoom={9} scrollWheelZoom style={{ height: "100%", width: "100%" }}>
          <TileLayer
            attribution="&copy; Esri"
            url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
          />
          {layer && frame && dataUrl && (
            <ImageOverlay url={dataUrl} bounds={frame} opacity={0.78} />
          )}
        </MapContainer>
      </div>
      {layer && (
        <p className="mt-2 text-[11px] text-[#8a837b]">
          {layer.units} · shown on its geographic bounding box ({layer.shape[1]}×{layer.shape[0]} px
          {layer.bounds_lonlat ? ", server-computed corners" : ", bundle frame"}).
          {layer.note ? ` ${layer.note}` : ""}
        </p>
      )}
      {/* hidden canvas rendered offscreen for the overlay image */}
      <canvas ref={canvasRef} className="hidden" />
    </section>
  );
}
