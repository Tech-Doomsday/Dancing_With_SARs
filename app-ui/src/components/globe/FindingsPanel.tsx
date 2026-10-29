"use client";

/** Results panel (§7.4 item 4): findings grouped by the ENGINE's own tier order, each finding a
 *  card with overlay chips, per-layer opacity, Detections (click -> fitBounds + outline), and
 *  expandable Evidence. One LayerSource interface feeds both live runs and cached verdicts. */
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import StatusBadge from "@/components/StatusBadge";
import { EvidenceList, WarningsPanel } from "@/components/VerdictCard";
import { fmtUtc, timeAgo } from "@/lib/format";
import type { PipelineResult, LayerPayload } from "@/lib/types";

export type LayerSource = (pipeline: string, layer: string) => Promise<LayerPayload>;

export interface FindingsPanelProps {
  results: PipelineResult[];
  title: string;
  sourceBadge: string;
  scanTimeIso: string | null;
  tile: string | null;
  bundleId: string | null;
  runId: string | null;
  loadLayer: LayerSource;
  cacheReport?: { enabled: boolean; written: number; skipped: { pipeline: string | null; reason: string }[]; failed: string[]; reason?: string } | null;
  selectedPipeline: string | null;
  onSelectPipeline: (pipeline: string | null) => void;
  activeOverlays: Record<string, { pipeline: string; layer: string; opacity: number }>;
  onToggleOverlay: (pipeline: string, layer: string, kind: string, on: boolean) => void;
  onOverlayOpacity: (pipeline: string, layer: string, opacity: number) => void;
  onSelectDetection: (detection: { bbox: [number, number, number, number]; id: string }) => void;
  selectedDetectionId: string | null;
  linkFullReport: string | null;
}

const TIER_ORDER = ["supported", "candidate", "not_supported", "insufficient", "quiet"];

