"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { api } from "@/lib/api";
import { cache, cacheConfigured } from "@/lib/cache";
import { fmtUtc, timeAgo } from "@/lib/format";
import type { BundleListItem, CachedVerdict, PipelineInfo } from "@/lib/types";
import PipelineGrid from "./PipelineGrid";
import RedoButton from "./RedoButton";
import EventsPanel from "./EventsPanel";
import { VerdictCard, EvidenceList, WarningsPanel } from "./VerdictCard";

const LayerMap = dynamic(() => import("./LayerMap"), {
  ssr: false,
  loading: () => <div className="skeleton h-[420px] rounded-lg" />,
});

type Engine = { state: "checking" } | { state: "offline" } | { state: "online"; bundle?: BundleListItem; catalogue?: PipelineInfo[] };

/** A cached verdict, served from Supabase. The engine is only needed for Redo and map context. */
export default function CachedView({ bundleId }: { bundleId: string }) {
  const [rows, setRows] = useState<CachedVerdict[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [engine, setEngine] = useState<Engine>({ state: "checking" });

  useEffect(() => {
    if (!cacheConfigured) return;
    cache.forBundle(bundleId).then(setRows).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
    Promise.allSettled([api.bundles(), api.pipelines()]).then(([b, p]) => {
      if (b.status === "rejected") return setEngine({ state: "offline" });
      setEngine({
        state: "online",
        bundle: b.value.bundles.find((x) => x.bundle_id === bundleId),
        catalogue: p.status === "fulfilled" ? p.value.pipelines : undefined,
      });
    });
  }, [bundleId]);

  const primary = rows?.[0];
  const pipeline = primary?.pipeline ?? "";
  const load = useCallback((name: string) => cache.layer(bundleId, pipeline, name), [bundleId, pipeline]);

  if (!cacheConfigured) {
    return (
      <p role="alert" className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] p-4 text-xs text-[#7a6438]">
        The Supabase cache is not configured for this UI (NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY).
      </p>
    );
  }
  if (err) {
    return (
      <p role="alert" className="mono rounded border border-[#9e3b2c] bg-[#fbf3ef] px-3 py-2 text-xs text-[#9e3b2c]">
        {err}
      </p>
    );
  }
  if (!rows) {
    return (
      <div className="space-y-4" aria-busy="true">
        <div className="skeleton h-28 rounded-lg" />
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="skeleton h-48 rounded-lg" />
          <div className="skeleton h-48 rounded-lg" />
        </div>
      </div>
    );
  }
  if (!primary) {
    return (
      <section className="rounded-lg border border-[#e7e2da] bg-white p-5 text-sm">
        <p className="font-semibold">Nothing is cached for this bundle yet.</p>
        <p className="mt-1 text-xs text-[#8a837b]">
          Run it once from the <Link href="/" className="text-[#2c5f58] underline">home page</Link>; the verdict is saved
          automatically.
        </p>
      </section>
    );
  }

  const unavailable =
    engine.state === "checking"
      ? "checking the engine…"
      : engine.state === "offline"
        ? "The engine is offline — start it to redo this verdict."
        : !engine.bundle
          ? "This engine has not ingested this bundle, so it cannot recompute it."
          : null;
  const bounds: [[number, number], [number, number]] | null =
    engine.state === "online" && engine.bundle?.bbox_lonlat
      ? [
          [engine.bundle.bbox_lonlat[1], engine.bundle.bbox_lonlat[0]],
          [engine.bundle.bbox_lonlat[3], engine.bundle.bbox_lonlat[2]],
        ]
      : null;
  const center = engine.state === "online" ? engine.bundle?.center_lonlat : null;
  const redone = primary.computed_at.slice(0, 16) !== primary.first_computed_at.slice(0, 16);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-sm font-semibold">
            Cached verdict <span className="mono text-[#8a837b]">· tile {primary.tile}</span>
          </h1>
          <p className="mt-1 text-xs text-[#8a837b]">
            <span className="mr-2 inline-flex items-center rounded-full border border-[#2c5f58]/40 bg-[#eff5f3] px-2 py-0.5 text-[11px] font-medium text-[#2c5f58]">
              cached
            </span>
            computed by the engine <span title={fmtUtc(primary.computed_at)}>{timeAgo(primary.computed_at)}</span>
            {" · "}
            {fmtUtc(primary.computed_at)}
            {redone && <> · first computed {fmtUtc(primary.first_computed_at)}</>}
            {primary.run_id && <span className="mono"> · {primary.run_id}</span>}
          </p>
        </div>
        <RedoButton bundleId={bundleId} pipelines={rows.map((r) => r.pipeline)} unavailable={unavailable} />
      </div>

      <VerdictCard result={primary.result} />
      <div className="grid gap-4 lg:grid-cols-2">
        <EvidenceList result={primary.result} />
        <div className="space-y-4">
          <WarningsPanel warnings={primary.result.warnings} />
          <LayerMap load={load} sourceKey={`${bundleId}:${pipeline}`} bounds={bounds} />
        </div>
      </div>
      <PipelineGrid
        results={rows.map((r) => r.result)}
        catalogue={engine.state === "online" ? engine.catalogue : undefined}
        title="Pipelines in this cached verdict"
      />
      {center && <EventsPanel lon={center[0]} lat={center[1]} />}
    </div>
  );
}
