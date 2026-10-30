"use client";

/** ExplorerDetail (§7.8 + UI-build-2 §4.3) — the selected acquisition's tabbed detail panel:
 *  Summary (decoded fields vs raw tokens, flags, footprint mini-map, the §4.1 GUNW/GCOV product
 *  story with the before/after backscatter offer) · Layers (the HDF5 layer inventory from the
 *  engine) · Timeline (this stack's epochs and day gaps with the selection highlighted) ·
 *  Data parts (what a scan would request vs what exists, engine-computed). Unknown = "—";
 *  undecoded tokens stay raw with an "undecoded" tag. */
import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Check, Copy, ImageOff, X } from "lucide-react";
import { api } from "@/lib/api";
import { bg } from "@/lib/bg";
import { GateError } from "@/lib/bgApi";
import { useGateFail, useGateRetryNonce } from "@/components/background/BackgroundGate";
import type {
  BackgroundGroup, ExplorerGranule, ExplorerStack, GranuleDetailResponse, StackPairResponse,
} from "@/lib/types";
import { directionLabel } from "@/lib/status";

/** maplibre loads client-side only; the Background route already carries the chunk */
const FootprintMap = dynamic(() => import("./FootprintMiniMap"), {
  ssr: false,
  loading: () => <p className="p-3 text-[11px] text-[#8b949e]">footprint map…</p>,
});

type Tab = "summary" | "layers" | "timeline" | "data";

const TABS: { key: Tab; label: string }[] = [
  { key: "summary", label: "Summary" },
  { key: "layers", label: "Layers" },
  { key: "timeline", label: "Timeline" },
  { key: "data", label: "Data parts" },
];

