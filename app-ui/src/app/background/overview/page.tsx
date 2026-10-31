"use client";

/** /background/overview — the end-to-end chain, read live from GET /api/background/overview.
 *  Nodes, sentences, technical lines and step names/order/needs all come from the ENGINE
 *  (config/explain.yaml + engine/common/chain.STEPS): nothing about the chain is retyped here. */
import Link from "next/link";
import { useEffect, useState } from "react";
import { bg } from "@/lib/bg";
import { GateError } from "@/lib/bgApi";
import { dash } from "@/lib/explainCache";
import type { OverviewPayload } from "@/lib/types";
import { useGateFail, useGateRetryNonce } from "@/components/background/BackgroundGate";

const PAGES: Record<string, string> = {
  retrieval: "/background/retrieval",
  preprocessing: "/background/preprocessing",
  pipelines: "/background/pipelines",
};

/** Only the pages that actually read ?run= get it; the retrieval page is keyed by box/dates/tile, not an id. */
function hrefFor(page: string | null, run: { retrieval_id: string | null; run_id: string | null }): string | null {
  if (!page || !PAGES[page]) return null;
  const base = PAGES[page];
  if (page === "retrieval" || !run.run_id) return base;
  return `${base}?run=${encodeURIComponent(run.run_id)}`;
}

export default function OverviewPage() {
  const gateFail = useGateFail();
  const retryNonce = useGateRetryNonce();
  const [data, setData] = useState<OverviewPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    setError(null);
    bg.overview(ctrl.signal)
      .then((d) => !ctrl.signal.aborted && setData(d))
      .catch((err) => {
        if (ctrl.signal.aborted) return;
        if (err instanceof GateError) { gateFail(err.kind); return; }
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => ctrl.abort();
  }, [gateFail, retryNonce]);

  return (
    <div className="min-h-0 flex-1 overflow-auto p-4 text-xs" aria-label="overview">
      <h1 className="text-sm font-semibold text-[#f2efe9]">Overview</h1>
      <p className="mt-1 max-w-3xl text-[#8b949e]">
        How a box and a date range become a result, left to right. Each box is read live from the engine.
      </p>
      {error && <p role="alert" className="mt-3 text-[#e8a087]">{error}</p>}
      {!data && !error && <p role="status" className="mt-3 text-[#8b949e]">loading…</p>}
      {data && (
        <>
          <p className="mt-3 text-[11px] text-[#cfd6dc]" data-testid="overview-counts">
            declared <b>{dash(data.counts.declared)}</b> · modelled <b>{dash(data.counts.modelled)}</b>
            <span className="ml-2 text-[#5a636e]">{data.featured_note}</span>
          </p>
          <ol className="mt-3 flex flex-wrap items-stretch gap-2" data-testid="overview-nodes">
            {data.nodes.map((n, i) => {
              const href = hrefFor(n.page, n.run);
              return (
                <li key={n.key} className="flex items-stretch gap-2">
                  <div className="flex w-56 flex-col gap-1 rounded border border-[#262c36] bg-[#12161c] p-2.5">
                    <h2 className="text-[11px] font-semibold text-[#f2efe9]">{dash(n.title)}</h2>
                    <p className="text-[#cfd6dc]">{dash(n.plain)}</p>
                    <p className="font-mono text-[10px] text-[#8b949e]">{dash(n.tech)}</p>
                    <div className="flex flex-wrap gap-1">
                      {n.chips.map((c) => (
                        <span key={c.label} className="rounded border border-[#333b47] px-1.5 py-0.5 font-mono text-[10px] text-[#aab3bd]">
                          {c.label} {dash(c.value)}
                        </span>
                      ))}
                    </div>
                    {href && (
                      <Link href={href} className="mt-auto text-[10px] text-[#7fd8e6] underline-offset-2 hover:underline">
                        open →
                      </Link>
                    )}
                  </div>
                  {i < data.nodes.length - 1 && <span aria-hidden className="self-center text-[#5a636e]">→</span>}
                </li>
              );
            })}
          </ol>
          <h2 className="mt-5 text-[11px] font-semibold text-[#f2efe9]">Preprocessing steps (from the engine)</h2>
          <ol className="mt-2 grid max-w-4xl gap-1" data-testid="overview-steps">
            {data.steps.map((s) => (
              <li key={s.code} className="flex gap-2 border-b border-[#1c222b] py-1">
                <span className="w-9 shrink-0 font-mono text-[#e6c977]">{s.code}</span>
                <span className="w-44 shrink-0 font-mono text-[#8b949e]">{s.name}</span>
                <span className="text-[#cfd6dc]">{dash(s.plain)}</span>
                <span className="ml-auto shrink-0 font-mono text-[10px] text-[#5a636e]">
                  needs {s.needs.length ? s.needs.join(" | ") : "—"}
                </span>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
