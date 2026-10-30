"use client";

/** ExplorerSummary (§7.8) — the always-visible strip: total granules · stacks · distinct
 *  acquisition epochs · first → last · per-stack median/min/max gap · time since last
 *  acquisition. Everything comes from the engine's response (DEC13); the strip never derives
 *  a number the engine did not send. */
import { useMemo } from "react";
import type { GranuleExplorerResponse } from "@/lib/types";
import { timeAgo } from "@/lib/format";

export default function ExplorerSummary({ data, visibleCount, keptCount, fetchedCount }: {
  data: GranuleExplorerResponse;
  visibleCount: number;
  keptCount: number;
  fetchedCount: number;
}) {
  const total = data.n_total;
  const nStacks = data.stacks.length;
  const nEpochs = useMemo(() => {
    // distinct epochs ACROSS stacks (union of each stack's own epoch list — engine-computed)
    const set = new Set<string>();
    for (const s of data.stacks) for (const e of s.epochs) set.add(e);
    return set.size;
  }, [data.stacks]);
  const first = useMemo(
    () => data.stacks.map((s) => s.first).filter(Boolean).sort()[0] ?? null,
    [data.stacks],
  );
  const last = useMemo(
    () => data.stacks.map((s) => s.last).filter(Boolean).sort().at(-1) ?? null,
    [data.stacks],
  );
  const sinceLast = timeAgo(last);

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-[#262c36] bg-[#10141a] px-4 py-1.5 text-[11px]">
      <span className="text-[#e6eaee]">
        {keptCount === fetchedCount && visibleCount === fetchedCount
          ? `${total} granules`
          : `${visibleCount} of ${fetchedCount} match`}
      </span>
      <span className="text-[#8b949e]">{nStacks} stacks</span>
      <span className="text-[#8b949e]">{nEpochs} distinct acquisition epochs</span>
      <span className="text-[#8b949e]">
        first → last: <span className="font-mono text-[#aab3bd]">{first ?? "—"}</span>
        {" → "}
        <span className="font-mono text-[#aab3bd]">{last ?? "—"}</span>
      </span>
      <span className="text-[#8b949e]">last acquisition {sinceLast}</span>
      <span className="ml-auto flex flex-wrap gap-x-3 gap-y-0.5">
        {data.stacks.map((s) => (
          <span key={s.key} className="text-[#8b949e]">
            <span className={`font-mono ${s.direction === "A" ? "text-[#3fb6c9]" : "text-[#b39ce8]"}`}>{s.key}</span>
            {" · "}{s.n_granules} granules · {s.n_epochs} epochs
            {s.median_gap_days != null
              ? ` · gap ${s.min_gap_days}/${s.median_gap_days}/${s.max_gap_days} d (min/med/max)`
              : ""}
            {s.nominal_repeat_days != null && s.max_gap_days != null && s.max_gap_days > s.nominal_repeat_days
              ? ` · missing cycle(s) (${s.max_gap_days} d > nominal ${s.nominal_repeat_days} d)`
              : ""}
          </span>
        ))}
      </span>
    </div>
  );
}
