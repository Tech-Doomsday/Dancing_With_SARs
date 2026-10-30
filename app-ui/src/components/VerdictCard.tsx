"use client";

import { useState } from "react";
import { CheckCircle2, AlertTriangle, Copy, Check } from "lucide-react";
import type { ReactNode } from "react";
import type { PipelineResult, EvidenceItem } from "@/lib/types";
import { fmtNum } from "@/lib/format";
import StatusBadge from "./StatusBadge";

/** Small copy-to-clipboard id (bundle ids are long; people paste them into tools). */
function CopyId({ value }: { value: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      title="Copy bundle id"
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => {
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        });
      }}
      className="inline-flex max-w-full items-center gap-1 rounded border border-[#e7e2da] px-1.5 py-0.5 text-[#8a837b] transition-colors hover:border-[#b08d57] hover:text-[#4a443c]"
    >
      <span className="mono truncate">{value}</span>
      {done ? <Check size={11} className="shrink-0 text-[#2c5f58]" /> : <Copy size={11} className="shrink-0" />}
    </button>
  );
}

export function VerdictCard({ result }: { result: PipelineResult }) {
  const narrative = (
    result.detail as { narrative?: { headline?: string; reliability?: string } } | undefined
  )?.narrative;
  const headline = narrative?.headline || result.summary || null;
  return (
    <section className="rounded-lg border border-[#e7e2da] bg-white p-5 text-[#1c1a17]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <StatusBadge status={result.status} />
        <span className="mono text-xs text-[#8a837b]">
          {result.pipeline} · tile {result.tile}
          {result.epoch ? ` · epoch ${result.epoch}` : ""} · {result.runtime_s.toFixed(1)} s
        </span>
      </div>
      {headline && <h2 className="mt-3 text-xl font-semibold leading-snug tracking-tight">{headline}</h2>}
      {result.raw_p !== null && (
        <div className="mt-4 max-w-xl">
          <div className="flex items-center justify-between text-xs text-[#8a837b]">
            <span>within-tier rank score</span>
            <span className="mono">{result.raw_p.toFixed(2)}</span>
          </div>
          <div
            className="mt-1 h-2 w-full overflow-hidden rounded-full bg-[#f0ece4]"
            role="meter"
            aria-label="within-tier rank score"
            aria-valuemin={0}
            aria-valuemax={1}
            aria-valuenow={result.raw_p}
          >
            <div className="h-full rounded-full bg-[#2c5f58]" style={{ width: `${Math.round(result.raw_p * 100)}%` }} />
          </div>
          <p className="mt-1 text-[11px] text-[#8a837b]">
            A within-tier rank score — NOT a percentage and not a calibrated probability (calibration lands with
            verdict stage 4.2).
          </p>
        </div>
      )}
      {narrative?.reliability && <p className="mt-4 text-xs leading-relaxed text-[#4a443c]">{narrative.reliability}</p>}
      {result.bundle_id && (
        <div className="mt-4 flex max-w-full items-center gap-2 text-[11px] text-[#8a837b]">
          <span className="shrink-0">bundle</span>
          <CopyId value={result.bundle_id} />
        </div>
      )}
    </section>
  );
}

