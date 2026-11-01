"use client";

/** useSarImage — ONE place that fetches an engine preview PNG for (granule, layer, box) and owns its object URL.
 *  Used by the footprint mini-map control, the Map-page control and the Before/After toggle, so the
 *  fetch / abort / revoke rules exist once:
 *   - every request has an AbortController; a newer request or unmount aborts the older one;
 *   - every URL.createObjectURL is revoked when replaced, when the hook is disabled and on unmount;
 *   - nothing is set after unmount (a `signal.aborted` check guards every update);
 *   - the engine decides colours, dB, unit and corners; this only transports and reports them. */
import { useEffect, useRef, useState } from "react";
import { bg } from "@/lib/bg";
import { GateError } from "@/lib/bgApi";
import type { PreviewMeta } from "@/lib/types";

export type Corners = NonNullable<PreviewMeta["corners_lonlat"]>;

export interface SarImageParams {
  enabled: boolean;
  granuleId: string;
  layerKey: string;
  bbox: [number, number, number, number] | null;
  scale?: string;
  cmap?: string;
  /** explicit colour range (both or neither) */
  lo?: number | null;
  hi?: number | null;
  maxPx?: number;
  /** bump to ask again after a failure (a saved token, a rate limit) */
  nonce?: number;
}

export interface SarImage {
  url: string | null;
  corners: Corners | null;
  meta: PreviewMeta | null;
  busy: boolean;
  message: string | null;
  /** set when the engine answered 401 / gate-503; the caller decides what to show */
  gate: "unauthorized" | "unconfigured" | null;
}

const IDLE: SarImage = { url: null, corners: null, meta: null, busy: false, message: null, gate: null };

export function useSarImage(p: SarImageParams): SarImage {
  const [state, setState] = useState<SarImage>(IDLE);
  const urlRef = useRef<string | null>(null);
  const bboxKey = p.bbox ? p.bbox.join(",") : "";

  const dropUrl = () => {
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = null;
  };
  useEffect(() => dropUrl, []);   // unmount: revoke whatever is still held

  useEffect(() => {
    if (!p.enabled || !p.layerKey || !p.granuleId || !p.bbox) {
      dropUrl();
      setState(IDLE);
      return;
    }
    const box = p.bbox;
    const ctrl = new AbortController();
    setState((s) => ({ ...s, busy: true, message: null, gate: null }));
    bg.previewBlob({
      id: p.granuleId, layer: p.layerKey, west: box[0], south: box[1], east: box[2], north: box[3],
      maxPx: p.maxPx ?? 512, scale: p.scale ?? "auto", cmap: p.cmap ?? "auto",
      ...(p.lo != null && p.hi != null ? { lo: p.lo, hi: p.hi } : {}),
    }, ctrl.signal)
      .then(({ blob, meta }) => {
        if (ctrl.signal.aborted) return;
        dropUrl();
        if (!blob || !meta.corners_lonlat) {     // never draw an image the engine could not place
          setState({ ...IDLE, meta,
            message: meta.corners_note ?? "the engine returned no image corners for this layer, so it is not drawn" });
          return;
        }
        urlRef.current = URL.createObjectURL(blob);
        setState({ ...IDLE, url: urlRef.current, corners: meta.corners_lonlat, meta });
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        dropUrl();
        if (err instanceof GateError) { setState({ ...IDLE, gate: err.kind, message: err.message }); return; }
        setState({ ...IDLE, message: err instanceof Error ? err.message : String(err) });
      });
    return () => ctrl.abort();
    // p.* are read through the dependency list below on purpose (primitives only)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.enabled, p.granuleId, p.layerKey, bboxKey, p.scale, p.cmap, p.lo, p.hi, p.maxPx, p.nonce]);

  return state;
}
