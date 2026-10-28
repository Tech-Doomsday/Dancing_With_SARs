"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ENGINE_API } from "@/lib/api";
import { cacheConfigured } from "@/lib/cache";
import type { BundleListItem, RunSummary, Health, PipelineInfo } from "@/lib/types";
import RunLauncher from "@/components/RunLauncher";
import StatusBadge from "@/components/StatusBadge";
import PipelineGrid from "@/components/PipelineGrid";
import CachedList from "@/components/CachedList";

function Dot({ ok, label }: { ok: boolean | null; label: string }) {
  const color = ok === null ? "bg-[#d8d2c6]" : ok ? "bg-[#2c5f58]" : "bg-[#9e3b2c]";
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`h-1.5 w-1.5 rounded-full ${color}`} aria-hidden />
      {label}
    </span>
  );
}

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [engineChecked, setEngineChecked] = useState(false);
  const [bundles, setBundles] = useState<BundleListItem[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [pipelines, setPipelines] = useState<PipelineInfo[] | null>(null);

  const refresh = () => {
    api
      .health()
      .then(setHealth)
      .catch(() => setHealth(null))
      .finally(() => setEngineChecked(true));
    api.bundles().then((r) => setBundles(r.bundles)).catch(() => {});
    api.runs().then((r) => setRuns(r.runs)).catch(() => {});
    api.pipelines().then((r) => setPipelines(r.pipelines)).catch(() => {});
  };

  useEffect(() => {
    refresh();
  }, []);

  const engineOnline = engineChecked ? health !== null : null;

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-[#e7e2da] bg-white p-5">
        <h1 className="text-xl font-semibold tracking-tight">Did the ground move? Check it with the engine.</h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[#4a443c]">
          This UI runs the engine&apos;s hazard pipelines and shows what was found, how confident the engine is, and
          exactly which data and method produced the verdict. Every verdict is saved, so you can reopen it instantly
          — and redo it whenever you want a fresh run. Every status is the engine&apos;s own vocabulary — including
          &ldquo;not enough evidence&rdquo;, which is a real answer, not a failure.
        </p>
        <div className="mono mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-[#8a837b]">
          <Dot ok={engineOnline} label={engineOnline === null ? "engine …" : engineOnline ? "engine online" : "engine offline"} />
          <Dot ok={cacheConfigured} label={cacheConfigured ? "verdict cache connected" : "verdict cache not configured"} />
          <span>
            engine data dir:{" "}
            {health ? (health.data_dir_exists ? "✓ present" : "✗ missing") : engineChecked ? "unreachable" : "…"}
          </span>
          {health && <span>bundle store: {health.bundles_dir_exists ? "✓ present" : "✗ missing"}</span>}
          {engineOnline && <span>{bundles.length} bundles ingested</span>}
        </div>
      </section>

      {engineOnline === false && (
        <p role="status" className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] px-4 py-3 text-xs text-[#7a6438]">
          The engine API is not reachable at <span className="mono">{ENGINE_API}</span>.
          {cacheConfigured
            ? " Cached verdicts below still open instantly; running or redoing a verdict needs the engine."
            : " Start it to run verdicts."}
        </p>
      )}

      <CachedList />

      <RunLauncher onStarted={() => refresh()} />

      <section className="rounded-lg border border-[#e7e2da] bg-white p-5">
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
