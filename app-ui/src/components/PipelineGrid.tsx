"use client";

import type { PipelineInfo, PipelineResult } from "@/lib/types";
import StatusBadge from "./StatusBadge";

/** The verdict board: one row per pipeline, same shape for every hazard. Given a run's
 *  results it shows what ran; given the catalogue it shows what is implemented — the
 *  board always shows the whole picture, never only the hits (docs/IMPLEMENTATION_PLAN.md 10). */
export default function PipelineGrid({
  results,
  catalogue,
  title = "Pipelines on this run",
}: {
  results?: PipelineResult[] | null;
  catalogue?: PipelineInfo[];
  title?: string;
}) {
  if (results && results.length > 0) {
    const first = results[0];
    return (
      <section className="rounded-lg border border-[#e7e2da] bg-white p-4">
        <h3 className="text-sm font-semibold">{title}</h3>
        <ul className="mt-2 divide-y divide-[#f0ece4]">
          {results.map((r) => (
            <li key={r.pipeline} className="flex flex-wrap items-center gap-3 py-2">
              <StatusBadge status={r.status} />
              <span className="mono text-xs">{r.pipeline}</span>
              <span className="min-w-0 flex-1 truncate text-xs text-[#8a837b]">
                {r.summary || r.evidence[0]?.claim || ""}
              </span>
              <span className="mono text-[10px] text-[#b08d57]">{r.runtime_s.toFixed(1)} s</span>
            </li>
          ))}
        </ul>
        {catalogue && catalogue.length > results.length && (
          <p className="mt-2 text-[11px] text-[#8a837b]">
            {catalogue.filter((p) => !results.some((r) => r.pipeline === p.name)).length} more
            pipeline(s) were not requested on this run — launch them from the home page.
          </p>
        )}
      </section>
    );
  }

  if (catalogue && catalogue.length > 0) {
    return (
      <section className="rounded-lg border border-[#e7e2da] bg-white p-4">
        <h3 className="text-sm font-semibold">Pipeline board</h3>
        <p className="mt-1 text-xs text-[#8a837b]">
          Every pipeline the registry knows about, with its implementation state. Unimplemented
          pipelines answer honestly as NOT_MODELLED — the board never invents statuses.
        </p>
        <ul className="mt-2 divide-y divide-[#f0ece4]">
          {catalogue.map((p) => (
            <li key={p.name} className="flex flex-wrap items-center gap-3 py-2">
              <StatusBadge status={p.implemented ? "NOT_RUN" : "NOT_MODELLED"} />
              <span className="mono text-xs">{p.name}</span>
              <span className="mono text-[10px] text-[#b08d57]">{p.code}</span>
              <span className="text-[11px] text-[#8a837b]">{p.applies}</span>
            </li>
          ))}
        </ul>
      </section>
    );
  }
  return null;
}
