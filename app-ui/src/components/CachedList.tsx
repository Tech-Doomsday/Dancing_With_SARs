"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { cache, cacheConfigured } from "@/lib/cache";
import { timeAgo } from "@/lib/format";
import type { CachedSummary } from "@/lib/types";
import StatusBadge from "./StatusBadge";

/** Every verdict the engine has computed and cached. Reads Supabase directly, so it works (and is
 *  instant) even when the engine is offline. */
export default function CachedList() {
  const [rows, setRows] = useState<CachedSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!cacheConfigured) return;
    cache.list(40).then(setRows).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <section className="rounded-lg border border-[#e7e2da] bg-white p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold">Cached verdicts</h2>
        {rows && rows.length > 0 && <span className="text-[11px] text-[#8a837b]">{rows.length} shown · newest first</span>}
      </div>
      {!cacheConfigured && (
        <p className="mt-2 text-xs text-[#8a837b]">
          The Supabase cache is not configured for this UI (set NEXT_PUBLIC_SUPABASE_URL and
          NEXT_PUBLIC_SUPABASE_ANON_KEY — see app-ui/.env.example). Verdicts still run through the engine as before.
        </p>
      )}
      {err && (
        <p role="alert" className="mono mt-2 rounded border border-[#9e3b2c] bg-[#fbf3ef] px-3 py-2 text-xs text-[#9e3b2c]">
          {err}
        </p>
      )}
      {cacheConfigured && !rows && !err && (
        <div className="mt-3 space-y-2" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton h-9 rounded-md" />
          ))}
        </div>
      )}
      {rows && rows.length === 0 && (
        <p className="mt-2 text-xs text-[#8a837b]">
          Nothing cached yet. Every verdict the engine finishes is saved here automatically.
        </p>
      )}
      {rows && rows.length > 0 && (
        <ul className="mt-2 divide-y divide-[#f0ece4]">
          {rows.map((r) => (
            <li key={`${r.bundle_id}/${r.pipeline}`}>
              <Link
                href={`/verdicts/${encodeURIComponent(r.bundle_id)}`}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md px-2 py-2.5 transition-colors hover:bg-[#faf9f6]"
              >
                <StatusBadge status={r.status} />
                <span className="mono text-xs font-semibold">{r.tile}</span>
                <span className="mono text-[11px] text-[#8a837b]">{r.pipeline}</span>
                <span className="min-w-0 flex-1 basis-48 truncate text-xs text-[#4a443c]">{r.summary}</span>
                <span className="mono text-[10px] text-[#b08d57]" title={r.computed_at}>
                  {timeAgo(r.computed_at)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
