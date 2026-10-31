"use client";

import Link from "next/link";
import type { CacheReport } from "@/lib/types";

/** What happened to this run's verdicts in the Supabase cache — said plainly, never implied. */
export default function CacheStatus({ report, bundleId }: { report?: CacheReport; bundleId?: string | null }) {
  if (!report) return null; // run loaded from the store after an engine restart: nothing to report
  const box = "rounded-lg border px-4 py-3 text-xs";
  if (!report.enabled) {
    return (
      <p role="status" className={`${box} border-[#e7e2da] bg-[#f7f4ee] text-[#8a837b]`}>
        Cache off — this verdict was computed but is not stored{report.reason ? `: ${report.reason}` : ""}.
      </p>
    );
  }
  return (
    <div role="status" className="space-y-2">
      {report.written > 0 && (
        <p className={`${box} flex flex-wrap items-center justify-between gap-2 border-[#2c5f58]/40 bg-[#eff5f3] text-[#2c5f58]`}>
          <span>
            Cached: {report.written} verdict{report.written === 1 ? "" : "s"} saved — anyone can now open this instantly.
          </span>
          {bundleId && (
            <Link href={`/verdicts/${encodeURIComponent(bundleId)}`} className="font-medium underline">
              open cached view →
            </Link>
          )}
        </p>
      )}
      {report.skipped.map((s, i) => (
        <p key={i} className={`${box} border-[#b08d57]/40 bg-[#f7f2e7] text-[#7a6438]`}>
          Not cached{s.pipeline ? ` (${s.pipeline})` : ""}: {s.reason}.
        </p>
      ))}
      {report.failed.map((f, i) => (
        <p key={i} role="alert" className={`${box} mono border-[#9e3b2c] bg-[#fbf3ef] text-[#9e3b2c]`}>
          Cache write failed — the verdict is computed but NOT cached: {f}
        </p>
      ))}
    </div>
  );
}