const KIND_ICON: Record<string, ReactNode> = {
  detection: <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-[#2c5f58]" />,
  forward_model: <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-[#2c5f58]" />,
  null_result: <AlertTriangle size={14} className="mt-0.5 shrink-0 text-[#8a837b]" />,
};

/** The engine tags every evidence item with a level; grouping by it is display only. */
const LEVELS: { key: string; title: string }[] = [
  { key: "decision", title: "What the verdict rests on" },
  { key: "corroboration", title: "Corroborating" },
  { key: "info", title: "Context" },
  { key: "caveat", title: "Caveats" },
];

/** Scalar values of the engine's `support` object as small chips. Nested values are not shown. */
function SupportChips({ support, sourceRef }: { support?: Record<string, unknown> | null; sourceRef?: string | null }) {
  const chips = Object.entries(support ?? {})
    .filter(([, v]) => typeof v === "number" || typeof v === "string" || typeof v === "boolean")
    .slice(0, 6)
    .map(([k, v]) => `${k.replace(/_/g, " ")} ${typeof v === "number" ? fmtNum(v) : String(v)}`);
  if (!chips.length && !sourceRef) return null;
  return (
    <span className="mt-1 flex flex-wrap gap-1">
      {chips.map((c) => (
        <span key={c} className="mono rounded bg-[#f7f4ee] px-1.5 py-0.5 text-[10px] text-[#4a443c]">
          {c}
        </span>
      ))}
      {sourceRef && <span className="mono rounded px-1.5 py-0.5 text-[10px] text-[#b08d57]">src {sourceRef}</span>}
    </span>
  );
}

export function EvidenceList({ result }: { result: PipelineResult }) {
  const levelOf = (e: EvidenceItem) => e.level || "info";
  const known = new Set(LEVELS.map((l) => l.key));
  const extra = Array.from(new Set(result.evidence.map(levelOf).filter((l) => !known.has(l))));
  const groups = [...LEVELS, ...extra.map((key) => ({ key, title: key }))]
    .map((g) => ({ ...g, items: result.evidence.filter((e) => levelOf(e) === g.key) }))
    .filter((g) => g.items.length > 0);

  return (
    <section className="rounded-lg border border-[#e7e2da] bg-white p-5 text-[#1c1a17]">
      <h3 className="text-sm font-semibold">Evidence</h3>
      {groups.length === 0 && <p className="mt-2 text-xs text-[#8a837b]">The engine attached no evidence items.</p>}
      {groups.map((g) => (
        <div key={g.key} className="mt-3 first:mt-2">
          {groups.length > 1 && (
            <h4 className="text-[11px] font-semibold uppercase tracking-wide text-[#8a837b]">{g.title}</h4>
          )}
          <ul className="mt-1.5 space-y-2.5">
            {g.items.map((e: EvidenceItem, i: number) => (
              <li key={e.id ?? `${g.key}-${i}`} className="flex gap-2 text-xs leading-relaxed text-[#4a443c]">
                {KIND_ICON[e.kind] ?? <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[#b08d57]" />}
                <span className="min-w-0">
                  {e.claim}
                  <SupportChips support={e.support} sourceRef={e.source_ref} />
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {result.data_used.length > 0 && (
        <>
          <h4 className="mt-5 text-xs font-semibold text-[#8a837b]">Data used</h4>
          <ul className="mono mt-1 space-y-1 text-[11px] text-[#4a443c]">
            {result.data_used.map((d, i) => (
              <li key={i}>· {d}</li>
            ))}
          </ul>
        </>
      )}
      {result.references_used.length > 0 && (
        <>
          <h4 className="mt-3 text-xs font-semibold text-[#8a837b]">Method</h4>
          <ul className="mt-1 space-y-1 text-[11px] text-[#8a837b]">
            {result.references_used.map((r, i) => (
              <li key={i}>· {r}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

export function WarningsPanel({ warnings }: { warnings: string[] }) {
  if (!warnings.length) return null;
  return (
    <section className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] p-5">
      <h3 className="text-sm font-semibold text-[#7a6438]">Warnings &amp; limitations</h3>
      <ul className="mt-2 space-y-1 text-xs text-[#7a6438]">
        {warnings.map((w, i) => (
          <li key={i}>· {w}</li>
        ))}
      </ul>
      <p className="mt-3 text-[11px] leading-relaxed text-[#9e3b2c]">
        The forward-model screen is a consistency check, never a causation claim. Inundation-scale hazards are out of
        scope for this pipeline; resolutions and tolerances are documented in the engine&apos;s method references.
      </p>
    </section>
  );
}
