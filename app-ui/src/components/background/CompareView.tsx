"use client";

/** CompareView (§4.4) — two panes side by side (stacked on mobile), each showing the preview
 *  image for ONE END of a GUNW pair: the GCOV backscatter at that epoch (§4.1), served by the
 *  engine's gated preview (clipped to the viewed box). Shared zoom/pan (toggle to unsync), a
 *  pixel probe with value and lon/lat, colour map + min/max, the acquisition time, a swap
 *  button, and a layer selector that switches BOTH panes. Variants of the same acquisition
 *  (different bandwidth/CRID/version) can be shown side by side — the resolution comparison —
 *  with their bandwidth labelled. Panes show exactly one of: image / loading /
 *  "no acquisition at this date" / "preview unavailable: <engine error text>". */
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeftRight, Link2, Link2Off, X } from "lucide-react";
import PreviewPane from "./PreviewPane";
import { bg } from "@/lib/bg";
import { GateError } from "@/lib/bgApi";
import { useGateFail, useGateRetryNonce } from "./BackgroundGate";
import { zoomBox } from "@/lib/groups";
import type { BackgroundGroup, ExplorerGranule, StackPairResponse } from "@/lib/types";

const CMAPS = ["auto", "viridis", "gray", "magma", "diverging"] as const;
const SCALES = [["auto", "auto"], ["db", "dB"], ["linear", "linear"]] as const;

type EndState =
  | { s: "loading" }
  | { s: "ready"; variants: ExplorerGranule[] }
  | { s: "none"; text: string };        // no backscatter acquisition found for this date

