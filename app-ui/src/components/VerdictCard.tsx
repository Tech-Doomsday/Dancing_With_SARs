"use client";

import { CheckCircle2, AlertTriangle } from "lucide-react";
import type { ReactNode } from "react";
import type { PipelineResult, EvidenceItem } from "@/lib/types";
import StatusBadge from "./StatusBadge";

export function VerdictCard({ result }: { result: PipelineResult }) {
  const narrative = (
    result.detail as { narrative?: { headline?: string; reliability?: string } } | undefined
  )?.narrative;
  return (
    <section className="rounded-lg border border-[#e7e2da] bg-white p-4">
      <div className="flex flex-wrap items-center gap-3">
        <StatusBadge status={result.status} />
        <span className="mono text-xs text-[#8a837b]">
          pipeline {result.pipeline} · tile {result.tile} · {result.runtime_s.toFixed(1)} s
        </span>
      </div>
      {narrative?.headline ? (
        <h2 className="mt-3 text-lg font-semibold leading-snug">{narrative.headline}</h2>
      ) : result.summary ? (
        <h2 className="mt-3 text-lg font-semibold leading-snug">{result.summary}</h2>
      ) : null}
      {result.raw_p !== null && (
        <div className="mt-3">
          <div className="flex items-center justify-between text-xs text-[#8a837b]">
            <span>within-tier rank score</span>
            <span className="mono">{result.raw_p.toFixed(2)}</span>
          </div>
          <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-[#f0ece4]">
            <div
              className="h-full rounded-full bg-[#2c5f58]"
              style={{ width: `${Math.round((result.raw_p ?? 0) * 100)}%` }}
            />
          </div>
          <p className="mt-1 text-[11px] text-[#8a837b]">
            A within-tier rank score — NOT a percentage and not a calibrated probability
            (calibration lands with verdict stage 4.2).
          </p>
        </div>
      )}
      {narrative?.reliability && (
        <p className="mt-3 text-xs text-[#4a443c]">{narrative.reliability}</p>
      )}
    </section>
  );
}

const KIND_ICON: Record<string, ReactNode> = {
  detection: <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-[#2c5f58]" />,
  forward_model: <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-[#2c5f58]" />,
  null_result: <AlertTriangle size={14} className="mt-0.5 shrink-0 text-[#8a837b]" />,
};

export function EvidenceList({ result }: { result: PipelineResult }) {
  return (
    <section className="rounded-lg border border-[#e7e2da] bg-white p-4">
      <h3 className="text-sm font-semibold">Evidence</h3>
      <ul className="mt-2 space-y-2">
        {result.evidence.map((e: EvidenceItem, i: number) => (
          <li key={e.id ?? i} className="flex gap-2 text-xs leading-relaxed text-[#4a443c]">
            {KIND_ICON[e.kind] ?? <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-[#b08d57]" />}
            <span>{e.claim}</span>
          </li>
        ))}
      </ul>
      {result.data_used.length > 0 && (
        <>
          <h4 className="mt-4 text-xs font-semibold text-[#8a837b]">Data used</h4>
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
    <section className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] p-4">
      <h3 className="text-sm font-semibold text-[#7a6438]">Warnings &amp; limitations</h3>
      <ul className="mt-2 space-y-1 text-xs text-[#7a6438]">
        {warnings.map((w, i) => (
          <li key={i}>· {w}</li>
        ))}
      </ul>
      <p className="mt-3 text-[11px] leading-relaxed text-[#9e3b2c]">
        The forward-model screen is a consistency check, never a causation claim. Inundation-scale
        hazards are out of scope for this pipeline; resolutions and tolerances are documented in the
        engine&apos;s method references.
      </p>
    </section>
  );
}
