"use client";

/** PipelinesView (§6) — every pipeline from /api/pipelines (identity, lane, implemented flag;
 *  unimplemented ones are listed and labelled "not modelled yet", never faked). Clicking one
 *  opens the detail view: Input (manifest identity, routing decision + reasons, declared uses /
 *  own_fetch as present/missing chips, the bundle arrays it reads) · Process (ordered steps,
 *  assumptions, forward screens, the decision rule and why it fired, timing) · Output (the
 *  PipelineResult: status, tier + tier label, rank score with the not-a-probability note,
 *  evidence, flags). The list is public; the per-run detail is gated. */
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import { bg } from "@/lib/bg";
import { GateError } from "@/lib/bgApi";
import { useGateFail, useGateRetryNonce } from "./BackgroundGate";
import type { PipelineInfo, RunSummary } from "@/lib/types";
import { VerdictCard } from "@/components/VerdictCard";

export default function PipelinesView() {
  const [pipelines, setPipelines] = useState<PipelineInfo[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runId, setRunId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    api.pipelines().then((r) => setPipelines(r.pipelines)).catch((e) => setError(e instanceof Error ? e.message : String(e)));
    api.runs().then((r) => {
      setRuns(r.runs);
      const firstOk = r.runs.find((x) => x.status === "ok");
      if (firstOk) setRunId((cur) => cur || firstOk.id);
    }).catch(() => setRuns([]));
    return () => ctrl.abort();
  }, []);

  const implemented = pipelines.filter((p) => p.implemented);
  const notYet = pipelines.filter((p) => !p.implemented);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4 text-xs" aria-label="pipelines">
      <h1 className="text-sm font-semibold text-[#f2efe9]">Pipelines</h1>
      <p className="mt-1 max-w-3xl text-[11px] leading-relaxed text-[#8b949e]">
        Every hazard check the engine carries, from the engine&apos;s own catalogue. Pipelines
        marked <em>not modelled yet</em> are listed for honesty — there is no fake result behind
        them. Clicking a pipeline opens its input / process / output for a chosen run (gated).
      </p>
      {error && <p role="alert" className="mt-2 text-[11px] text-[#e8a087]">{error}</p>}

      <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {[...implemented, ...notYet].map((p) => (
          <li key={p.name}>
            <a
              href={`/background/pipelines/${p.name}`}
              className="block rounded-md border border-[#262c36] bg-[#12161d] p-2.5 hover:border-[#3fb6c9]"
            >
              <p className="flex items-center justify-between gap-2">
                <span className="font-mono text-[11px] text-[#7fd8e6]">{p.name}</span>
                {p.implemented
                  ? <span className="rounded border border-[#2c5f58] px-1.5 py-px text-[9px] uppercase text-[#8fd0b8]">implemented</span>
                  : <span className="rounded border border-[#333b47] px-1.5 py-px text-[9px] uppercase text-[#8b949e]">not modelled yet</span>}
              </p>
              <p className="mt-1 text-[10.5px] leading-relaxed text-[#8b949e]">{p.applies || "—"}</p>
              <p className="mt-1 flex flex-wrap gap-1 text-[9.5px] text-[#5a636e]">
                <span className="rounded border border-[#333b47] px-1">lane {p.execution ?? "normal"}</span>
                {p.needs_sar !== false && <span className="rounded border border-[#333b47] px-1">SAR</span>}
                {p.min_epochs != null && <span className="rounded border border-[#333b47] px-1">min epochs {p.min_epochs}</span>}
                <span className="rounded border border-[#333b47] px-1">{p.code}</span>
              </p>
            </a>
          </li>
        ))}
      </ul>

      <div className="mt-4 flex items-center gap-2 text-[11px] text-[#8b949e]">
        <ArrowRight size={12} aria-hidden />
        pick a run to see a pipeline&apos;s real input/process/output:
        <select
          value={runId}
          onChange={(e) => setRunId(e.target.value)}
          className="max-w-[240px] rounded border border-[#333b47] bg-[#0e1116] px-1.5 py-1 font-mono text-[11px] text-[#e6eaee]"
          aria-label="pick a run for the pipeline detail"
        >
          <option value="">— no run selected —</option>
          {runs.map((r) => <option key={r.id} value={r.id}>{r.id} · {r.status}</option>)}
        </select>
        {runId && <a className="text-[#7fd8e6] underline" href={`/background/pipelines/earthquake_coseismic?run=${runId}`}>open earthquake_coseismic for it</a>}
      </div>
    </div>
  );
}