function Row({ k, v, mono = true, tag }: { k: string; v: React.ReactNode; mono?: boolean; tag?: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-[#1c222b] py-1">
      <dt className="shrink-0 text-[#8b949e]">{k}</dt>
      <dd className={`text-right text-[#e6eaee] ${mono ? "font-mono text-[11px]" : "text-xs"} break-all`}>
        {v ?? "—"}
        {tag && <span className="ml-1 rounded border border-[#4a3a1f] px-1 py-px text-[9px] uppercase text-[#e6c977]">{tag}</span>}
      </dd>
    </div>
  );
}

export default function ExplorerDetail({ granule, group, stack, stackGranules, bbox, engineOnline, onClose, onShowOnGlobe, onScanTile, onCompare }: {
  granule: ExplorerGranule | null;
  group: BackgroundGroup | null;
  stack: ExplorerStack | null;
  stackGranules: ExplorerGranule[];
  bbox: [number, number, number, number] | null;
  engineOnline: boolean;
  onClose: () => void;
  onShowOnGlobe: (lat: number, lon: number) => void;
  onScanTile: (tileId: string) => void;
  /** §4.4: open the Compare view for this pair's end */
  onCompare: (group: BackgroundGroup, end: "ref" | "sec") => void;
}) {
  const gateFail = useGateFail();
  const retryNonce = useGateRetryNonce();
  const [tab, setTab] = useState<Tab>("summary");
  const [copied, setCopied] = useState(false);
  const [tileId, setTileId] = useState<string | null>(null);
  const [tileError, setTileError] = useState<string | null>(null);
  const [detail, setDetail] = useState<GranuleDetailResponse | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [variantId, setVariantId] = useState<string | null>(null);
  const [pair, setPair] = useState<StackPairResponse | null>(null);
  const [pairState, setPairState] = useState<"idle" | "loading" | "error">("idle");
  const [pairError, setPairError] = useState<string | null>(null);
  const reqRef = useRef<AbortController | null>(null);

  const selectedVariant = useMemo(() => {
    if (!group || !variantId) return null;
    return group.variants.find((v) => v.id === variantId) ?? null;
  }, [group, variantId]);

  // the engine's granule detail (decoded + inventory) — loaded per selected variant
  useEffect(() => {
    reqRef.current?.abort();
    setDetail(null);
    setDetailError(null);
    setPair(null);
    setPairState("idle");
    if (!granule || !engineOnline) return;
    const ctrl = new AbortController();
    reqRef.current = ctrl;
    bg.granule(granule.id, ctrl.signal)
      .then((d) => !ctrl.signal.aborted && setDetail(d))
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        if (err instanceof GateError) { gateFail(err.kind); return; }
        setDetailError(err instanceof Error ? err.message : String(err));
      });
    return () => ctrl.abort();
  }, [granule?.id, engineOnline, gateFail, granule, retryNonce]);

  useEffect(() => {
    if (group) setVariantId(group.variants[0]?.id ?? null);
    else setVariantId(null);
  }, [group?.key, group]);

  // resolve the containing MGRS tile through the ENGINE (the client never inverts ids)
  useEffect(() => {
    if (!granule) return;
    const lon = granule.footprint
      ? granule.footprint.reduce((acc, p) => acc + p[0], 0) / granule.footprint.length
      : null;
    const lat = granule.footprint
      ? granule.footprint.reduce((acc, p) => acc + p[1], 0) / granule.footprint.length
      : null;
    if (lon === null || lat === null || !engineOnline) {
      setTileId(null);
      return;
    }
    const ctrl = new AbortController();
    setTileError(null);
    api.tileAt(lon, lat)
      .then((r) => setTileId(r.tile.id))
      .catch((e) => (ctrl.signal.aborted ? undefined : setTileError(e instanceof Error ? e.message : String(e))));
    return () => ctrl.abort();
  }, [granule, engineOnline]);

  const copyName = () => {
    if (!granule) return;
    void navigator.clipboard?.writeText(granule.id).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  };

  // §4.1: the before/after backscatter offer (only for pair groups; the engine answers)
  const loadPair = () => {
    if (!group || group.kind !== "pair") return;
    setPairState("loading");
    setPairError(null);
    bg.stackPair(group.granule_ids[0])
      .then((r) => {
        setPair(r);
        setPairState("idle");
      })
      .catch((err) => {
        if (err instanceof GateError) { gateFail(err.kind); return; }
        setPairError(err instanceof Error ? err.message : String(err));
        setPairState("error");
      });
  };

  const stackColor = stack?.direction === "D" ? "#b39ce8" : "#3fb6c9";
  const dirChip = useMemo(() => {
    if (!granule) return null;
    const label = directionLabel(granule.direction);
    const cls = granule.direction === "D"
      ? "border-[#b39ce8]/50 bg-[#241f33] text-[#cdbdf0]"
      : granule.direction === "A"
        ? "border-[#3fb6c9]/50 bg-[#12262c] text-[#7fd8e6]"
        : "border-[#333b47] bg-[#161b22] text-[#8b949e]";
    return <span className={`rounded border px-1.5 py-0.5 text-[10px] ${cls}`}>{label}</span>;
  }, [granule]);

  if (!granule) {
    return (
      <div className="p-4 text-[11px] leading-relaxed text-[#8b949e]">
        select a bar in the timeline (or a row in List view) to see the acquisition&apos;s real
        metadata: the full file name, track and frame, reference and secondary dates, baseline,
        polarization, bandwidth, collection, CRID, the HDF5 layer inventory, this stack&apos;s
        timeline, and what a scan would fetch — every field the engine actually knows, with
        undecoded tokens shown raw.
      </div>
    );
  }

  const center = granule.footprint && granule.footprint.length > 0
    ? {
        lon: granule.footprint.reduce((acc, p) => acc + p[0], 0) / granule.footprint.length,
        lat: granule.footprint.reduce((acc, p) => acc + p[1], 0) / granule.footprint.length,
      }
    : null;

  const shown = selectedVariant ?? granule;
  const isPair = (group?.kind ?? (shown.ref_time && shown.sec_time ? "pair" : "single")) === "pair";

  return (
    <div className="p-3 text-xs">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${stackColor === "#3fb6c9" ? "border-[#3fb6c9]/50 bg-[#12262c] text-[#7fd8e6]" : "border-[#b39ce8]/50 bg-[#241f33] text-[#cdbdf0]"}`}>
            {shown.stack}
          </span>
          {dirChip}
          <span className="rounded border border-[#333b47] px-1.5 py-0.5 font-mono text-[10px] text-[#aab3bd]">
            {shown.product ?? "—"}{isPair ? " · pair" : " · single date"}
          </span>
          {(group?.n_variants ?? 1) > 1 && (
            <span className="rounded border border-[#4a3a1f] bg-[#2a2212] px-1.5 py-0.5 font-mono text-[10px] text-[#e6c977]" title="engine-grouped resolution variants">
              {group!.n_variants} variants
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="clear selection"
          className="rounded p-1 text-[#8b949e] hover:bg-[#1c222b] hover:text-[#e6eaee]"
        >
          <X size={13} aria-hidden />
        </button>
      </div>

      {/* tabs (§4.3) */}
      <div role="tablist" aria-label="detail sections" className="mb-2 flex gap-1 border-b border-[#262c36]">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`rounded-t px-2.5 py-1 text-[11px] ${tab === t.key
              ? "border-x border-t border-[#262c36] bg-[#12161d] font-medium text-[#e6eaee]"
              : "text-[#8b949e] hover:text-[#d9dee4]"}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* variant picker (§4.2) — never merges, never hides a variant */}
      {group && group.n_variants > 1 && (
        <div className="mb-2 rounded-md border border-[#4a3a1f] bg-[#1d1810] p-2">
          <p className="mb-1 text-[10px] font-medium text-[#e6c977]">
            {group.n_variants} resolution variants — same acquisition, differing in{" "}
            {group.variant_facts.join(", ") || "(the engine listed no differing facts)"}:
          </p>
          <ul className="space-y-0.5">
            {group.variants.map((v) => (
              <li key={v.id}>
                <label className="flex cursor-pointer items-center gap-2 text-[10px]">
                  <input
                    type="radio"
                    name="variant"
                    checked={v.id === (variantId ?? group.variants[0]?.id)}
                    onChange={() => setVariantId(v.id)}
                  />
                  <span className="font-mono text-[#cfd6dc]">
                    {v.range_bandwidth_mhz != null ? `${v.range_bandwidth_mhz} MHz` : "bandwidth —"}
                    {" · "}{v.crid ?? "CRID —"}{" · v"}{v.version ?? "—"}
                    {v.collection ? ` · ${v.collection}` : ""}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </div>
      )}

      {tab === "summary" && (
        <>
          {/* full file name + copy */}
          <div className="rounded-md border border-[#262c36] bg-[#0e1116] p-2">
            <p className="break-all font-mono text-[10.5px] leading-relaxed text-[#cfd6dc]">{shown.id}</p>
            <button
              type="button"
              onClick={copyName}
              className="mt-1.5 flex items-center gap-1 rounded border border-[#333b47] px-2 py-1 text-[10px] text-[#aab3bd] hover:border-[#3fb6c9] hover:text-[#7fd8e6]"
            >
              {copied ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />} {copied ? "copied" : "copy file name"}
            </button>
          </div>

          {/* footprint mini-map: the whole stack's footprints, the selection highlighted, box amber */}
          <div className="mt-2 overflow-hidden rounded-md border border-[#262c36]">
            <FootprintMap
              bbox={bbox}
              stackRings={stackGranules.map((g) => ({ id: g.id, ring: g.footprint }))}
              selectedId={shown.id}
            />
          </div>

          {/* key/value table */}
          <dl className="mt-2">
            <Row k="Level" v={shown.product} />
            <Row k="Direction" v={directionLabel(shown.direction)} mono={false} />
            <Row k="Track / frame" v={shown.track != null && shown.frame != null ? `${shown.track} / ${shown.frame}` : shown.track ?? shown.frame ?? null} />
            {isPair ? (
              <>
                <Row
                  k={group?.flags.some((f) => f.includes("flipped")) ? "Earlier date" : "Reference"}
                  v={shown.ref_time ? `${shown.ref_time} UTC` : null}
                  tag={shown.cycle_ref != null ? `cycle ${shown.cycle_ref}` : undefined}
                />
                <Row
                  k={group?.flags.some((f) => f.includes("flipped")) ? "Later date" : "Secondary"}
                  v={shown.sec_time ? `${shown.sec_time} UTC` : null}
                  tag={shown.cycle_sec != null ? `cycle ${shown.cycle_sec}` : undefined}
                />
                <Row k="Baseline" v={shown.baseline_days != null ? `${shown.baseline_days} d` : null} />
              </>
            ) : (
              <Row k="Acquired" v={shown.start ? `${shown.start} UTC` : null} />
            )}
            <Row k="Polarization" v={shown.polarization} />
            <Row k="Range bandwidth" v={shown.range_bandwidth_mhz != null ? `${shown.range_bandwidth_mhz} MHz` : null} />
            <Row k="Collection" v={shown.collection} />
            <Row k="Release (CRID)" v={shown.crid} />
            <Row
              k="Orbit accuracy"
              v={
                shown.orbit_accuracy.asf || shown.orbit_accuracy.filename
                  ? `ASF: ${shown.orbit_accuracy.asf ?? "—"}; filename: ${shown.orbit_accuracy.filename ?? "—"}`
                  : null
              }
              tag={shown.flags.some((f) => f.includes("orbit token")) ? "undecoded" : undefined}
            />
            <Row k="Size" v={shown.size_bytes != null ? `${(shown.size_bytes / 1e9).toFixed(2)} GB` : null} />
            <Row k="Version" v={shown.version} />
            {(shown.flags.length > 0 || (group?.flags.length ?? 0) > 0) && (
              <Row
                k="Flags"
                mono={false}
                v={(
                  <ul className="space-y-0.5 text-left">
                    {[...group?.flags ?? [], ...shown.flags].map((f, i) => (
                      <li key={`${i}-${f}`} className="text-[10px] text-[#d9c98a]">{f}</li>
                    ))}
                  </ul>
                )}
              />
            )}
          </dl>

          {/* §4.1: the GUNW/GCOV product story */}
          {isPair ? (
            <div className="mt-2 rounded-md border border-[#262c36] bg-[#12161d] p-2 text-[10.5px] leading-relaxed text-[#8b949e]">
              <p>
                <strong className="text-[#cfd6dc]">A {shown.product ?? "GUNW"} is not two images.</strong>{" "}
                It holds interferometric layers — unwrapped phase, coherence, connected-component
                masks, pixel offsets — computed between two dates. Its two ends are two dates.
              </p>
              <p className="mt-1">
                The backscatter pictures for those dates live in GCOV granules.{" "}
                {pairState === "loading" && <span role="status">asking the engine…</span>}
                {pairState === "error" && <span role="alert" className="text-[#e8a087]">{pairError}</span>}
                {pair && (
                  <>
                    {pair.ref.granules.length > 0
                      ? <span className="text-[#8fd0b8]">{pair.ref.granules.length} GCOV at the earlier date</span>
                      : <span>no backscatter acquisition found for {pair.ref.epoch} UTC</span>}
                    {" · "}
                    {pair.sec.granules.length > 0
                      ? <span className="text-[#8fd0b8]">{pair.sec.granules.length} GCOV at the later date</span>
                      : <span>no backscatter acquisition found for {pair.sec.epoch} UTC</span>}
                  </>
                )}
              </p>
              <div className="mt-1.5 flex flex-wrap gap-2">
                {!pair && pairState !== "loading" && (
                  <button
                    type="button"
                    onClick={loadPair}
                    disabled={!engineOnline}
                    className="rounded border border-[#3fb6c9] px-2 py-1 text-[10px] text-[#7fd8e6] hover:bg-[#12262c] disabled:opacity-40"
                  >
                    check for before/after backscatter
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => group && onCompare(group, "ref")}
                  disabled={!engineOnline || !group}
                  className="rounded border border-[#3fb6c9] px-2 py-1 text-[10px] text-[#7fd8e6] hover:bg-[#12262c] disabled:opacity-40"
                >
                  compare the two ends
                </button>
              </div>
            </div>
          ) : (
            <div className="mt-2 rounded-md border border-[#262c36] bg-[#12161d] p-2 text-[10.5px] leading-relaxed text-[#8b949e]">
              <p>
                <ImageOff size={11} aria-hidden className="mr-1 inline" />
                <strong className="text-[#cfd6dc]">A {shown.product ?? "GCOV"} is one acquisition</strong>{" "}
                — one date&apos;s backscatter picture, no pair, no baseline.
              </p>
            </div>
          )}

          {/* buttons: viewer + bridge only (DEC14) */}
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={!center}
              onClick={() => center && onShowOnGlobe(center.lat, center.lon)}
              className="rounded-md border border-[#3fb6c9] px-2.5 py-1.5 text-[11px] text-[#7fd8e6] hover:bg-[#12262c] disabled:cursor-not-allowed disabled:opacity-40"
            >
              Show on globe
            </button>
            <button
              type="button"
              disabled={!tileId || !engineOnline}
              title={!engineOnline ? "the engine is offline — the tile cannot be resolved" : !tileId ? (tileError ?? "resolving the containing tile…") : `open tile ${tileId} on the globe (nothing is scanned automatically)`}
              onClick={() => tileId && onScanTile(tileId)}
              className="rounded-md border border-[#2c5f58] bg-[#12241f] px-2.5 py-1.5 text-[11px] text-[#8fd0b8] hover:bg-[#17312a] disabled:cursor-not-allowed disabled:opacity-40"
            >
              {tileId ? `Scan this tile (${tileId})` : "Scan this tile"}
            </button>
          </div>
          <p className="mt-1.5 text-[10px] leading-relaxed text-[#5a636e]">
            the footprint outline is the granule&apos;s imaged outer ring (ASF), not the
            satellite&apos;s trajectory. Previews are fetched click-by-click, clipped to the
            requested box, never prefetched and never cached beyond the engine&apos;s own store.
          </p>
        </>
      )}

      {tab === "layers" && (
        <div>
          {detailError && (
            <p role="alert" className="mb-2 border border-[#4a2f24] bg-[#2a1712] px-2 py-1 text-[10px] text-[#e8a087]">
              {detailError}
            </p>
          )}
          {!detail && !detailError && engineOnline && (
            <p role="status" className="text-[10px] text-[#8b949e]" aria-live="polite">
              loading the layer inventory from the engine…
            </p>
          )}
          {detail?.inventory && (
            <>
              <p className="mb-1 text-[10px] text-[#8b949e]">
                {detail.inventory.n_layers} layers across {detail.inventory.grids.length} grids ·
                source: <span className="font-mono">{detail.inventory.source}</span>
                {detail.inventory_note && <> · {detail.inventory_note}</>}
              </p>
              {detail.inventory.grids.map((grid) => (
                <div key={grid.key} className="mb-2 rounded-md border border-[#262c36] bg-[#0e1116] p-2">
                  <p className="break-all font-mono text-[10px] text-[#7fd8e6]">{grid.key}</p>
                  <p className="text-[10px] text-[#8b949e]">
                    EPSG:{grid.epsg ?? "—"} · {grid.nx}×{grid.ny} px
                    {grid.dx != null && ` · ${grid.dx.toFixed(0)} m`}
                    {grid.extras.length > 0 && ` · extra axes: ${grid.extras.map((e) => e.name).join(", ")}`}
                  </p>
                  <table className="mt-1 w-full text-left text-[10px]">
                    <thead className="text-[#8b949e]">
                      <tr><th className="py-0.5 pr-2 font-normal">layer</th><th className="py-0.5 pr-2 font-normal">shape</th><th className="py-0.5 font-normal">dtype</th></tr>
                    </thead>
                    <tbody className="font-mono text-[#cfd6dc]">
                      {grid.layers.map((l) => (
                        <tr key={l.key} className="border-t border-[#1c222b]">
                          <td className="break-all py-0.5 pr-2">{l.path.split("/").slice(-2).join("/")}{!l.readable && <span className="ml-1 text-[#d9c98a]">({l.why_not ?? "not readable"})</span>}</td>
                          <td className="py-0.5 pr-2">{l.shape.join("×")}</td>
                          <td className="py-0.5">{l.dtype}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </>
          )}
          {engineOnline && detail === null && detailError === null && (
            <p className="text-[10px] text-[#5a636e]">the inventory arrives from the engine&apos;s layout index (metadata only).</p>
          )}
          {!engineOnline && <p className="text-[10px] text-[#e8a087]">the engine is offline — no inventory.</p>}
        </div>
      )}

      {tab === "timeline" && (
        <div>
          {stack ? (
            <>
              <p className="mb-1 text-[10px] text-[#8b949e]">
                stack <span className="font-mono text-[#cfd6dc]">{stack.key}</span> ·{" "}
                {stack.n_epochs} distinct epochs · {stack.n_granules} granules
                {stack.min_gap_days != null && (
                  <> · gaps min/med/max {stack.min_gap_days}/{stack.median_gap_days}/{stack.max_gap_days} d</>
                )}
                {stack.n_epochs <= 1 && <> · single-epoch stack: no gaps to show (honest empty)</>}
              </p>
              <ol className="space-y-1">
                {stack.epochs.map((e, i) => {
                  const gap = stack.gaps_days[i - 1];
                  const isSel = shown.ref_time === e || shown.sec_time === e || shown.start === e;
                  return (
                    <li key={e} className={`flex items-baseline gap-2 rounded px-1.5 py-0.5 text-[10px] ${isSel ? "bg-[#2a2212] text-[#e6c977]" : "text-[#8b949e]"}`}>
                      <span className="font-mono">{e} UTC</span>
                      {gap != null && <span>· +{gap} d</span>}
                      {isSel && <span className="ml-auto uppercase">selected acquisition</span>}
                    </li>
                  );
                })}
              </ol>
              {isPair && group && (
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => onCompare(group, "ref")}
                    disabled={!engineOnline}
                    className="rounded border border-[#3fb6c9] px-2 py-1 text-[10px] text-[#7fd8e6] hover:bg-[#12262c] disabled:opacity-40"
                  >
                    click the reference end → compare ({shown.ref_time ?? "—"})
                  </button>
                  <button
                    type="button"
                    onClick={() => onCompare(group, "sec")}
                    disabled={!engineOnline}
                    className="rounded border border-[#3fb6c9] px-2 py-1 text-[10px] text-[#7fd8e6] hover:bg-[#12262c] disabled:opacity-40"
                  >
                    click the secondary end → compare ({shown.sec_time ?? "—"})
                  </button>
                </div>
              )}
            </>
          ) : (
            <p className="text-[10px] text-[#8b949e]">no stack summary for this selection.</p>
          )}
        </div>
      )}

      {tab === "data" && (
        <div>
          <p className="mb-1 text-[10px] leading-relaxed text-[#8b949e]">
            What a scan of this tile would request (the chain&apos;s own layer request, computed by
            the engine) vs what this granule actually holds.
          </p>
          {detailError && <p role="alert" className="mb-2 text-[10px] text-[#e8a087]">{detailError}</p>}
          {detail?.inventory?.scan_request_regex != null && (
            <p className="mb-1 break-all font-mono text-[9.5px] text-[#5a636e]">
              request: {detail.inventory.scan_request_regex}
            </p>
          )}
          {detail?.inventory && (
            <p className="mb-1 text-[10px] text-[#8b949e]">
              {detail.inventory.n_layers_requested_by_scan ?? "—"} of {detail.inventory.n_layers} layers would be fetched by a scan
              {detail.granule.size_bytes != null && <> · granule size {(detail.granule.size_bytes / 1e9).toFixed(2)} GB (wire)</>}
            </p>
          )}
          <table className="w-full text-left text-[10px]">
            <thead className="text-[#8b949e]">
              <tr><th className="py-0.5 pr-2 font-normal">layer</th><th className="py-0.5 pr-2 font-normal">exists</th><th className="py-0.5 font-normal">scan requests</th></tr>
            </thead>
            <tbody className="font-mono text-[#cfd6dc]">
              {(detail?.inventory?.grids ?? []).flatMap((g) => g.layers.map((l) => (
                <tr key={l.key} className="border-t border-[#1c222b]">
                  <td className="break-all py-0.5 pr-2">{l.path.split("/").slice(-2).join("/")}</td>
                  <td className="py-0.5 pr-2">{l.readable ? "yes" : "no"}</td>
                  <td className="py-0.5">{l.requested_by_scan ? "yes" : "no"}</td>
                </tr>
              )))}
            </tbody>
          </table>
          {!detail && !detailError && engineOnline && (
            <p role="status" className="text-[10px] text-[#8b949e]" aria-live="polite">loading…</p>
          )}
        </div>
      )}
    </div>
  );
}
