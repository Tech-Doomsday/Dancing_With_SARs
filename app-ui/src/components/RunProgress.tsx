"use client";

import type { RunState } from "@/lib/types";
import StatusBadge from "./StatusBadge";

/** Live progress for a queued/running engine run. Every stage the runner reports is
 *  shown verbatim — including heavy-lane progress (PENDING + fraction). */
export default function RunProgress({ state }: { state: RunState }) {
  const progress = state.progress as { stage?: string; tile?: string; fraction?: number; job?: string };
  return (
    <section aria-live="polite" className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] p-4">
      <div className="flex items-center gap-3">
        <StatusBadge status={state.status} />
        <span className="mono text-xs text-[#7a6438]">
          {progress?.stage ?? "working"}
          {progress?.tile ? ` · tile ${progress.tile}` : ""}
          {progress?.job ? ` · job ${progress.job}` : ""}
        </span>
      </div>
      {typeof progress?.fraction === "number" && (
        <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-[#efe6d2]">
          <div
            className="h-full rounded-full bg-[#b08d57]"
            style={{ width: `${Math.round(Math.max(0, Math.min(1, progress.fraction)) * 100)}%` }}
          />
        </div>
      )}
      <p className="mt-2 text-xs text-[#7a6438]">
        Running the real engine path (routing → gates → pipeline → collect). The verdict
        appears here when the registry returns.
      </p>
    </section>
  );
}