/** The detail view (§6): input / process / output for one pipeline of one run. */
export function PipelineDetailView({ name, initialRun }: { name: string; initialRun?: string }) {
  const gateFail = useGateFail();
  const retryNonce = useGateRetryNonce();
  const router = useRouter();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runId, setRunId] = useState<string>(initialRun ?? "");
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof bg.pipelineDetail>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.runs().then((r) => {
      setRuns(r.runs);
      if (!initialRun) {
        const firstOk = r.runs.find((x) => x.status === "ok");
        if (firstOk) setRunId(firstOk.id);
      }
    }).catch(() => setRuns([]));
  }, [initialRun]);

  const load = useCallback((rid: string) => {
    if (!rid) return;
    setRunId(rid);
    setDetail(null);
    setError(null);
    setLoading(true);
    const ctrl = new AbortController();
    bg.pipelineDetail(rid, name, ctrl.signal)
      .then((d) => { setDetail(d); setLoading(false); })
      .catch((err) => {
        if (err instanceof GateError) { gateFail(err.kind); return; }
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
  }, [name, gateFail]);

  useEffect(() => {
    if (runId) load(runId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);
  useEffect(() => {
    if (retryNonce > 0 && runId) load(runId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retryNonce]);

  const r = detail?.result ?? null;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4 text-xs" aria-label={`pipeline ${name}`}>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => router.push("/background/pipelines")} className="text-[11px] text-[#8b949e] hover:text-[#e6eaee]">
          ← all pipelines
        </button>
        <h1 className="font-mono text-sm font-semibold text-[#7fd8e6]">{name}</h1>
        <label className="ml-auto flex items-center gap-1 text-[11px] text-[#8b949e]">
          run
          <select
            value={runId}
            onChange={(e) => setRunId(e.target.value)}
            className="max-w-[240px] rounded border border-[#333b47] bg-[#0e1116] px-1.5 py-1 font-mono text-[11px] text-[#e6eaee]"
            aria-label="pick a run"
          >
            <option value="">— pick a run —</option>
            {runs.map((x) => <option key={x.id} value={x.id}>{x.id} · {x.status}</option>)}
          </select>
        </label>
      </div>

      {loading && (
        <p className="mt-3 flex items-center gap-2 text-[11px] text-[#8b949e]" role="status" aria-live="polite">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-[#3fb6c9] border-t-transparent" aria-hidden />
          loading the pipeline detail…
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 border border-[#4a2f24] bg-[#2a1712] px-2 py-1.5 text-[11px] text-[#e8a087]">
          {error}
        </p>
      )}

      {detail && r && (
        <div className="mt-3 space-y-3">
          {/* INPUT */}
          <section className="rounded-md border border-[#262c36] bg-[#12161d] p-3">
            <h2 className="text-xs font-semibold text-[#f2efe9]">Input</h2>
            <dl className="mt-2 space-y-1 text-[10.5px]">
              <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">identity</dt><dd className="text-[#cfd6dc]">{detail.manifest.name} · code {detail.manifest.code} · {detail.manifest.sensor || "sensor —"} · {detail.manifest.applies || "—"}</dd></div>
              <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">lane</dt><dd className="text-[#cfd6dc]">{detail.manifest.execution}</dd></div>
              <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">min epochs</dt><dd className="text-[#cfd6dc]">{detail.manifest.min_epochs ?? "—"}</dd></div>
              <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">routing</dt><dd className="text-[#cfd6dc]">{detail.routing ? JSON.stringify(detail.routing) : "—"}</dd></div>
              <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">routing reasons</dt><dd className="text-[#cfd6dc]">{detail.feed?.reasons?.join(" · ") || "—"}</dd></div>
              <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">declared uses</dt><dd className="flex flex-wrap gap-1">
                {detail.feed?.uses.map((u) => (
                  <span key={u.code} title={u.why} className={`rounded border px-1 py-px ${u.state === "present" ? "border-[#2c5f58] text-[#8fd0b8]" : u.state === "no_producer" ? "border-[#333b47] text-[#8b949e]" : "border-[#4a3a1f] text-[#e6c977]"}`}>
                    {u.code} {u.state}
                  </span>
                )) ?? "—"}
              </dd></div>
              <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">own fetch</dt><dd className="flex flex-wrap gap-1">
                {detail.feed?.own_fetch.map((u) => (
                  <span key={u.code} title={u.why} className={`rounded border px-1 py-px ${u.state === "present" ? "border-[#2c5f58] text-[#8fd0b8]" : u.state === "no_producer" ? "border-[#333b47] text-[#8b949e]" : "border-[#4a3a1f] text-[#e6c977]"}`}>
                    {u.code} {u.state}
                  </span>
                )) ?? "—"}
              </dd></div>
              {detail.bundle_error && <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">bundle</dt><dd className="text-[#e8a087]">{detail.bundle_error}</dd></div>}
            </dl>
          </section>

          {/* PROCESS */}
          <section className="rounded-md border border-[#262c36] bg-[#12161d] p-3">
            <h2 className="text-xs font-semibold text-[#f2efe9]">Process</h2>
            <dl className="mt-2 space-y-1 text-[10.5px]">
              <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">method</dt><dd className="text-[#cfd6dc]">{detail.manifest.method.join(" · ") || "—"}</dd></div>
              <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">own prep</dt><dd className="text-[#cfd6dc]">{detail.manifest.own_prep.join(" · ") || "—"}</dd></div>
              <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">assumptions</dt><dd className="text-[#cfd6dc]">{detail.manifest.assumptions.join(" · ") || "—"}</dd></div>
              <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">timing</dt><dd className="font-mono text-[#cfd6dc]">{r.runtime_s != null ? `${r.runtime_s.toFixed(2)} s` : "—"}</dd></div>
              <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">engine numbers</dt><dd className="break-all text-[#cfd6dc]">{detail.process.diagnostics ? JSON.stringify(detail.process.diagnostics) : "—"}</dd></div>
              <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">decision rule</dt><dd className="text-[#cfd6dc]">{r.summary || "—"} <span className="text-[#8b949e]">— fired because the engine&apos;s own screen/threshold logic said so; the evidence below carries the engine&apos;s words</span></dd></div>
              {r.warnings.length > 0 && <div className="flex gap-2"><dt className="w-28 shrink-0 text-[#8b949e]">warnings</dt><dd className="text-[#e6c977]">{r.warnings.join(" · ")}</dd></div>}
            </dl>
          </section>

          {/* OUTPUT */}
          <section className="rounded-md border border-[#262c36] bg-[#12161d] p-3">
            <h2 className="text-xs font-semibold text-[#f2efe9]">Output</h2>
            <p className="mt-1 text-[10px] text-[#8b949e]">
              tier and tier label come from the engine (the UI never re-derives them); the rank
              score is a within-tier rank, NOT a probability.
            </p>
            <div className="mt-2 rounded-md">
              <VerdictCard result={r} />
            </div>
          </section>
        </div>
      )}

      {!detail && !loading && !error && !runId && (
        <p className="mt-3 text-[11px] text-[#8b949e]" role="status">pick a run to load this pipeline&apos;s detail.</p>
      )}
    </div>
  );
}
