"use client";

/** PreviewPane (§4.4) — one pane of the Compare view: the engine's preview PNG for ONE granule +
 *  layer + box, with a pixel probe, colour-map/min-max readout, acquisition time and the four
 *  honest states (loading / image / "no acquisition at this date" / "preview unavailable: …").
 *  Zoom = a NEW preview request for a sub-box with a larger max_px — never a client upscale.
 *  The PNG is fetched with the bearer token in the Authorization header (never a URL token). */
import { useEffect, useMemo, useRef, useState } from "react";
import { bg } from "@/lib/bg";
import { GateError } from "@/lib/bgApi";
import { useGateFail, useGateRetryNonce } from "./BackgroundGate";
import type { ExplorerGranule, PreviewMeta } from "@/lib/types";

export type PaneState =
  | { s: "loading" }
  | { s: "image"; url: string; meta: PreviewMeta }
  | { s: "empty"; text: string }          // no acquisition at this date
  | { s: "error"; text: string };         // preview unavailable: <engine error text>

export default function PreviewPane({ granule, layer, box, maxPx, cmap, synced, onZoom, onPan, title, onProbeMoved, emptyText }: {
  granule: ExplorerGranule | null;
  layer: string | null;
  box: [number, number, number, number];
  maxPx: number;
  cmap: string;
  synced: boolean;
  onZoom: (focusX: number, focusY: number, factor: number) => void;
  onPan?: (dxFrac: number, dyFrac: number) => void;
  title: string;
  onProbeMoved?: () => void;
  /** the engine's own no-acquisition text (CompareView passes the per-end message) */
  emptyText?: string;
}) {
  const gateFail = useGateFail();
  const retryNonce = useGateRetryNonce();
  const [state, setState] = useState<PaneState>({ s: "loading" });
  const [probe, setProbe] = useState<{ value: number | null; finite: boolean; lon: number; lat: number; dtype: string } | null>(null);
  const [probing, setProbing] = useState(false);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const urlRef = useRef<string | null>(null);

  const key = `${granule?.id ?? "-"}|${layer ?? "-"}|${box.map((v) => v.toFixed(4)).join(",")}|${maxPx}|${cmap}`;

  useEffect(() => {
    if (!granule || !layer) {
      setState({ s: "empty", text: emptyText ?? "no acquisition at this date" });
      return;
    }
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setState({ s: "loading" });
    setProbe(null);
    bg.previewBlob({ id: granule.id, layer, west: box[0], south: box[1], east: box[2], north: box[3], maxPx, cmap }, ctrl.signal)
      .then(({ blob, meta }) => {
        if (ctrl.signal.aborted) return;
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        const url = URL.createObjectURL(blob!);
        urlRef.current = url;
        setState({ s: "image", url, meta });
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        if (err instanceof GateError) {
          gateFail(err.kind);
          return;
        }
        const text = err instanceof Error ? err.message : String(err);
        setState(/no valid pixels/i.test(text)
          ? { s: "empty", text }
          : { s: "error", text: `preview unavailable: ${text}` });
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, gateFail, retryNonce]);

  useEffect(() => () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
  }, []);

  const onMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!granule || !layer || state.s !== "image" || !imgRef.current) return;
    if (probing) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const fx = (e.clientX - rect.left) / rect.width;
    const fy = (e.clientY - rect.top) / rect.height;
    const lon = box[0] + (box[2] - box[0]) * fx;
    const lat = box[3] - (box[3] - box[1]) * fy;
    setProbing(true);
    bg.probe({ id: granule.id, layer, lon, lat })
      .then((r) => setProbe({ value: r.value, finite: r.finite, lon: r.lon, lat: r.lat, dtype: r.dtype }))
      .catch(() => setProbe(null))
      .finally(() => {
        setProbing(false);
        onProbeMoved?.();
      });
  };

  const onWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    if (state.s !== "image") return;
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    const fx = (e.clientX - rect.left) / rect.width;
    const fy = (e.clientY - rect.top) / rect.height;
    onZoom(fx, fy, e.deltaY < 0 ? 1.6 : 1 / 1.6);
  };

  // drag-to-pan (fraction of the box span; the PARENT decides sync/unsync)
  const dragRef = useRef<{ x: number; y: number } | null>(null);
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (state.s !== "image") return;
    dragRef.current = { x: e.clientX, y: e.clientY };
  };
  const onPointerMove2 = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || !onPan) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const dx = (e.clientX - drag.x) / rect.width;
    const dy = (e.clientY - drag.y) / rect.height;
    if (Math.abs(e.clientX - drag.x) > 3 || Math.abs(e.clientY - drag.y) > 3) {
      dragRef.current = { x: e.clientX, y: e.clientY };
      onPan(-dx, dy);
    }
  };
  const onPointerUp2 = () => { dragRef.current = null; };

  const meta = state.s === "image" ? state.meta : null;
  const timeLabel = useMemo(() => granule?.start ?? granule?.ref_time ?? null, [granule]);

  return (
    <figure className="flex min-h-0 flex-1 flex-col rounded-md border border-[#262c36] bg-[#0e1116]" data-pane-title={title}>
      <figcaption className="flex flex-wrap items-baseline justify-between gap-x-2 border-b border-[#262c36] px-2 py-1 text-[10px] text-[#8b949e]">
        <span className="font-semibold text-[#cfd6dc]">{title}</span>
        {granule && <span className="font-mono">{granule.id.slice(0, 42)}{granule.id.length > 42 ? "…" : ""}</span>}
        {timeLabel && <span>{timeLabel} UTC</span>}
      </figcaption>
      <div
        className="relative min-h-[180px] flex-1 cursor-grab touch-none select-none overflow-hidden active:cursor-grabbing"
        onMouseMove={onMove}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove2}
        onPointerUp={onPointerUp2}
        onPointerLeave={onPointerUp2}
        role="img"
        aria-label={`preview pane ${title}: ${state.s === "image" ? `layer ${layer} of ${granule?.id ?? ""}` : state.s}`}
      >
        {state.s === "loading" && (
          <div className="flex h-full items-center justify-center gap-2 p-4 text-xs text-[#8b949e]" role="status" aria-live="polite">
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-[#3fb6c9] border-t-transparent" aria-hidden />
            fetching the preview (clipped to the box, engine-side)…
          </div>
        )}
        {state.s === "empty" && (
          <div className="flex h-full items-center justify-center p-4 text-center text-xs text-[#8b949e]" role="status" aria-live="polite">
            {state.text}
          </div>
        )}
        {state.s === "error" && (
          <div className="flex h-full items-center justify-center p-4 text-center text-xs text-[#e8a087]" role="alert" aria-live="polite">
            {state.text}
          </div>
        )}
        {state.s === "image" && (
          <img
            ref={imgRef}
            src={state.url}
            alt={`backscatter preview for ${granule?.id ?? "granule"}, layer ${layer}`}
            className="h-full w-full object-contain"
            draggable={false}
          />
        )}
      </div>
      <figcaption className="border-t border-[#262c36] px-2 py-1 text-[10px] leading-relaxed text-[#8b949e]">
        {meta ? (
          <>
            cmap <span className="font-mono text-[#cfd6dc]">{meta.cmap}</span> · range{" "}
            <span className="font-mono text-[#cfd6dc]">{fmtNum(meta.lo)} … {fmtNum(meta.hi)}</span> ·
            shape {meta.stats.shape[1]}×{meta.stats.shape[0]} {meta.stats.dtype} · NaN{" "}
            {(meta.stats.nan_fraction * 100).toFixed(1)}%
            {meta.cache_hit && <span className="ml-1 rounded border border-[#4a3a1f] px-1 text-[9px] uppercase text-[#e6c977]">cache</span>}
            {meta.slice_note && <span> · {meta.slice_note}</span>}
            <br />
          </>
        ) : null}
        {probe ? (
          probe.finite
            ? <>probe: <span className="font-mono text-[#cfd6dc]">{fmtNum(probe.value ?? 0)}</span> {probe.dtype} @ {probe.lat.toFixed(5)}, {probe.lon.toFixed(5)}</>
            : <>probe: no valid pixel here (NaN) @ {probe.lat.toFixed(5)}, {probe.lon.toFixed(5)}</>
        ) : (
          "hover to probe a pixel (value + lon/lat, engine-read) · wheel zooms by re-requesting a sub-box"
        )}
      </figcaption>
    </figure>
  );
}

function fmtNum(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e5 || (a < 1e-3 && a > 0)) return v.toExponential(2);
  return String(Number(v.toPrecision(4)));
}
