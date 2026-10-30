"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Play, Eye } from "lucide-react";
import { api } from "@/lib/api";
import { cache, cacheConfigured } from "@/lib/cache";
import type { BundleListItem, PipelineInfo } from "@/lib/types";
import StatusBadge from "./StatusBadge";
import BundlePicker from "./BundlePicker";

/** Parameter overrides the engine validates and APPLIES (routes_runs.ALLOWED_PARAMS):
 *  out-of-range or unknown values are rejected with 400, in-range values reach the
 *  pipeline through the run's request triggers and land in the result's detail.params. */
const PARAM_FIELDS: { key: string; label: string; min: number; max: number; step: number; hint: string }[] = [
  { key: "coherence_threshold", label: "Coherence gate", min: 0.0, max: 1.0, step: 0.05, hint: "valid-pixel coherence floor (default 0.5)" },
  { key: "detection_floor_m", label: "Detection floor", min: 0.001, max: 0.2, step: 0.001, hint: "absolute LOS floor in m (default 0.01 = 10 mm)" },
  { key: "significance_k", label: "Significance k", min: 1.0, max: 6.0, step: 0.5, hint: "sigma multiplier (default 3)" },
  { key: "far_field_margin", label: "Far-field margin", min: 0.02, max: 0.4, step: 0.02, hint: "reference margin fraction (default 0.12)" },
];

export default function RunLauncher({
  onStarted,
}: {
  onStarted: (runId: string) => void;
}) {
  const [bundles, setBundles] = useState<BundleListItem[]>([]);
  const [pipelines, setPipelines] = useState<PipelineInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [params, setParams] = useState<Record<string, number>>({});
  const [cachedIds, setCachedIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    api.bundles().then((r) => {
      setBundles(r.bundles);
      if (r.bundles.length) setSelected(r.bundles[0].bundle_id);
    }).catch((e) => setError(String(e)));
    api.pipelines().then((r) => setPipelines(r.pipelines)).catch(() => {});
    if (cacheConfigured) {
      cache.list(200).then((rows) => setCachedIds(new Set(rows.map((r) => r.bundle_id)))).catch(() => {});
    }
  }, []);

  const isCached = selected !== null && cachedIds.has(selected);
  const customParams = Object.values(params).some((v) => Number.isFinite(v));

  const launch = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => Number.isFinite(v)));
      const { run_id } = await api.startRun(selected, ["earthquake_coseismic"], clean);
      onStarted(run_id);
      window.location.href = `/runs/${run_id}`;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-lg border border-[#e7e2da] bg-white p-4">
      <h2 className="text-sm font-semibold">1 · Choose an ingested scene</h2>
      <p className="mt-1 text-xs text-[#8a837b]">
        Bundles are the engine&apos;s preprocessed SceneBundles (P01–P13). The pipeline
        derives the acquisition window itself and fetches matching ComCat events.
      </p>
      <BundlePicker bundles={bundles} selected={selected} onSelect={setSelected} error={error} cachedIds={cachedIds} />

      <h2 className="mt-5 text-sm font-semibold">2 · Parameters (optional)</h2>
      <p className="mt-1 text-xs text-[#8a837b]">
        Overrides the engine validates server-side (out-of-range → 400) and APPLIES to the run —
        the verdict&apos;s detail always records what was actually used. Runs with custom parameters are
        never cached — the cache holds engine-default verdicts only.
      </p>
      <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {PARAM_FIELDS.map((f) => (
          <label key={f.key} className="text-xs">
            <span className="block font-medium text-[#4a443c]">{f.label}</span>
            <input suppressHydrationWarning
              type="number"
              inputMode="decimal"
              min={f.min}
              max={f.max}
              step={f.step}
              placeholder="engine default"
              value={params[f.key] ?? ""}
              onChange={(e) => {
                const v = e.target.value === "" ? undefined : Number(e.target.value);
                setParams((p) => {
                  const next = { ...p };
                  if (v === undefined) delete next[f.key];
                  else next[f.key] = v;
                  return next;
                });
              }}
              className="mono mt-1 w-full rounded-md border border-[#e7e2da] bg-[#faf9f6] px-2 py-1.5 text-xs focus:border-[#2c5f58] focus:outline-none"
            />
            <span className="mt-1 block text-[10px] text-[#8a837b]">
              {f.min}–{f.max} · {f.hint}
            </span>
          </label>
        ))}
      </div>

      <h2 className="mt-5 text-sm font-semibold">3 · Run</h2>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        {isCached && !customParams && selected && (
          <Link
            href={`/verdicts/${encodeURIComponent(selected)}`}
            className="inline-flex items-center gap-2 rounded-md bg-[#2c5f58] px-4 py-2 text-sm font-medium text-white"
          >
            <Eye size={15} /> View cached verdict
          </Link>
        )}
        <button
          onClick={launch}
          disabled={!selected || busy}
          className={`inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium disabled:opacity-40 ${
            isCached && !customParams
              ? "border border-[#2c5f58] text-[#2c5f58] hover:bg-[#eff5f3]"
              : "bg-[#2c5f58] text-white"
          }`}
        >
          <Play size={15} />{" "}
          {busy
            ? "Starting…"
            : customParams
              ? "Run with custom parameters (not cached)"
              : isCached
                ? "Redo verdict (replaces cached)"
                : "Run earthquake_coseismic"}
        </button>
        {pipelines.length > 0 && (
          <span className="text-xs text-[#8a837b]">
            {pipelines.filter((p) => p.implemented).length}/{pipelines.length} pipelines implemented
            — the rest report honestly as <StatusBadge status="NOT_MODELLED" />
          </span>
        )}
      </div>
      {error && (
        <p role="alert" className="mono mt-3 rounded border border-[#9e3b2c] bg-[#fbf3ef] px-3 py-2 text-xs text-[#9e3b2c]">
          {error}
        </p>
      )}
    </section>
  );
}
