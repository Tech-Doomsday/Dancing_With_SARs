"use client";

import { useEffect, useState } from "react";
import { Play } from "lucide-react";
import { api } from "@/lib/api";
import type { BundleListItem, PipelineInfo } from "@/lib/types";
import StatusBadge from "./StatusBadge";

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

  useEffect(() => {
    api.bundles().then((r) => {
      setBundles(r.bundles);
      if (r.bundles.length) setSelected(r.bundles[0].bundle_id);
    }).catch((e) => setError(String(e)));
    api.pipelines().then((r) => setPipelines(r.pipelines)).catch(() => {});
  }, []);

  const launch = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await api.startRun(selected, ["earthquake_coseismic"]);
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
        Bundles are the engine's preprocessed SceneBundles (P01–P13). The pipeline
        derives the acquisition window itself and fetches matching ComCat events.
      </p>
      {bundles.length === 0 && !error && (
        <p className="mono mt-3 text-xs text-[#8a837b]">
          No bundles ingested yet — build one with the retrieval/chain tools, then reload.
        </p>
      )}
      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {bundles.map((b) => (
          <button
            key={b.bundle_id}
            onClick={() => setSelected(b.bundle_id)}
            className={`rounded-md border p-3 text-left text-xs transition ${
              selected === b.bundle_id
                ? "border-[#2c5f58] bg-[#eff5f3]"
                : "border-[#e7e2da] bg-[#faf9f6] hover:border-[#b08d57]"
            }`}
          >
            <div className="mono font-semibold">{b.tile ?? b.bundle_id}</div>
            <div className="mt-1 text-[#8a837b]">
              epoch {b.epoch ?? "—"} · status {b.status ?? "—"}
            </div>
            <div className="mono mt-1 truncate text-[10px] text-[#b08d57]">{b.bundle_id}</div>
          </button>
        ))}
      </div>

      <h2 className="mt-5 text-sm font-semibold">2 · Run</h2>
      <div className="mt-2 flex items-center gap-3">
        <button
          onClick={launch}
          disabled={!selected || busy}
          className="inline-flex items-center gap-2 rounded-md bg-[#2c5f58] px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          <Play size={15} /> {busy ? "Starting…" : "Run earthquake_coseismic"}
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
