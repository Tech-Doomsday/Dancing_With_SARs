"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { BundleListItem, RunSummary, Health, PipelineInfo } from "@/lib/types";
import RunLauncher from "@/components/RunLauncher";
import StatusBadge from "@/components/StatusBadge";
import PipelineGrid from "@/components/PipelineGrid";

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [bundles, setBundles] = useState<BundleListItem[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [pipelines, setPipelines] = useState<PipelineInfo[] | null>(null);

  const refresh = () => {
    api.health().then(setHealth).catch(() => setHealth(null));
    api.bundles().then((r) => setBundles(r.bundles)).catch(() => {});
    api.runs().then((r) => setRuns(r.runs)).catch(() => {});
    api.pipelines().then((r) => setPipelines(r.pipelines)).catch(() => {});
  };

  useEffect(() => {
    refresh();
  }, []);

  const boundsOf = (b: BundleListItem | undefined) => {
    if (!b?.bbox_lonlat) return null;
    const [w, s, e, n] = b.bbox_lonlat;
    return [
      [s, w],
      [n, e],
    ] as [[number, number], [number, number]];
  };

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-[#e7e2da] bg-white p-4">
        <h1 className="text-xl font-semibold tracking-tight">
          Did the ground move? Check it with the engine.
        </h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[#4a443c]">
          This UI runs the engine&apos;s hazard pipelines and shows what was found, how confident
          the engine is, and exactly which data and method produced the verdict. Every status is
          the engine&apos;s own vocabulary — including &ldquo;not enough evidence&rdquo;, which is
          a real answer, not a failure.
        </p>
        <div className="mono mt-3 flex flex-wrap gap-4 text-[11px] text-[#8a837b]">
          <span>
            engine data dir: {health ? (health.data_dir_exists ? "✓ present" : "✗ missing") : "…"}
          </span>
          <span>
            bundle store: {health ? (health.bundles_dir_exists ? "✓ present" : "✗ missing") : "…"}
          </span>
          <span>{bundles.length} bundles ingested</span>
        </div>
      </section>

      <RunLauncher onStarted={() => refresh()} />

      <section className="rounded-lg border border-[#e7e2da] bg-white p-4">
        <h2 className="text-sm font-semibold">Recent runs</h2>
        {runs.length === 0 ? (
          <p className="mt-2 text-xs text-[#8a837b]">No runs yet.</p>
        ) : (
          <ul className="mt-2 divide-y divide-[#f0ece4]">
            {runs.map((r) => (
              <li key={r.id} className="flex items-center justify-between py-2">
                <Link href={`/runs/${r.id}`} className="mono text-xs text-[#2c5f58] hover:underline">
                  {r.id}
                </Link>
                <StatusBadge status={r.status} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <PipelineGrid catalogue={pipelines ?? undefined} />
    </div>
  );
}
