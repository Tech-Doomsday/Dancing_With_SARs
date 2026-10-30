"use client";

/** ExplorerList (§7.8) — the keyboard-operable List view: a sortable table with the SAME
 *  filters and the SAME selection as the canvas. Columns are the engine's fields; unknown is
 *  "—"; the time columns are UTC. */
import { useMemo, useState } from "react";
import type { ExplorerGranule, ExplorerStack } from "@/lib/types";

type SortKey = "id" | "stack" | "ref" | "sec" | "baseline" | "track";

export default function ExplorerList({ granules, stacks, selectedIds, onSelect }: {
  granules: ExplorerGranule[];
  stacks: ExplorerStack[];
  selectedIds: string[];
  onSelect: (id: string, extend: boolean) => void;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("ref");
  const [asc, setAsc] = useState(true);

  const stackOf = useMemo(() => new Map(stacks.map((s) => [s.key, s])), [stacks]);

  const rows = useMemo(() => {
    const val = (g: ExplorerGranule): string | number | null => {
      switch (sortKey) {
        case "id": return g.id;
        case "stack": return g.stack;
        case "ref": return g.ref_time ?? g.start ?? null;
        case "sec": return g.sec_time ?? null;
        case "baseline": return g.baseline_days;
        case "track": return g.track;
      }
    };
    const sorted = [...granules].sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;         // unknown sorts last but stays in the list
      if (vb == null) return -1;
      if (typeof va === "number" && typeof vb === "number") return va - vb;
      return String(va).localeCompare(String(vb));
    });
    return asc ? sorted : sorted.reverse();
  }, [granules, sortKey, asc]);

  const th = (key: SortKey, label: string) => (
    <th scope="col" className="whitespace-nowrap px-2 py-1 text-left font-medium">
      <button
        type="button"
        onClick={() => (sortKey === key ? setAsc((a) => !a) : (setSortKey(key), setAsc(true)))}
        className={`hover:text-[#e6eaee] ${sortKey === key ? "text-[#7fd8e6]" : ""}`}
        aria-label={`sort by ${label}`}
      >
        {label}{sortKey === key ? (asc ? " ↑" : " ↓") : ""}
      </button>
    </th>
  );

  return (
    <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-[#262c36]">
      <table className="w-full border-collapse text-[11px]">
        <caption className="sr-only">granule list — the same filtered set as the timeline</caption>
        <thead className="sticky top-0 bg-[#12161d] text-[#8b949e]">
          <tr>
            {th("stack", "stack")}
            {th("track", "track")}
            {th("ref", "reference (UTC)")}
            {th("sec", "secondary (UTC)")}
            {th("baseline", "baseline d")}
            {th("id", "file name")}
          </tr>
        </thead>
        <tbody>
          {rows.map((g) => {
            const sel = selectedIds.includes(g.id);
            const color = g.direction === "D" ? "text-[#b39ce8]" : g.direction === "A" ? "text-[#3fb6c9]" : "text-[#8b949e]";
            return (
              <tr
                key={g.id}
                onClick={(e) => onSelect(g.id, e.shiftKey || e.ctrlKey || e.metaKey)}
                className={`cursor-pointer border-b border-[#1c222b] ${sel ? "bg-[#2a2416] outline outline-1 outline-[#e6c977]" : "hover:bg-[#161b22]"}`}
              >
                <td className={`px-2 py-1 font-mono ${color}`}>{g.stack}</td>
                <td className="px-2 py-1 font-mono text-[#aab3bd]">{g.track ?? "—"}</td>
                <td className="px-2 py-1 font-mono text-[#aab3bd]">{g.ref_time ?? g.start ?? "—"}</td>
                <td className="px-2 py-1 font-mono text-[#aab3bd]">{g.sec_time ?? "—"}</td>
                <td className="px-2 py-1 font-mono text-[#aab3bd]">{g.baseline_days ?? "—"}</td>
                <td className="max-w-[420px] truncate px-2 py-1 font-mono text-[#cfd6dc]" title={g.id}>{g.id}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
