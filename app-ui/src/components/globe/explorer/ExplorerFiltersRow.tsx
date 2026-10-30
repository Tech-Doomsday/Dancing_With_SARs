"use client";

/** ExplorerFiltersRow (§7.8) — client-side filters over the fetched set. All parsers are pure
 *  (lib/explorer.ts) and unit-tested; invalid input shows an inline message and is NOT applied. */
import { useMemo, useState } from "react";
import {
  parseDayOfMonth, parseTimeOfDay,
  type AppliesTo, type ExplorerFilters,
} from "@/lib/explorer";

export default function ExplorerFiltersRow({ filters, onChange }: {
  filters: ExplorerFilters;
  onChange: (f: ExplorerFilters) => void;
}) {
  const [domText, setDomText] = useState("");
  const [todText, setTodText] = useState("");

  const domMessage = useMemo(() => {
    if (!domText.trim()) return null;
    return parseDayOfMonth(domText) === null ? "day-of-month: use 1–31, ranges like 20-25, or comma lists" : null;
  }, [domText]);
  const todMessage = useMemo(() => {
    if (!todText.trim()) return null;
    return parseTimeOfDay(todText) === null ? "UTC time: use HH:MM or HH:MM-HH:MM (midnight wrap ok)" : null;
  }, [todText]);

  const applyDom = (text: string) => {
    setDomText(text);
    const parsed = parseDayOfMonth(text);
    if (parsed !== null) onChange({ ...filters, dom: parsed });
  };
  const applyTod = (text: string) => {
    setTodText(text);
    const parsed = parseTimeOfDay(text);
    if (parsed !== null) onChange({ ...filters, tod: parsed });
  };

  const inputCls =
    "rounded border border-[#333b47] bg-[#0e1116] px-1.5 py-1 font-mono text-[11px] text-[#e6eaee] placeholder:text-[#5a636e] focus:border-[#3fb6c9] focus:outline-none";

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-4 py-1.5 text-[11px] text-[#8b949e]">
      <label className="flex items-center gap-1.5">
        day of month
        <input
          value={domText}
          onChange={(e) => applyDom(e.target.value)}
          placeholder="20-25, 5"
          className={`${inputCls} w-24`}
          aria-invalid={Boolean(domMessage)}
        />
      </label>
      <label className="flex items-center gap-1.5">
        UTC time of day
        <input
          value={todText}
          onChange={(e) => applyTod(e.target.value)}
          placeholder="13:00-14:30"
          className={`${inputCls} w-28`}
          aria-invalid={Boolean(todMessage)}
        />
      </label>
      <label className="flex items-center gap-1.5">
        applies to
        <select
          value={filters.applies}
          onChange={(e) => onChange({ ...filters, applies: e.target.value as AppliesTo })}
          className={`${inputCls} bg-[#0e1116]`}
        >
          <option value="either">either</option>
          <option value="reference">reference</option>
          <option value="secondary">secondary</option>
        </select>
      </label>
      <label className="flex items-center gap-1.5">
        direction
        <select
          value={filters.direction}
          onChange={(e) => onChange({ ...filters, direction: e.target.value as ExplorerFilters["direction"] })}
          className={`${inputCls} bg-[#0e1116]`}
        >
          <option value="both">both</option>
          <option value="A">ascending</option>
          <option value="D">descending</option>
        </select>
      </label>
      <label className="flex items-center gap-1.5">
        track
        <input
          value={filters.track ?? ""}
          onChange={(e) => {
            const v = e.target.value.trim();
            const n = v === "" ? null : Number(v);
            onChange({ ...filters, track: n !== null && Number.isInteger(n) && n > 0 ? n : null });
          }}
          placeholder="34"
          className={`${inputCls} w-16`}
          type="number"
          min={1}
        />
      </label>
      <label className="flex items-center gap-1.5">
        text
        <input
          value={filters.text}
          onChange={(e) => onChange({ ...filters, text: e.target.value })}
          placeholder="file-name substring"
          className={`${inputCls} w-40`}
        />
      </label>
      {(domMessage || todMessage) && (
        <p role="status" className="text-[#e6c977]">
          {domMessage ?? todMessage}
        </p>
      )}
    </div>
  );
}
