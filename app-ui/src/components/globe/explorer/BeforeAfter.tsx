"use client";

/** BeforeAfter — the SAME layer of two acquisitions, same box, toggled in place.
 *  Honesty rules: (1) the second image is requested with the FIRST image's resolved scale, colour map and
 *  explicit lo/hi, so both are drawn on one fixed colour range — two independently stretched images would
 *  mislead; (2) the two dates are stated in words; (3) no sentence says what changed. All colour / dB /
 *  unit decisions stay in the engine; this component only orders the requests and shows the pictures. */
import { useEffect, useMemo, useRef, useState } from "react";
import { bg } from "@/lib/bg";
import { GateError } from "@/lib/bgApi";
import { dash } from "@/lib/explainCache";
import type { ExplorerGranule, GranuleDetailResponse } from "@/lib/types";
import { useSarImage } from "./useSarImage";

type Inv = NonNullable<GranuleDetailResponse["inventory"]>;
const previewable = (inv: Inv | null | undefined) =>
  (inv?.grids ?? []).flatMap((g) => g.layers).filter((l) => l.previewable);

const when = (g: ExplorerGranule | undefined) => (g?.start ? `${g.start} UTC` : "—");

export default function BeforeAfter({ candidates, bbox, defaultBefore, defaultAfter, onGate }: {
  /** acquisitions the engine/stack already grouped, oldest first (the caller orders them) */
  candidates: ExplorerGranule[];
  bbox: [number, number, number, number] | null;
  defaultBefore?: string;
  defaultAfter?: string;
  onGate?: (kind: "unauthorized" | "unconfigured") => void;
}) {
  const [beforeId, setBeforeId] = useState(defaultBefore ?? candidates[0]?.id ?? "");
  const [afterId, setAfterId] = useState(defaultAfter ?? candidates[candidates.length - 1]?.id ?? "");
  const [layerKey, setLayerKey] = useState("");
  const [shownEnd, setShownEnd] = useState<"before" | "after">("before");
  const [invs, setInvs] = useState<{ before: Inv | null; after: Inv | null }>({ before: null, after: null });
  const [invError, setInvError] = useState<string | null>(null);
  const onGateRef = useRef(onGate);
  onGateRef.current = onGate;

  // each side's own layer inventory (metadata only): the layer must exist in BOTH
  useEffect(() => {
    setInvs({ before: null, after: null });
    setInvError(null);
    if (!beforeId || !afterId || beforeId === afterId) return;
    const ctrl = new AbortController();
    Promise.all([bg.granule(beforeId, ctrl.signal), bg.granule(afterId, ctrl.signal)])
      .then(([b, a]) => { if (!ctrl.signal.aborted) setInvs({ before: b.inventory ?? null, after: a.inventory ?? null }); })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        if (err instanceof GateError) { onGateRef.current?.(err.kind); return; }
        setInvError(err instanceof Error ? err.message : String(err));
      });
    return () => ctrl.abort();
  }, [beforeId, afterId]);

  const common = useMemo(() => {
    const a = new Set(previewable(invs.after).map((l) => l.key));
    return previewable(invs.before).filter((l) => a.has(l.key));
  }, [invs]);
  const key = common.some((l) => l.key === layerKey) ? layerKey : (common[0]?.key ?? "");

  // 1) the BEFORE image decides scale / colour map / range ...
  const first = useSarImage({ enabled: !!key && beforeId !== afterId, granuleId: beforeId, layerKey: key, bbox, maxPx: 384 });
  const fm = first.meta;
  const fixable = !!fm && fm.cmap !== "categorical" && Number.isFinite(fm.lo) && Number.isFinite(fm.hi) && fm.lo < fm.hi;
  // 2) ... the AFTER image is requested with exactly those values
  const second = useSarImage({
    enabled: !!key && beforeId !== afterId && fixable && !!first.url, granuleId: afterId, layerKey: key, bbox, maxPx: 384,
    scale: fm?.scale, cmap: fm?.cmap, lo: fixable ? fm!.lo : null, hi: fixable ? fm!.hi : null,
  });
  useEffect(() => { if (first.gate) onGateRef.current?.(first.gate); }, [first.gate]);
  useEffect(() => { if (second.gate) onGateRef.current?.(second.gate); }, [second.gate]);

  const gb = candidates.find((c) => c.id === beforeId);
  const ga = candidates.find((c) => c.id === afterId);
  const sm = second.meta;
  const rangeHonoured = !!sm && !!fm && sm.lo === fm.lo && sm.hi === fm.hi && sm.scale === fm.scale && sm.cmap === fm.cmap;
  const sameGrid = !!first.corners && !!second.corners
    && first.corners.every((c, i) => Math.abs(c[0] - second.corners![i][0]) < 1e-6 && Math.abs(c[1] - second.corners![i][1]) < 1e-6);
  const cur = shownEnd === "before" ? first : second;

  const opt = (g: ExplorerGranule) => <option key={g.id} value={g.id}>{when(g)}</option>;
  return (
    <div className="mt-2 rounded-md border border-[#262c36] bg-[#12161c] p-2 text-[10px] text-[#cfd6dc]" data-testid="before-after">
      <p className="mb-1 text-[#8b949e]">
        the same layer on two dates, drawn on one fixed colour range (the range of the first image is reused for the second).
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1">before
          <select aria-label="before date" value={beforeId} onChange={(e) => setBeforeId(e.target.value)}
                  className="rounded border border-[#333b47] bg-[#0e1217] px-1 py-0.5 font-mono">{candidates.map(opt)}</select>
        </label>
        <label className="flex items-center gap-1">after
          <select aria-label="after date" value={afterId} onChange={(e) => setAfterId(e.target.value)}
                  className="rounded border border-[#333b47] bg-[#0e1217] px-1 py-0.5 font-mono">{candidates.map(opt)}</select>
        </label>
        <select aria-label="layer" value={key} onChange={(e) => setLayerKey(e.target.value)} disabled={!common.length}
                className="max-w-[14rem] rounded border border-[#333b47] bg-[#0e1217] px-1 py-0.5 font-mono">
          {common.map((l) => <option key={l.key} value={l.key}>{l.path.split("/").slice(-3).join("/")}</option>)}
        </select>
      </div>
      {beforeId === afterId && <p role="alert" className="mt-1 text-[#e8a087]">pick two different acquisitions.</p>}
      {invError && <p role="alert" className="mt-1 text-[#e8a087]">{invError}</p>}
      {beforeId !== afterId && invs.before && invs.after && !common.length && (
        <p role="alert" className="mt-1 text-[#e8a087]">no previewable layer exists in both granules, so there is nothing to compare.</p>
      )}
      {key && beforeId !== afterId && (
        <>
          <div className="mt-2 flex items-center gap-1" role="group" aria-label="date shown">
            {(["before", "after"] as const).map((e) => (
              <button key={e} type="button" aria-pressed={shownEnd === e} onClick={() => setShownEnd(e)}
                      className={`rounded border px-2 py-1 ${shownEnd === e ? "border-[#3fb6c9] bg-[#12262c] text-[#7fd8e6]" : "border-[#333b47] text-[#aab3bd]"}`}>
                {e === "before" ? "Before" : "After"}
              </button>
            ))}
            <span className="ml-2 font-mono" data-testid="ba-dates">
              {shownEnd === "before" ? `Before: ${when(gb)}` : `After: ${when(ga)}`}
              {" · "}comparing {when(gb)} with {when(ga)}
            </span>
          </div>
          <div className="mt-1 min-h-[3rem]">
            {cur.busy && <p role="status" className="text-[#8b949e]">drawing…</p>}
            {cur.message && <p role="alert" className="text-[#e8a087]">{cur.message}</p>}
            {shownEnd === "after" && first.url && !fixable && !first.busy && (
              <p role="alert" className="text-[#e8a087]">this layer has no continuous colour range to fix, so the second date is not drawn.</p>
            )}
            {cur.url && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={cur.url} alt={`${shownEnd} backscatter image`} className="max-h-72 w-auto max-w-full rounded border border-[#262c36] bg-[#0e1116]" />
            )}
          </div>
          {fm && (
            <p className="font-mono text-[#8b949e]" data-testid="ba-legend">
              {dash(fm.cmap)} · {dash(fm.scale)} · {dash(fm.lo)} … {dash(fm.hi)} {dash(fm.unit)} · same range for both
              {sm && !rangeHonoured && <span className="text-[#e8a087]"> · the engine did not report the same range for the second image</span>}
              {sm && rangeHonoured && !sameGrid && <span className="text-[#e8a087]"> · the two clips are not on the same pixel grid</span>}
            </p>
          )}
        </>
      )}
    </div>
  );
}
