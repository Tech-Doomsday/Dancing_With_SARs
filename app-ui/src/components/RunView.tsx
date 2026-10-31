"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { api, pollWhile } from "@/lib/api";
import type { PipelineInfo, RunState } from "@/lib/types";
import RunProgress from "./RunProgress";
import CacheStatus from "./CacheStatus";
import PipelineGrid from "./PipelineGrid";
import { VerdictCard, EvidenceList, WarningsPanel } from "./VerdictCard";

// Leaflet touches window at import time: render the map client-side only.
const LayerMap = dynamic(() => import("./LayerMap"), {
  ssr: false,
  loading: () => <div className="skeleton h-[420px] rounded-lg" />,
});

export default function RunView({ runId, bounds }: { runId: string; bounds: [[number, number], [number, number]] | null }) {
  const [state, setState] = useState<RunState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [catalogue, setCatalogue] = useState<PipelineInfo[] | null>(null);
  const loadLayer = useCallback((name: string) => api.layer(runId, name).then((r) => r.layer), [runId]);

  useEffect(() => {
    // The contract is FLAT: GET /api/runs/{id} answers {ok, id, status, progress, error, results}.
    // Poll errors are surfaced (they used to be swallowed, leaving the page on "Loading run…").
    const stop = pollWhile(
      async () => {
        const r = await api.run(runId);
        const done = r.status === "ok" || r.status === "error";
        return { done, value: r };
      },
      (value) => setState(value),
      1500,
      (message) => setError(message || null),
    );
    return stop;
  }, [runId]);

  useEffect(() => {
    api.pipelines().then((r) => setCatalogue(r.pipelines)).catch(() => {});
  }, []);

  if (error && !state) {
    return (
      <p role="alert" className="mono rounded border border-[#9e3b2c] bg-[#fbf3ef] px-3 py-2 text-xs text-[#9e3b2c]">
        {error}
      </p>
    );
  }
  if (!state) return <p className="text-sm text-[#8a837b]">Loading run…</p>;

  return (
    <div className="space-y-4">
      {(state.status === "queued" || state.status === "running") && <RunProgress state={state} />}

      {state.status === "error" && (
        <p role="alert" className="mono rounded border border-[#9e3b2c] bg-[#fbf3ef] px-3 py-2 text-xs text-[#9e3b2c]">
          {state.error ?? "run failed"}
        </p>
      )}
      {error && state.status !== "error" && (
        <p role="status" className="mono rounded border border-[#b08d57]/50 bg-[#f7f2e7] px-3 py-2 text-xs text-[#7a6438]">
          last poll: {error}
        </p>
      )}

      {state.status === "ok" && state.results.length > 0 && (
        <>
          <CacheStatus report={state.cache} bundleId={state.results[0].bundle_id} />
          <VerdictCard result={state.results[0]} />
          <div className="grid gap-4 lg:grid-cols-2">
            <EvidenceList result={state.results[0]} />
            <div className="space-y-4">
              <WarningsPanel warnings={state.results[0].warnings} />
              <LayerMap load={loadLayer} sourceKey={runId} bounds={bounds} />
            </div>
          </div>
          <PipelineGrid results={state.results} catalogue={catalogue ?? undefined} />
        </>
      )}
    </div>
  );
}
