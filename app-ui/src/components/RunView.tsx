"use client";

import { useEffect, useState } from "react";
import { api, pollWhile } from "@/lib/api";
import type { RunState } from "@/lib/types";
import StatusBadge from "./StatusBadge";
import LayerMap from "./LayerMap";
import { VerdictCard, EvidenceList, WarningsPanel } from "./VerdictCard";

export default function RunView({ runId, bounds }: { runId: string; bounds: [[number, number], [number, number]] | null }) {
  const [state, setState] = useState<RunState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const stop = pollWhile(
      async () => {
        const r = await api.run(runId);
        const done = r.run.status === "ok" || r.run.status === "error";
        return { done, value: r.run };
      },
      (value) => setState(value),
    );
    return stop;
  }, [runId]);

  if (error) {
    return (
      <p role="alert" className="mono rounded border border-[#9e3b2c] bg-[#fbf3ef] px-3 py-2 text-xs text-[#9e3b2c]">
        {error}
      </p>
    );
  }
  if (!state) return <p className="text-sm text-[#8a837b]">Loading run…</p>;

  const progress = state.progress as { stage?: string; tile?: string };

  return (
    <div className="space-y-4">
      {(state.status === "queued" || state.status === "running") && (
        <section aria-live="polite" className="rounded-lg border border-[#b08d57]/40 bg-[#f7f2e7] p-4">
          <div className="flex items-center gap-3">
            <StatusBadge status={state.status} />
            <span className="mono text-xs text-[#7a6438]">
              {progress?.stage ?? "working"}
              {progress?.tile ? ` · tile ${progress.tile}` : ""}
            </span>
          </div>
          <p className="mt-2 text-xs text-[#7a6438]">
            Running the real engine path (routing → gates → pipeline → collect). The verdict
            appears here when the registry returns.
          </p>
        </section>
      )}

      {state.status === "error" && (
        <p role="alert" className="mono rounded border border-[#9e3b2c] bg-[#fbf3ef] px-3 py-2 text-xs text-[#9e3b2c]">
          {state.error ?? "run failed"}
        </p>
      )}

      {state.status === "ok" && state.results.length > 0 && (
        <>
          <VerdictCard result={state.results[0]} />
          <div className="grid gap-4 lg:grid-cols-2">
            <EvidenceList result={state.results[0]} />
            <div className="space-y-4">
              <WarningsPanel warnings={state.results[0].warnings} />
              <LayerMap runId={runId} bounds={bounds} />
            </div>
          </div>
          {state.results.length > 1 && (
            <section className="rounded-lg border border-[#e7e2da] bg-white p-4">
              <h3 className="text-sm font-semibold">Other pipelines on this run</h3>
              <ul className="mt-2 space-y-1 text-xs">
                {state.results.slice(1).map((r) => (
                  <li key={r.pipeline} className="flex items-center gap-2">
                    <StatusBadge status={r.status} />
                    <span className="mono text-[#8a837b]">{r.pipeline}</span>
                    <span className="text-[#8a837b]">{r.evidence[0]?.text ?? ""}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
