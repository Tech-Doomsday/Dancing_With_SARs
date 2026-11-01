"use client";

/** LayerThumb — a ~96 px engine-rendered thumbnail for ONE previewable layer, fetched lazily when its row
 *  scrolls into view. A layer that is not previewable never mounts this (no element, no request).
 *  Failure / rate limit shows "—" with the reason; a rate limit is not retried automatically. */
import { useEffect, useRef, useState } from "react";
import { GateError } from "@/lib/bgApi";
import { requestThumb, ThumbError, thumbCached, thumbKey, THUMB_MAX_PX, type Thumb } from "@/lib/thumbQueue";

export default function LayerThumb({ granuleId, layerKey, bbox, previewable, onGate }: {
  granuleId: string;
  layerKey: string;
  bbox: [number, number, number, number] | null;
  previewable: boolean;
  onGate?: (kind: "unauthorized" | "unconfigured") => void;
}) {
  const boxKey = bbox ? bbox.join(",") : "";
  const startKey = bbox ? thumbKey(granuleId, layerKey, bbox, "auto") : "";
  const [thumb, setThumb] = useState<Thumb | null>(() => (startKey ? thumbCached(startKey) ?? null : null));
  const [error, setError] = useState<{ text: string; rateLimited: boolean } | null>(null);
  const [visible, setVisible] = useState(false);
  const [attempt, setAttempt] = useState(0);        // bumped only by the user's "retry" click
  const elRef = useRef<HTMLDivElement | null>(null);
  const onGateRef = useRef(onGate);
  onGateRef.current = onGate;

  useEffect(() => {                                  // become visible once, then stop observing
    if (!previewable || !bbox || visible) return;
    const el = elRef.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") { setVisible(true); return; }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { setVisible(true); io.disconnect(); }
    }, { rootMargin: "80px" });
    io.observe(el);
    return () => io.disconnect();
  }, [previewable, boxKey, visible]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!previewable || !bbox || !visible) return;
    const ctrl = new AbortController();
    setError(null);
    requestThumb({ granuleId, layerKey, bbox, signal: ctrl.signal })
      .then((t) => { if (!ctrl.signal.aborted) setThumb(t); })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        if (err instanceof GateError) { onGateRef.current?.(err.kind); setError({ text: err.message, rateLimited: false }); return; }
        const te = err instanceof ThumbError ? err : null;
        setError({ text: err instanceof Error ? err.message : String(err), rateLimited: te?.rateLimited ?? false });
      });
    return () => ctrl.abort();
  }, [previewable, granuleId, layerKey, boxKey, visible, attempt]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!previewable) return null;
  return (
    <div ref={elRef} className="shrink-0" style={{ width: THUMB_MAX_PX, minHeight: 24 }} data-testid="layer-thumb">
      {thumb ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={thumb.dataUrl} alt={`thumbnail of ${layerKey.split("__").slice(-1)[0]}`} width={THUMB_MAX_PX}
             className="block h-auto max-w-full rounded border border-[#262c36] bg-[#0e1116]" />
      ) : error ? (
        <div className="text-[10px] text-[#8b949e]" title={error.text}>
          <span aria-label="no thumbnail">—</span>
          <span className="ml-1 text-[#e8a087]">{error.text.length > 60 ? `${error.text.slice(0, 57)}…` : error.text}</span>
          {error.rateLimited && (
            <button type="button" onClick={() => setAttempt((n) => n + 1)}
                    className="ml-1 rounded border border-[#333b47] px-1 text-[#aab3bd] hover:text-[#7fd8e6]">retry</button>
          )}
        </div>
      ) : visible ? (
        <span className="text-[10px] text-[#5a636e]" role="status">…</span>
      ) : null}
    </div>
  );
}