export default function FindingsPanel(props: FindingsPanelProps) {
  const grouped = useMemo(() => {
    const main: Record<string, PipelineResult[]> = {};
    const other: Record<string, PipelineResult[]> = {};
    for (const r of props.results) {
      // tier comes FROM THE ENGINE (D4); the UI never re-derives it from status
      const tier = (r as PipelineResult & { tier?: string }).tier ?? "insufficient";
      if (TIER_ORDER.includes(tier)) (main[tier] ??= []).push(r);
      else if (tier === "not_applicable") (other["Did not apply here"] ??= []).push(r);
      else if (tier === "pending") (other["Not run"] ??= []).push(r);
      else if (tier === "not_modelled") (other["Not modelled yet"] ??= []).push(r);
      else (other["Other"] ??= []).push(r);
    }
    return { main, other };
  }, [props.results]);

  return (
    <div className="space-y-3 p-3">
      <div className="rounded-lg border border-[#e7e2da] bg-white p-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-sm font-semibold">{props.title}</h3>
          <span className="rounded-full border border-[#2c5f58]/40 bg-[#eff5f3] px-2 py-0.5 text-[10px] font-medium text-[#2c5f58]">
            {props.sourceBadge}
          </span>
        </div>
        <p className="mono mt-0.5 text-[11px] text-[#4a443c]">
          tile {props.tile ?? "?"}
          {props.scanTimeIso && <> · scanned {timeAgo(props.scanTimeIso)} ({fmtUtc(props.scanTimeIso)})</>}
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
          {props.bundleId && (
            <button
              onClick={() => void navigator.clipboard?.writeText(props.bundleId as string)}
              className="mono rounded border border-[#e7e2da] px-1.5 py-0.5 text-[10px] text-[#8a837b] hover:border-[#2c5f58]"
              title="copy bundle id"
            >
              bundle {props.bundleId.slice(0, 14)}… ⧉
            </button>
          )}
          {props.linkFullReport && (
            <Link href={props.linkFullReport} className="text-[#2c5f58] underline">
              Full report
            </Link>
          )}
        </div>
        {props.cacheReport && !props.cacheReport.enabled && (
          <p role="status" className="mt-2 rounded border border-[#b08d57]/40 bg-[#f7f2e7] px-2 py-1 text-[10px] text-[#7a6438]">
            computed but NOT cached: {props.cacheReport.reason}
          </p>
        )}
        {props.cacheReport && props.cacheReport.enabled && props.cacheReport.failed.length > 0 && (
          <p role="status" className="mt-2 rounded border border-[#b08d57]/40 bg-[#f7f2e7] px-2 py-1 text-[10px] text-[#7a6438]">
            computed but NOT cached: {props.cacheReport.failed[0]}
          </p>
        )}
      </div>

      {TIER_ORDER.filter((t) => grouped.main[t]?.length).map((tier) => (
        <section key={tier}>
          <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[#8a837b]">{tier}</h4>
          <div className="space-y-2">
            {grouped.main[tier].map((r) => (
              <FindingCard key={r.pipeline} result={r} {...props} />
            ))}
          </div>
        </section>
      ))}
      {Object.entries(grouped.other).map(([label, results]) => (
        <details key={label} className="rounded-lg border border-[#e7e2da] bg-white p-2">
          <summary className="cursor-pointer text-[11px] font-semibold text-[#8a837b]">
            {label} ({results.length})
          </summary>
          <ul className="mt-1.5 space-y-1">
            {results.map((r) => (
              <li key={r.pipeline} className="flex items-center justify-between text-[11px] text-[#4a443c]">
                <span className="mono">{r.pipeline}</span>
                <span className="text-[10px] text-[#8a837b]">
                  {(r as PipelineResult & { tier_label?: string }).tier_label ?? r.status.replace(/_/g, " ").toLowerCase()}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ))}
    </div>
  );
}

function FindingCard(props: FindingsPanelProps & { result: PipelineResult }) {
  const r = props.result;
  const [openEvidence, setOpenEvidence] = useState(false);
  const detail = r.detail as {
    narrative?: { headline?: string; reliability?: string; note?: string };
    detections?: { id: string; label: string; bbox_lonlat: [number, number, number, number]; layer: string | null }[];
    detections_truncated?: boolean;
  };
  const rasterLayers = (r.layers ?? []).filter((l) => (l as { kind?: string }).kind === "raster");
  const vectorLayers = (r.layers ?? []).filter((l) => (l as { kind?: string }).kind !== "raster");
  const rawP = r.raw_p;
  return (
    <article
      className={`rounded-lg border bg-white p-3 ${
        props.selectedPipeline === r.pipeline ? "border-[#2c5f58]" : "border-[#e7e2da]"
      }`}
      onMouseEnter={() => props.onSelectPipeline(r.pipeline)}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="mono text-[11px] font-semibold text-[#1c1a17]">{r.pipeline}</p>
          <p className="mt-0.5 text-xs text-[#4a443c]">
            {detail.narrative?.headline || r.summary || "—"}
          </p>
        </div>
        <StatusBadge
          status={r.status}
          tier={(r as PipelineResult & { tier?: string }).tier ?? null}
          label={(r as PipelineResult & { tier_label?: string }).tier_label}
          compact
        />
      </div>
      {rawP != null && (
        <div className="mt-1.5">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-[#f0ece4]">
            <div className="h-full rounded-full bg-[#b08d57]" style={{ width: `${Math.round(rawP * 100)}%` }} />
          </div>
          <p className="mt-0.5 text-[10px] text-[#8a837b]">
            rank score {Math.round(rawP * 100)}/100 — within-tier rank score, NOT a probability
          </p>
        </div>
      )}
      {/* layer chips from result.layers (kind raster shown; vector noted, not displayable) */}
      {rasterLayers.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {rasterLayers.map((l) => {
            const name = (l as { name?: string }).name ?? "?";
            const on = Boolean(props.activeOverlays[`${r.pipeline}:${name}`]);
            return (
              <span key={name} className="inline-flex items-center overflow-hidden rounded-md border border-[#e7e2da]">
                <button
                  onClick={() => props.onToggleOverlay(r.pipeline, name, "raster", !on)}
                  aria-pressed={on}
                  className={`px-2 py-0.5 text-[10px] ${on ? "bg-[#2c5f58] text-white" : "bg-white text-[#4a443c] hover:border-[#2c5f58]"}`}
                >
                  {name}
                </button>
                {on && (
                  <input
                    type="range"
                    min={0.2}
                    max={1}
                    step={0.05}
                    value={props.activeOverlays[`${r.pipeline}:${name}`]?.opacity ?? 0.8}
                    onChange={(e) => props.onOverlayOpacity(r.pipeline, name, Number(e.target.value))}
                    aria-label={`${name} opacity`}
                    className="w-14 px-1"
                  />
                )}
              </span>
            );
          })}
          {vectorLayers.map((l) => {
            const name = (l as { name?: string }).name ?? "?";
            return (
              <span key={name} className="rounded-md border border-[#e7e2da] bg-[#f7f4ee] px-2 py-0.5 text-[10px] text-[#8a837b]" title="vector layers are not displayable yet">
                {name} — vector layer, not displayable yet
              </span>
            );
          })}
        </div>
      )}
      {(detail.detections?.length ?? 0) > 0 && (
        <DetectionList
          detections={detail.detections ?? []}
          truncated={Boolean(detail.detections_truncated)}
          selectedId={props.selectedDetectionId}
          layerName={detail.detections?.[0]?.layer ?? "detected"}
          onSelect={(d) =>
            props.onSelectDetection({ bbox: d.bbox_lonlat, id: d.id })
          }
        />
      )}
      <button onClick={() => setOpenEvidence((v) => !v)} className="mt-2 text-[11px] text-[#2c5f58] underline">
        {openEvidence ? "hide evidence" : "evidence"}
      </button>
      {openEvidence && (
        <div className="mt-2 space-y-3 border-t border-[#f0ece4] pt-2">
          <EvidenceList result={r} />
          <WarningsPanel warnings={r.warnings} />
          {detail.narrative?.reliability && (
            <p className="text-[11px] text-[#4a443c]">{detail.narrative.reliability}</p>
          )}
          {detail.narrative?.note && <p className="text-[10px] text-[#8a837b]">{detail.narrative.note}</p>}
          {r.data_used.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-[#8a837b]">data used</p>
              <ul className="mt-0.5 space-y-0.5">
                {r.data_used.map((d, i) => (
                  <li key={i} className="mono text-[10px] text-[#4a443c]">{d}</li>
                ))}
              </ul>
            </div>
          )}
          {r.references_used.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-[#8a837b]">method</p>
              <ul className="mt-0.5 space-y-0.5">
                {r.references_used.map((d, i) => (
                  <li key={i} className="text-[10px] text-[#4a443c]">{d}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </article>
  );
}

function DetectionList(props: {
  detections: { id: string; label: string; bbox_lonlat: [number, number, number, number]; layer: string | null }[];
  truncated: boolean;
  selectedId: string | null;
  layerName: string;
  onSelect: (d: { id: string; label: string; bbox_lonlat: [number, number, number, number] }) => void;
}) {
  return (
    <div className="mt-2">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-[#8a837b]">
        detections ({props.detections.length})
      </p>
      {props.truncated && (
        <p className="text-[10px] text-[#8a837b]">showing the 50 largest; the count in the evidence is complete</p>
      )}
      <ul className="mt-1 space-y-1">
        {props.detections.map((d) => (
          <li key={d.id}>
            <button
              onClick={() => props.onSelect(d)}
              className={`w-full rounded-md border px-2 py-1 text-left text-[11px] ${
                props.selectedId === d.id
                  ? "border-[#9e3b2c] bg-[#fbf3ef] text-[#9e3b2c]"
                  : "border-[#e7e2da] text-[#4a443c] hover:border-[#2c5f58]"
              }`}
            >
              {d.label}
              <span className="mono ml-1 text-[9px] text-[#8a837b]">· {props.layerName}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
