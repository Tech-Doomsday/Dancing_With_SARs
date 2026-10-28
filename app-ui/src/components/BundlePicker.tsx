"use client";

import type { BundleListItem } from "@/lib/types";

/** Bundle picker — the engine's ingested SceneBundles (P01–P13), one card each.
 *  Metadata only: the pipeline derives its own acquisition window and events. */
export default function BundlePicker({
  bundles,
  selected,
  onSelect,
  error,
  cachedIds,
}: {
  bundles: BundleListItem[];
  selected: string | null;
  onSelect: (bundleId: string) => void;
  error?: string | null;
  /** bundles that already have a cached verdict in Supabase */
  cachedIds?: Set<string>;
}) {
  return (
    <div>
      {bundles.length === 0 && !error && (
        <p className="mono mt-3 text-xs text-[#8a837b]">
          No bundles ingested yet — build one with the retrieval/chain tools, then reload.
        </p>
      )}
      {error && (
        <p role="alert" className="mono mt-3 rounded border border-[#9e3b2c] bg-[#fbf3ef] px-3 py-2 text-xs text-[#9e3b2c]">
          {error}
        </p>
      )}
      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {bundles.map((b) => (
          <button
            key={b.bundle_id}
            onClick={() => onSelect(b.bundle_id)}
            aria-pressed={selected === b.bundle_id}
            className={`rounded-md border p-3 text-left text-xs transition ${
              selected === b.bundle_id
                ? "border-[#2c5f58] bg-[#eff5f3]"
                : "border-[#e7e2da] bg-[#faf9f6] hover:border-[#b08d57]"
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="mono font-semibold">{b.tile ?? b.bundle_id}</span>
              {cachedIds?.has(b.bundle_id) && (
                <span className="rounded-full border border-[#2c5f58]/40 bg-[#eff5f3] px-1.5 py-0.5 text-[10px] font-medium text-[#2c5f58]">
                  cached
                </span>
              )}
            </div>
            <div className="mt-1 text-[#8a837b]">
              epoch {b.epoch ?? "—"} · status {b.status ?? "—"}
            </div>
            <div className="mono mt-1 truncate text-[10px] text-[#b08d57]">{b.bundle_id}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