export default function CompareView({ group, initialEnd, box, onClose }: {
  group: BackgroundGroup;
  initialEnd: "ref" | "sec";
  box: [number, number, number, number];
  onClose: () => void;
}) {
  const gateFail = useGateFail();
  const retryNonce = useGateRetryNonce();
  // per-pane boxes: when zoom is SYNCED both keys move together; UNSYNCED each pane keeps its
  // own box (the toggle restores the shared box on both sides).
  const [views, setViews] = useState<{ L: [number, number, number, number]; R: [number, number, number, number] }>({ L: box, R: box });
  const [synced, setSynced] = useState(true);
  const [cmap, setCmap] = useState<string>("auto");
  const [scale, setScale] = useState<string>("auto");
  const [swapped, setSwapped] = useState(initialEnd === "sec");
  const [pair, setPair] = useState<StackPairResponse | null>(null);
  const [pairError, setPairError] = useState<string | null>(null);
  const [refSel, setRefSel] = useState<string | null>(null);
  const [secSel, setSecSel] = useState<string | null>(null);
  const [layer, setLayer] = useState<string | null>(null);
  const [layers, setLayers] = useState<{ key: string; label: string }[] | null>(null);
  const [layersError, setLayersError] = useState<string | null>(null);

  // the two GCOV granules (if any) matching the GUNW's two dates — engine-computed (§4.1)
  useEffect(() => {
    const ctrl = new AbortController();
    setPair(null);
    setPairError(null);
    bg.stackPair(group.granule_ids[0], ctrl.signal)
      .then((r) => {
        if (ctrl.signal.aborted) return;
        setPair(r);
        setRefSel(r.ref.granules[0]?.id ?? null);
        setSecSel(r.sec.granules[0]?.id ?? null);
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        if (err instanceof GateError) { gateFail(err.kind); return; }
        setPairError(err instanceof Error ? err.message : String(err));
      });
    return () => ctrl.abort();
  }, [group.key, group.granule_ids, gateFail, retryNonce]);

  // the layer selector: the readable GCOV layers of whichever end has granules
  const anyGcov = pair?.ref.granules[0]?.id ?? pair?.sec.granules[0]?.id ?? null;
  useEffect(() => {
    if (!anyGcov) return;
    const ctrl = new AbortController();
    bg.granule(anyGcov, ctrl.signal)
      .then((d) => {
        if (ctrl.signal.aborted) return;
        const rows: { key: string; label: string }[] = [];
        for (const grid of d.inventory?.grids ?? []) {
          for (const l of grid.layers) {
            if (!l.readable) continue;
            rows.push({ key: l.key, label: l.key.split("__").slice(-2).join(" / ") });
          }
        }
        setLayers(rows);
        setLayer((cur) => cur ?? rows[0]?.key ?? null);
      })
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        if (err instanceof GateError) { gateFail(err.kind); return; }
        setLayersError(err instanceof Error ? err.message : String(err));
      });
    return () => ctrl.abort();
  }, [anyGcov, gateFail, retryNonce]);

  const onZoom = useCallback((pane: "L" | "R", fx: number, fy: number, factor: number) => {
    setViews((vs) => {
      const box2 = zoomBox(vs[pane], fx, fy, factor);
      return synced ? { L: box2, R: box2 } : { ...vs, [pane]: box2 };
    });
  }, [synced]);
  const onPan = useCallback((pane: "L" | "R", dx: number, dy: number) => {
    setViews((vs) => {
      const v = vs[pane];
      const spanX = v[2] - v[0];
      const spanY = v[3] - v[1];
      const w2 = Math.min(180 - spanX, Math.max(-180, v[0] - spanX * dx));
      const s2 = Math.min(90 - spanY, Math.max(-90, v[1] - spanY * dy));
      const box2: [number, number, number, number] = [w2, s2, w2 + spanX, s2 + spanY];
      return synced ? { L: box2, R: box2 } : { ...vs, [pane]: box2 };
    });
  }, [synced]);
  const toggleSync = () => {
    setSynced((s) => {
      if (!s) setViews((vs) => ({ L: vs.L, R: vs.L }));   // re-syncing adopts the LEFT box
      return !s;
    });
  };

  const refState: EndState = pair ? (pair.ref.granules.length > 0 ? { s: "ready", variants: pair.ref.granules } : { s: "none", text: `no backscatter acquisition found for ${pair.ref.epoch} UTC` }) : pairError ? { s: "none", text: `preview unavailable: ${pairError}` } : { s: "loading" };
  const secState: EndState = pair ? (pair.sec.granules.length > 0 ? { s: "ready", variants: pair.sec.granules } : { s: "none", text: `no backscatter acquisition found for ${pair.sec.epoch} UTC` }) : pairError ? { s: "none", text: `preview unavailable: ${pairError}` } : { s: "loading" };

  const leftState = swapped ? secState : refState;
  const rightState = swapped ? refState : secState;
  const leftSel = swapped ? secSel : refSel;
  const rightSel = swapped ? refSel : secSel;
  const setLeftSel = swapped ? setSecSel : setRefSel;
  const setRightSel = swapped ? setRefSel : setSecSel;
  const leftLabel = swapped ? "after" : "before";
  const rightLabel = swapped ? "before" : "after";
  const leftEpoch = (swapped ? pair?.sec.epoch : pair?.ref.epoch) ?? "—";
  const rightEpoch = (swapped ? pair?.ref.epoch : pair?.sec.epoch) ?? "—";

  const maxPx = 384;
  const note = useMemo(() => pair?.note, [pair]);

  const pane = (state: EndState, label: string, epoch: string, sel: string | null, onSel: (id: string) => void, paneKey: "L" | "R", paneBox: [number, number, number, number]) => {
    const granule = state.s === "ready" ? state.variants.find((v) => v.id === sel) ?? state.variants[0] : null;
    return (
      <div className="flex min-h-0 flex-1 flex-col gap-1">
        {state.s === "ready" && state.variants.length > 1 && (
          <label className="flex items-center gap-1 text-[10px] text-[#8b949e]">
            variant (resolution comparison)
            <select
              value={granule?.id ?? ""}
              onChange={(ev) => onSel(ev.target.value)}
              className="max-w-[200px] rounded border border-[#333b47] bg-[#0e1116] px-1 py-0.5 font-mono text-[10px] text-[#e6eaee]"
              aria-label={`${label} variant`}
            >
              {state.variants.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.range_bandwidth_mhz != null ? `${v.range_bandwidth_mhz} MHz` : "bandwidth —"} · {v.crid ?? "CRID —"} · v{v.version ?? "—"}
                </option>
              ))}
            </select>
          </label>
        )}
        <PreviewPane
          granule={granule}
          layer={layer}
          box={paneBox}
          maxPx={maxPx}
          cmap={cmap}
          scale={scale}
          synced={synced}
          onZoom={(fx, fy, f) => onZoom(paneKey, fx, fy, f)}
          onPan={(dx, dy) => onPan(paneKey, dx, dy)}
          title={`${label} · ${epoch} UTC`}
          emptyText={state.s === "none" ? state.text : undefined}
        />
      </div>
    );
  };

  return (
    <section className="flex min-h-0 flex-1 flex-col p-3" aria-label="compare the two ends of a pair">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <h3 className="text-sm font-semibold text-[#f2efe9]">Compare the two ends</h3>
        <span className="font-mono text-[10px] text-[#8b949e]">{group.granule_ids[0].slice(0, 34)}…</span>
        <button
          type="button"
          onClick={toggleSync}
          aria-pressed={synced}
          className="flex items-center gap-1 rounded border border-[#333b47] px-2 py-1 text-[10px] text-[#aab3bd] hover:border-[#4a5462]"
        >
          {synced ? <Link2 size={11} aria-hidden /> : <Link2Off size={11} aria-hidden />}
          {synced ? "zoom synced" : "zoom unsynced"}
        </button>
        <button
          type="button"
          onClick={() => setSwapped((s) => !s)}
          className="flex items-center gap-1 rounded border border-[#333b47] px-2 py-1 text-[10px] text-[#aab3bd] hover:border-[#4a5462]"
        >
          <ArrowLeftRight size={11} aria-hidden /> swap
        </button>
        <label className="flex items-center gap-1 text-[10px] text-[#8b949e]">
          layer (both panes)
          <select
            value={layer ?? ""}
            onChange={(ev) => setLayer(ev.target.value)}
            className="max-w-[220px] rounded border border-[#333b47] bg-[#0e1116] px-1 py-0.5 font-mono text-[10px] text-[#e6eaee]"
            aria-label="layer shown in both panes"
          >
            {(layers ?? []).map((l) => (
              <option key={l.key} value={l.key}>{l.label}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1 text-[10px] text-[#8b949e]">
          cmap
          <select
            value={cmap}
            onChange={(ev) => setCmap(ev.target.value)}
            className="rounded border border-[#333b47] bg-[#0e1116] px-1 py-0.5 font-mono text-[10px] text-[#e6eaee]"
            aria-label="colour map for both panes"
          >
            {CMAPS.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1 text-[10px] text-[#8b949e]">
          scale
          <select
            value={scale}
            onChange={(ev) => setScale(ev.target.value)}
            className="rounded border border-[#333b47] bg-[#0e1116] px-1 py-0.5 font-mono text-[10px] text-[#e6eaee]"
            aria-label="value scale for both panes"
          >
            {SCALES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <button
          type="button"
          onClick={() => setViews({ L: box, R: box })}
          className="rounded border border-[#333b47] px-2 py-1 text-[10px] text-[#aab3bd] hover:border-[#4a5462]"
        >
          reset view
        </button>
        <button
          type="button"
          onClick={onClose}
          aria-label="close compare view"
          className="ml-auto rounded p-1 text-[#8b949e] hover:bg-[#1c222b] hover:text-[#e6eaee]"
        >
          <X size={14} aria-hidden />
        </button>
      </div>

      {layersError && (
        <p role="alert" className="mb-2 border border-[#4a2f24] bg-[#2a1712] px-2 py-1 text-[10px] text-[#e8a087]">
          {layersError}
        </p>
      )}
      {note && <p className="mb-1 text-[10px] text-[#5a636e]">{note}</p>}
      <div className="mb-2 flex gap-1 text-[10px] text-[#8b949e]" aria-label="pan controls">
        <button type="button" onClick={() => onPan("L", -0.2, 0)} className="rounded border border-[#333b47] px-2 py-0.5 hover:border-[#4a5462]">← pan</button>
        <button type="button" onClick={() => onPan("L", 0.2, 0)} className="rounded border border-[#333b47] px-2 py-0.5 hover:border-[#4a5462]">pan →</button>
        <button type="button" onClick={() => onPan("L", 0, 0.2)} className="rounded border border-[#333b47] px-2 py-0.5 hover:border-[#4a5462]">↑ pan</button>
        <button type="button" onClick={() => onPan("L", 0, -0.2)} className="rounded border border-[#333b47] px-2 py-0.5 hover:border-[#4a5462]">↓ pan</button>
        <span className="ml-2 font-mono">box {views.L.map((v) => v.toFixed(2)).join(", ")}</span>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-2 lg:flex-row">
        {pane(leftState, leftLabel, leftEpoch, leftSel, setLeftSel, "L", views.L)}
        {pane(rightState, rightLabel, rightEpoch, rightSel, setRightSel, "R", views.R)}
      </div>
      {!synced && (
        <p className="mt-1 text-[10px] text-[#5a636e]">
          zoom is unsynced: each pane keeps its own box. Re-syncing adopts the left pane&apos;s box
          on both sides.
        </p>
      )}
    </section>
  );
}
