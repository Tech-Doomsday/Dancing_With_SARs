"use client";

import { useEffect, useRef, useState } from "react";
import { MapContainer, ImageOverlay, TileLayer, CircleMarker, Tooltip } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { api } from "@/lib/api";
import type { LayerPayload } from "@/lib/types";

/** Decode little-endian base64 float32/uint8 grids (server-computed bounds). */
function decode(layer: LayerPayload): Float32Array | Uint8Array {
  const bytes = Uint8Array.from(atob(layer.data_b64), (c) => c.charCodeAt(0));
  if (layer.dtype === "<u1") return new Uint8Array(bytes.buffer);
  return new Float32Array(bytes.buffer);
}

function colorize(layer: LayerPayload, canvas: HTMLCanvasElement): void {
  const [h, w] = layer.shape;
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const img = ctx.createImageData(w, h);
  const data = decode(layer);
  const vmin = layer.vmin ?? 0;
  const vmax = layer.vmax ?? 1;
  const span = vmax - vmin || 1;
  for (let i = 0; i < w * h; i++) {
    const v = Number(data[i]);
    const [r, g, b, a] = px(layer, v, vmin, span, i);
    img.data[i * 4] = r;
    img.data[i * 4 + 1] = g;
    img.data[i * 4 + 2] = b;
    img.data[i * 4 + 3] = a;
  }
  ctx.putImageData(img, 0, 0);
}

function px(layer: LayerPayload, v: number, vmin: number, span: number, i: number): [number, number, number, number] {
  if (layer.dtype === "<u1") {
    // detected: 1 = toward (teal), 2 = away (terracotta)
    if (v === 1) return [44, 95, 88, 210];
    if (v === 2) return [158, 59, 44, 210];
    return [0, 0, 0, 0];
  }
  if (!Number.isFinite(v)) return [0, 0, 0, 0]; // NaN -> transparent
  const t = Math.max(0, Math.min(1, (v - vmin) / span));
  if (layer.colormap.startsWith("diverging")) {
    // terracotta -> cream -> teal
    const r = Math.round(t < 0.5 ? 158 + t * 2 * (247 - 158) : 247 - (t - 0.5) * 2 * (247 - 44));
    const g = Math.round(t < 0.5 ? 59 + t * 2 * (242 - 59) : 242 - (t - 0.5) * 2 * (242 - 95));
    const b = Math.round(t < 0.5 ? 44 + t * 2 * (233 - 44) : 233 - (t - 0.5) * 2 * (233 - 88));
    return [r, g, b, 205];
  }
  // sequential cream -> teal
  return [
    Math.round(247 - t * (247 - 26)),
    Math.round(242 - t * (242 - 77)),
    Math.round(233 - t * (233 - 68)),
    205,
  ];
}

const LAYERS = [
  { key: "displacement", label: "LOS displacement" },
  { key: "detected", label: "Detected" },
  { key: "threshold", label: "Threshold" },
  { key: "residual", label: "Residual vs Okada" },
];

export default function LayerMap({ runId, bounds }: { runId: string; bounds: [[number, number], [number, number]] | null }) {
  const [layerKey, setLayerKey] = useState("displacement");
  const [layer, setLayer] = useState<LayerPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .layer(runId, layerKey)
      .then((r) => alive && setLayer(r.layer))
      .catch((e) => alive && setErr(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [runId, layerKey]);

  const [dataUrl, setDataUrl] = useState<string | null>(null);

  useEffect(() => {
    if (layer && canvasRef.current) {
      colorize(layer, canvasRef.current);
      setDataUrl(canvasRef.current.toDataURL());
    }
  }, [layer]);

  const center: [number, number] = bounds
    ? [(bounds[0][0] + bounds[1][0]) / 2, (bounds[0][1] + bounds[1][1]) / 2]
    : [0, 0];

  return (
    <section className="rounded-lg border border-[#e7e2da] bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Evidence layers</h3>
        {LAYERS.map((l) => (
          <button
            key={l.key}
            onClick={() => setLayerKey(l.key)}
            className={`rounded-full border px-2.5 py-0.5 text-xs ${
              layerKey === l.key ? "border-[#2c5f58] bg-[#eff5f3] text-[#2c5f58]" : "border-[#e7e2da] text-[#8a837b]"
            }`}
          >
            {l.label}
          </button>
        ))}
      </div>
      {err && <p className="mono mt-2 text-xs text-[#8a837b]">{err} (layer not produced for this verdict)</p>}
      <div className="mt-3 h-[420px] overflow-hidden rounded-md border border-[#e7e2da]">
        <MapContainer center={center} zoom={9} scrollWheelZoom style={{ height: "100%", width: "100%" }}>
          <TileLayer
            attribution="&copy; Esri"
            url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
          />
          {layer && bounds && dataUrl && (
            <ImageOverlay url={dataUrl} bounds={bounds} opacity={0.78} />
          )}
        </MapContainer>
      </div>
      {layer && (
        <p className="mt-2 text-[11px] text-[#8a837b]">
          {layer.units} · shown on its geographic bounding box ({layer.shape[1]}×{layer.shape[0]} px).
          {layer.note ? ` ${layer.note}` : ""}
        </p>
      )}
      {/* hidden canvas rendered offscreen for the overlay image */}
      <canvas ref={canvasRef} className="hidden" />
    </section>
  );
}
